"""Preview and apply a confirmed protocol mapping to one selected subject visit."""
import hashlib
import json

from django.utils.translation import gettext as _

from apps.audit.public import AuditContextAdapter
from apps.core.form_data_document import is_canonical_form_data, set_field_value
from apps.crf.public import CrfContextAdapter
from apps.datacapture.application.commands.save_submit_page import SavePageCommand
from apps.datacapture.application.services.file_import_values import choice_options, convert_value, equivalent
from apps.datacapture.application.services.save_submit_page import DataCaptureSaveSubmitPageService
from apps.datacapture.domain.status import DataCapturePageState
from apps.datacapture.infrastructure.file_import.runtime import (
    MAX_BYTES,
    FileImportCaptureRepository,
    digest,
    match_rows,
    read_preview,
    read_workbook,
    sign_preview,
)
from apps.study.public import StudyEventFormBindingReader, StudyFileImportReader
from apps.subject.public import get_event_instance_snapshot, get_subject_capture_eligibility


class StaleImportPreviewError(ValueError):
    pass


class MappedFileImportService:
    def __init__(self, reader=None, crf=None, capture=None, saver=None, audit=None):
        self.reader = reader or StudyFileImportReader()
        self.crf = crf or CrfContextAdapter()
        self.capture = capture or FileImportCaptureRepository()
        self.saver = saver or DataCaptureSaveSubmitPageService()
        self.audit = audit or AuditContextAdapter()

    @staticmethod
    def _context(study_id, subject_id, visit_id):
        event = get_event_instance_snapshot(event_instance_id=visit_id)
        if not event or event["study_id"] != study_id or event["subject_id"] != subject_id:
            raise ValueError(_("The visit does not belong to the selected participant and study."))
        if event.get("status") in {"locked", "finalized", "skipped", "cancelled", "verified"}:
            raise ValueError(_("This visit is locked, closed, or unavailable for data entry. Reopen it through the existing workflow."))
        eligibility = get_subject_capture_eligibility(subject_id=subject_id, event_instance_id=visit_id)
        if not eligibility.allowed:
            raise ValueError(_("Data entry is not allowed for this visit (%(reason)s).") % {"reason": eligibility.reason})
        return event

    def _targets(self, config, event):
        catalog = self.reader.catalog(event["study_id"])
        allowed = {(binding["id"], field["id"]): (binding, field)
                   for binding in catalog["bindings"] for field in binding["fields"]}
        targets = []
        for mapping in config["confirmation"]["mappings"]:
            for target in mapping["targets"]:
                if target["visit_id"] != event["event_definition_id"] or str(target["study_version"]) != str(event["study_version"]):
                    continue
                pair = allowed.get((target["binding_id"], target["field_id"]))
                if (not pair or pair[0]["form_id"] != target["form_id"] or pair[1]["key"] != target["field_key"]
                        or pair[0]["visit_id"] != event["event_definition_id"]
                        or str(pair[0]["study_version"]) != str(event["study_version"])):
                    raise ValueError(_("The mapping is outdated because the CRF configuration changed. Configure the mapping again."))
                binding = StudyEventFormBindingReader().get_binding_snapshot(binding_id=target["binding_id"])
                if binding and binding["is_repeatable_within_event"]:
                    raise ValueError(_("A repeating form requires a specific record. Enter data in the target record instead of importing at visit level."))
                targets.append(dict(target, column=mapping["column"]))
        return targets

    def preview(self, *, study_id, subject_id, visit_id, actor_user_id, filename, content,
                mapping_id=None, row_number=None, value_overrides=None):
        if not filename.lower().endswith(".xlsx"):
            raise ValueError(_("Choose an .xlsx file."))
        event = self._context(study_id, subject_id, visit_id)
        workbook = read_workbook(content)
        candidates = []
        for config in self.reader.configurations(study_id):
            rows = match_rows(workbook, config)
            if rows is not None and any(t["visit_id"] == event["event_definition_id"] and str(t["study_version"]) == str(event["study_version"])
                                        for m in config["confirmation"]["mappings"] for t in m["targets"]):
                candidates.append((config, rows))
        if not candidates:
            raise ValueError(_("No confirmed mapping matches the file structure and visit. Configure one in Study → File Importer."))
        base = {"status": "selection_required", "mappings": [{"id": c["id"], "filename": c["filename"]} for c, _ in candidates],
                "rows": [], "items": [], "errors": [], "can_apply": False, "overwrite_count": 0}
        if not mapping_id and len(candidates) > 1:
            return base
        chosen = next(((c, rows) for c, rows in candidates if c["id"] == mapping_id), None) if mapping_id else candidates[0]
        if chosen is None:
            raise ValueError(_("The selected mapping does not match the file and visit."))
        config, rows = chosen
        base["selected_mapping_id"] = config["id"]
        base["rows"] = [{"row_number": row["row_number"], "values": [cell["value"] for cell in row["cells"].values()][:12]} for row in rows]
        if not rows:
            raise ValueError(_("The file contains no result rows."))
        if row_number is None and len(rows) > 1:
            return base
        row = next((r for r in rows if r["row_number"] == row_number), None) if row_number is not None else rows[0]
        if row is None:
            raise ValueError(_("The selected result row is invalid."))
        targets = self._targets(config, event)
        overrides = value_overrides or {}
        if not isinstance(overrides, dict):
            raise ValueError(_("The list of confirmed values is invalid."))
        allowed_keys = {f'{t["binding_id"]}:{t["field_id"]}' for t in targets}
        if set(overrides) - allowed_keys:
            raise ValueError(_("The confirmed value does not belong to the mapping for this visit."))
        forms, schemas, items, errors = {}, {}, [], []
        headers = {c["column"]: c["header"] for c in config["preview"]["columns"]}
        for target in targets:
            form_id, binding_id = target["form_id"], target["binding_id"]
            form_key = str(binding_id)
            if form_key not in forms:
                snapshot = self.capture.snapshot(subject_id, visit_id, form_id, binding_id)
                forms[form_key] = {"form_id": form_id, "binding_id": binding_id, "snapshot": snapshot, "updates": {}}
                schemas[form_key] = {str(f["id"]): f for f in self.crf.list_template_fields_with_ui_config(template_id=form_id)}
            field = schemas[form_key].get(str(target["field_id"]))
            if not field:
                raise ValueError(_("The field was removed from the CRF. Configure the mapping again."))
            cell = row["cells"].get(target["column"], {"value": None, "formula": False})
            value = cell["value"]
            old = forms[form_key]["snapshot"]["data"].get(target["field_key"])
            key = f'{binding_id}:{target["field_id"]}'
            item = {"key": key, "column": target["column"], "header": headers[target["column"]],
                    "form_id": form_id, "form_code": target["form_code"], "field_key": target["field_key"],
                    "field_label": field["label"], "old_value": old, "new_value": value, "source_value": value, "raw_value": value, "unit": field.get("unit") or "",
                    "status": "empty", "options": choice_options(field)}
            if value is not None and str(value).strip():
                try:
                    if cell.get("excel_error"):
                        raise ValueError(_("The Excel cell contains an error. Correct it before importing."))
                    if cell["formula"]:
                        raise ValueError(_("Formula cells cannot be imported. Use their result values instead."))
                    if (field.get("section_template") or {}).get("is_repeatable"):
                        raise ValueError(_("Select a repeating-group record before importing this field."))
                    new = convert_value(value, field, overrides.get(key))
                    item["new_value"] = new
                    item["status"] = "unchanged" if equivalent(old, new, field) else ("overwrite" if old is not None and str(old).strip() else "new")
                    if item["status"] in {"new", "overwrite"}:
                        forms[form_key]["updates"][target["field_key"]] = new
                except ValueError as exc:
                    item.update(status="error", error=str(exc))
            items.append(item)
        for form in forms.values():
            if form["updates"]:
                if DataCapturePageState.is_capture_locked(form["snapshot"]["page_status"]):
                    errors.append(_("Form %(form_id)s is locked, verified, or certified. Reopen it through the existing workflow.") % {"form_id": form["form_id"]})
                self.saver._assert_capture_not_locked(subject_id=subject_id, visit_id=visit_id, crf_template_id=form["form_id"])
        mapped_columns = {t["column"] for t in targets}
        for mapping in config["confirmation"]["mappings"]:
            if mapping["column"] not in mapped_columns:
                raw = row["cells"].get(mapping["column"], {}).get("value")
                items.append({"key": "skip:" + mapping["column"], "column": mapping["column"], "header": headers[mapping["column"]],
                              "field_label": "", "field_key": "", "form_code": "", "raw_value": raw, "new_value": raw, "old_value": None,
                              "status": "skipped", "error": mapping.get("skip_reason") or _("This column does not apply to this visit."), "options": []})
        overwrite_count = sum(item["status"] == "overwrite" for item in items)
        can_apply = any(form["updates"] for form in forms.values()) and not errors and not any(i["status"] == "error" for i in items)
        payload = {"study_id": study_id, "subject_id": subject_id, "visit_id": visit_id, "actor_user_id": actor_user_id,
                   "mapping_id": config["id"], "mapping_digest": digest(config), "schema_digest": digest(schemas),
                   "source_sha256": hashlib.sha256(content).hexdigest(), "filename": filename[:255], "row_number": row["row_number"],
                   "forms": forms, "overrides": overrides, "overwrite_count": overwrite_count,
                   "changes": [i for i in items if i["status"] in {"new", "overwrite"}]}
        return dict(base, status="preview", selected_row_number=row["row_number"], items=items, errors=errors,
                    overwrite_count=overwrite_count, can_apply=can_apply, token=sign_preview(payload) if can_apply else None,
                    creates_correction=any(f["updates"] and f["snapshot"]["entry_status"] == "submitted" for f in forms.values()))

    def apply(self, *, study_id, subject_id, visit_id, actor_user_id, token, confirmed, confirm_overwrite=False, reason=""):
        if confirmed is not True:
            raise ValueError(_("Confirm the preview before importing."))
        payload = read_preview(token)
        for key, value in dict(study_id=study_id, subject_id=subject_id, visit_id=visit_id, actor_user_id=actor_user_id).items():
            if payload.get(key) != value:
                raise ValueError(_("The preview does not belong to this user, participant, or visit."))
        if payload["overwrite_count"] and confirm_overwrite is not True:
            raise ValueError(_("Compare the values and confirm overwriting the existing eCRF data."))
        with self.capture.atomic_scope(subject_id, visit_id):
            event = self._context(study_id, subject_id, visit_id)
            config = self.reader.configuration(study_id, payload["mapping_id"])
            self._targets(config, event)
            if digest(config) != payload["mapping_digest"]:
                raise StaleImportPreviewError(_("The mapping changed. Generate a new preview."))
            schemas = {}
            for key, form in payload["forms"].items():
                schemas[key] = {str(f["id"]): f for f in self.crf.list_template_fields_with_ui_config(template_id=form["form_id"])}
                if self.capture.snapshot(subject_id, visit_id, form["form_id"], form["binding_id"]) != form["snapshot"]:
                    raise StaleImportPreviewError(_("The eCRF data changed after the preview. Compare the values again."))
            if digest(schemas) != payload["schema_digest"]:
                raise StaleImportPreviewError(_("The CRF structure changed. Generate a new preview."))
            results = []
            for form in payload["forms"].values():
                if not form["updates"]:
                    continue
                data = form["snapshot"]["raw_data"]
                if is_canonical_form_data(data):
                    fields = {f["field_key"]: f for f in schemas[str(form["binding_id"])].values()}
                    for field_key, value in form["updates"].items():
                        section = (fields[field_key].get("section_template") or {}).get("code") or ""
                        data = set_field_value(data, section_code=section, field_key=field_key, value=value)
                else:
                    data = dict(data, **form["updates"])
                result = self.saver.save(SavePageCommand(subject_id=subject_id, visit_id=visit_id,
                    crf_template_id=form["form_id"], event_form_binding_id=form["binding_id"], actor_user_id=actor_user_id,
                    data=json.dumps(data, ensure_ascii=False)))
                results.append({"form_id": form["form_id"], "entry_id": result.entry_id})
            self.audit.record_event(action="file_import", object_type="subject_event", object_id=str(visit_id), actor_user_id=actor_user_id,
                before_data={"fields": [{"field_key": i["field_key"], "form_id": i["form_id"], "value": i["old_value"]} for i in payload["changes"]]},
                after_data={"mapping_id": payload["mapping_id"], "source_sha256": payload["source_sha256"], "filename": payload["filename"],
                            "row_number": payload["row_number"], "overrides": payload["overrides"], "changes": payload["changes"], "reason": str(reason)[:2000]})
        return {"forms": results, "applied_fields": len(payload["changes"]), "message": _("The data was imported into an eCRF draft. It has not been submitted or certified.")}


MAX_FILE_BYTES = MAX_BYTES

def inspect_import_preview(token):
    return read_preview(token)
