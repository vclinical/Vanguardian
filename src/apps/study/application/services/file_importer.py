from apps.crf.public import CrfContextAdapter
from apps.study.infrastructure.file_importer import MAX_FILE_BYTES, read_import_workbook
from apps.study.infrastructure.repositories.file_importer import StudyFileImporterRepository


class StudyFileImporterService:
    def __init__(self, repository=None, crf_adapter=None):
        self.repository = repository or StudyFileImporterRepository()
        self.crf_adapter = crf_adapter or CrfContextAdapter()

    def catalog(self, study_id):
        bindings = self.repository.list_bindings(study_id)
        schemas = {}
        result = []
        for binding in bindings:
            template_id = binding["form_definition_id"]
            if template_id not in schemas:
                schemas[template_id] = self.crf_adapter.list_template_fields_with_ui_config(template_id=template_id)
            fields = [{"id": int(field["id"]), "key": field["field_key"], "label": field["label"],
                       "labels": field.get("labels", [field["label"]]),
                       "type": field["data_type"], "unit": field.get("unit") or ""}
                      for field in schemas[template_id] if str((field.get("ui_config") or {}).get("control_type") or "").upper() != "LABEL_ONLY"]
            result.append({"id": binding["id"], "visit_id": binding["event_definition_id"],
                           "visit_code": binding["event_definition__code"], "visit_name": binding["event_definition__name"],
                           "form_id": template_id, "form_code": binding["form_definition__code"],
                           "study_version": binding["study_version"], "fields": fields})
        return {"bindings": result, "events": self.repository.list_events(study_id)}

    def upload(self, *, study_id, filename, content, header_row, sheet_name, actor_user_id):
        if not filename.lower().endswith(".xlsx") or len(content) > MAX_FILE_BYTES:
            raise ValueError("Chọn file .xlsx không quá 5 MB.")
        preview = read_import_workbook(content, header_row=header_row, sheet_name=sheet_name)
        return self.repository.create_upload(study_id=study_id, filename=filename, content=content,
                                             preview=preview, actor_user_id=actor_user_id)

    def confirm(self, *, study_id, upload_id, mappings, actor_user_id, expected_confirmed_at=None):
        upload = self.repository.get_upload(study_id, upload_id)
        if expected_confirmed_at is not None and (not isinstance(expected_confirmed_at, str) or not expected_confirmed_at):
            raise ValueError("Phiên bản mapping không hợp lệ. Mở lại mapping trước khi chỉnh sửa.")
        if upload["confirmation"] is not None and expected_confirmed_at is None:
            raise ValueError("Mapping đã được xác nhận; mở lại mapping và chọn Chỉnh sửa mapping.")
        if expected_confirmed_at is not None and (
            upload["confirmation"] is None or upload["confirmation"]["confirmed_at"] != expected_confirmed_at
        ):
            raise ValueError("Mapping đã thay đổi. Mở lại mapping mới nhất trước khi chỉnh sửa.")
        columns = {column["column"] for column in upload["preview"]["columns"]}
        catalog = self.catalog(study_id)
        allowed = {(binding["id"], field["id"]): (binding, field)
                   for binding in catalog["bindings"] for field in binding["fields"]}
        if not isinstance(mappings, list) or len(mappings) != len(columns):
            raise ValueError("Cần xác nhận mapping hoặc bỏ qua cho từng cột trong file.")
        seen_columns, seen_targets = set(), set()
        cleaned = []
        for mapping in mappings:
            if not isinstance(mapping, dict) or not isinstance(mapping.get("column"), str) or mapping["column"] not in columns:
                raise ValueError("Cột nguồn không hợp lệ.")
            column = mapping["column"]
            if column in seen_columns:
                raise ValueError(f"Cột {column} bị trùng.")
            seen_columns.add(column)
            targets = mapping.get("targets")
            if not isinstance(targets, list) or len(targets) > 500:
                raise ValueError(f"Danh sách field của cột {column} không hợp lệ.")
            reason = str(mapping.get("skip_reason") or "").strip()
            if not targets and not reason:
                raise ValueError(f"Cột {column}: chọn field hoặc ghi lý do bỏ qua.")
            normalized_targets = []
            for target in targets:
                if not isinstance(target, dict):
                    raise ValueError("Field đích không hợp lệ.")
                binding_id, field_id = target.get("binding_id"), target.get("field_id")
                if type(binding_id) is not int or type(field_id) is not int:
                    raise ValueError("Mã binding/field phải là số nguyên.")
                key = (binding_id, field_id)
                if key not in allowed:
                    raise ValueError("Field/Visit không thuộc study này, đã ngừng dùng hoặc chưa được gắn biểu mẫu.")
                if key in seen_targets:
                    raise ValueError("Một field trong cùng biểu mẫu/Visit không được nhận từ hai cột hoặc được chọn trùng.")
                seen_targets.add(key)
                binding, field = allowed[key]
                normalized_targets.append({"binding_id": binding_id, "field_id": field_id,
                                           "visit_id": binding["visit_id"], "visit_code": binding["visit_code"],
                                           "form_id": binding["form_id"], "form_code": binding["form_code"],
                                           "study_version": binding["study_version"], "field_key": field["key"],
                                           "field_label": field["label"], "data_type": field["type"], "unit": field["unit"]})
            cleaned.append({"column": column, "targets": normalized_targets, "skip_reason": reason[:500] if not targets else ""})
        if not seen_targets:
            raise ValueError("Cần chọn ít nhất một field đích.")
        return self.repository.confirm(study_id=study_id, upload_id=upload_id,
                                       mappings=cleaned, actor_user_id=actor_user_id, expected_confirmed_at=expected_confirmed_at)
