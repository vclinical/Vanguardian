import copy
import json
from contextlib import contextmanager
from datetime import date, time
from io import BytesIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, override_settings
from openpyxl import Workbook

from apps.datacapture.application.services.file_import_values import convert_value, equivalent
from apps.datacapture.application.services.mapped_file_import import MappedFileImportService, StaleImportPreviewError
from apps.datacapture.infrastructure.file_import.runtime import match_rows, read_preview, read_workbook
from apps.study.infrastructure.repositories.file_importer import StudyFileImporterRepository


def workbook(value="5", second=None, headers=("Result", "Unused")):
    book = Workbook()
    sheet = book.active
    sheet.title = "Results"
    sheet.append(list(headers))
    sheet.append([value, "other"])
    if second is not None:
        sheet.append([second, "other"])
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


@override_settings(LANGUAGE_CODE="en")
class MappedImportTests(SimpleTestCase):
    def setUp(self):
        self.scope = dict(study_id=3, subject_id=10, visit_id=20, actor_user_id=7)
        self.event = dict(study_id=3, subject_id=10, event_definition_id=30, study_version="1")
        self.field = dict(id="4", field_key="RESULT", label="Result", data_type="DECIMAL", ui_config={}, section_template=None)
        target = dict(visit_id=30, study_version="1", binding_id=2, field_id=4, form_id=8, field_key="RESULT", form_code="LAB")
        self.config = {"id": "mapping-one", "filename": "config.xlsx", "preview": {"sheet": "Results", "header_row": 1,
            "columns": [{"column": "A", "header": "Result"}, {"column": "B", "header": "Unused"}]},
            "confirmation": {"mappings": [{"column": "A", "targets": [target]}, {"column": "B", "targets": [], "skip_reason": "not needed"}]}}
        self.reader = Mock()
        self.reader.configurations.return_value = [self.config]
        self.reader.configuration.return_value = self.config
        self.reader.catalog.return_value = {"bindings": [{"id": 2, "form_id": 8, "visit_id": 30, "study_version": "1", "fields": [{"id": 4, "key": "RESULT"}]}]}
        self.crf = Mock()
        self.crf.list_template_fields_with_ui_config.side_effect = lambda **kwargs: [self.field]
        self.snapshot = {"page_id": None, "page_status": None, "entry_id": None, "entry_status": None, "updated_at": None,
                         "data": {"UNRELATED": "keep"}, "raw_data": {"UNRELATED": "keep"}, "version": None}
        self.capture = Mock()
        self.capture.snapshot.side_effect = lambda *args: copy.deepcopy(self.snapshot)
        self.capture.atomic_scope = self.atomic
        self.saver, self.audit = Mock(), Mock()
        self.saver.save.return_value = SimpleNamespace(entry_id=99)
        self.validation = Mock()
        self.service = MappedFileImportService(reader=self.reader, crf=self.crf, capture=self.capture, saver=self.saver, audit=self.audit, validation=self.validation)
        context = patch.object(self.service, "_context", return_value=self.event)
        context.start()
        self.addCleanup(context.stop)
        binding = patch("apps.datacapture.application.services.mapped_file_import.StudyEventFormBindingReader")
        self.binding = binding.start()
        self.binding.return_value.get_binding_snapshot.return_value = {"is_repeatable_within_event": False}
        self.addCleanup(binding.stop)

    @contextmanager
    def atomic(self, *args):
        yield

    def preview(self, **kwargs):
        return self.service.preview(**self.scope, filename="file.xlsx", content=kwargs.pop("content", workbook()), **kwargs)

    def apply(self, result, **kwargs):
        return self.service.apply(**self.scope, token=result["token"], confirmed=True, **kwargs)

    def test_preview_no_writes_apply_preserves_unmapped_data_and_audits(self):
        result = self.preview()
        self.assertTrue(result["can_apply"])
        self.saver.save.assert_not_called()
        self.audit.record_event.assert_not_called()
        self.assertEqual(result["items"][1]["status"], "skipped")
        self.assertEqual(self.apply(result)["applied_fields"], 1)
        data = json.loads(self.saver.save.call_args.args[0].data)
        self.assertEqual(data, {"RESULT": "5", "UNRELATED": "keep"})
        self.assertEqual(self.audit.record_event.call_args.kwargs["after_data"]["row_number"], 2)

    def test_blank_does_not_clear_existing_zero_is_not_blank(self):
        self.snapshot["data"]["RESULT"] = "8"
        result = self.preview(content=workbook(None))
        self.assertFalse(result["can_apply"])
        self.assertEqual(result["items"][0]["status"], "empty")
        result = self.preview(content=workbook(0))
        self.assertEqual(result["items"][0]["new_value"], "0")

    def test_overwrite_requires_confirmation_and_compares_values(self):
        self.snapshot["data"]["RESULT"] = "8"
        result = self.preview()
        self.assertEqual(result["overwrite_count"], 1)
        with self.assertRaisesMessage(ValueError, "every field requiring adjustment"):
            self.apply(result)
        self.saver.save.assert_not_called()
        self.apply(result, field_decisions={"2:4": "file"})

    def test_numeric_equivalence_is_noop(self):
        self.snapshot["data"]["RESULT"] = "5.0"
        result = self.preview()
        self.assertFalse(result["can_apply"])
        self.assertEqual(result["items"][0]["status"], "unchanged")

    def test_stale_data_blocks_before_any_write(self):
        result = self.preview()
        self.snapshot["data"]["UNRELATED"] = "changed concurrently"
        with self.assertRaises(StaleImportPreviewError):
            self.apply(result)
        self.saver.save.assert_not_called()

    def test_mapping_or_schema_change_blocks_apply(self):
        result = self.preview()
        self.field["unit"] = "changed"
        with self.assertRaises(StaleImportPreviewError):
            self.apply(result)
        self.saver.save.assert_not_called()

    def test_editing_confirmed_mapping_invalidates_existing_preview(self):
        result = self.preview()
        self.config["confirmation"]["confirmed_at"] = "new-confirmation-version"
        with self.assertRaises(StaleImportPreviewError):
            self.apply(result)
        self.saver.save.assert_not_called()

    def test_token_bound_to_actor_and_tamper_rejected(self):
        result = self.preview()
        with self.assertRaises(ValueError):
            self.service.apply(**dict(self.scope, actor_user_id=8), token=result["token"], confirmed=True)
        with self.assertRaises(ValueError):
            read_preview(result["token"] + "tampered")

    def test_multiple_mapping_and_rows_need_explicit_selection(self):
        other = dict(self.config, id="mapping-two")
        self.reader.configurations.return_value = [self.config, other]
        self.assertEqual(self.preview()["status"], "selection_required")
        result = self.preview(mapping_id="mapping-two", content=workbook(second="7"))
        self.assertEqual(result["status"], "selection_required")
        result = self.preview(mapping_id="mapping-two", row_number=3, content=workbook(second="7"))
        self.assertEqual(result["items"][0]["new_value"], "7")

    def test_formula_and_invalid_numeric_block(self):
        for value in ("=1+1", "wrong", "NaN", "Infinity"):
            with self.subTest(value=value):
                result = self.preview(content=workbook(value))
                self.assertFalse(result["can_apply"])
                self.assertEqual(result["items"][0]["status"], "error")

    def test_choice_resolution_is_explicit_per_field(self):
        self.field["ui_config"] = {"options": {"source": "static", "static": [{"label": "Âm tính", "value": "0"}, {"label": "Dương tính", "value": "1"}]}}
        result = self.preview(content=workbook("0.21 (Unknown)"))
        self.assertFalse(result["can_apply"])
        result = self.preview(content=workbook("0.21 (Unknown)"), value_overrides={"2:4": "0"})
        self.assertTrue(result["can_apply"])
        self.assertEqual(result["items"][0]["raw_value"], "0.21 (Unknown)")
        self.assertEqual(result["items"][0]["new_value"], "0")
        self.assertFalse(self.preview(value_overrides={"2:4": "99"})["can_apply"])

    def test_locked_pages_and_repeating_sections_block(self):
        for status in ("verified", "certified", "locked", "finalized"):
            self.snapshot["page_status"] = status
            self.assertFalse(self.preview()["can_apply"])
        self.snapshot["page_status"] = None
        self.field["section_template"] = {"is_repeatable": True}
        self.assertFalse(self.preview()["can_apply"])

    def test_repeatable_form_does_not_default_to_first_instance(self):
        self.binding.return_value.get_binding_snapshot.return_value = {"is_repeatable_within_event": True}
        with self.assertRaisesMessage(ValueError, "A repeating form"):
            self.preview()

    def test_header_reorder_does_not_match(self):
        with self.assertRaisesMessage(ValueError, "No confirmed mapping"):
            self.preview(content=workbook(headers=("Unused", "Result")))

    def test_expired_preview_rejected(self):
        result = self.preview()
        with patch("django.core.signing.time.time", return_value=9999999999), self.assertRaisesMessage(ValueError, "expired"):
            read_preview(result["token"])

    def test_disabled_binding_or_wrong_version_cannot_apply(self):
        self.reader.catalog.return_value = {"bindings": []}
        with self.assertRaisesMessage(ValueError, "outdated"):
            self.preview()
        self.config["confirmation"]["mappings"][0]["targets"][0]["study_version"] = "other"
        with self.assertRaisesMessage(ValueError, "No confirmed mapping"):
            self.preview()

    def test_reassigned_binding_cannot_use_old_mapping(self):
        self.reader.catalog.return_value["bindings"][0]["visit_id"] = 999
        with self.assertRaisesMessage(ValueError, "outdated"):
            self.preview()

    def test_governance_lock_blocks_preview(self):
        self.saver._assert_capture_not_locked.side_effect = PermissionDenied("Locked")
        with self.assertRaises(PermissionDenied):
            self.preview()
        self.saver.save.assert_not_called()

    def test_visit_becoming_locked_after_preview_blocks_before_save(self):
        result = self.preview()
        self.service._context.side_effect = ValueError("This visit is locked")
        with self.assertRaisesMessage(ValueError, "This visit is locked"):
            self.apply(result)
        self.saver.save.assert_not_called()

    def test_submitted_values_warn_about_correction_before_apply(self):
        self.snapshot["entry_status"] = "submitted"
        self.assertTrue(self.preview()["creates_correction"])
        self.saver.save.assert_not_called()

    def test_preserves_canonical_unrelated_repeat_row_keys(self):
        raw = {"schema_version": 1, "groups": {"": {"kind": "single", "items": {"RESULT": "1"}},
               "HISTORY": {"kind": "repeatable", "rows": [{"row_key": "original", "row_no": 1, "items": {"NOTE": "keep"}}]}}}
        self.snapshot["raw_data"] = raw
        result = self.preview()
        self.apply(result)
        data = json.loads(self.saver.save.call_args.args[0].data)
        self.assertEqual(data["groups"]["HISTORY"], raw["groups"]["HISTORY"])


@override_settings(LANGUAGE_CODE="en")
class FileImportValueTests(SimpleTestCase):
    def test_strict_date_and_numeric_equivalence(self):
        with self.assertRaises(ValueError):
            convert_value("2026-01-01garbage", {"data_type": "DATE"})
        self.assertTrue(equivalent("1.00", "1", {"data_type": "DECIMAL"}))

    def test_bad_workbook_and_blank_header_structure(self):
        with self.assertRaises(ValueError):
            read_workbook(b"not an excel file")
        config = {"preview": {"sheet": "Results", "header_row": 1, "columns": [{"column": "A", "header": "Result"},
                                                                                   {"column": "B", "header": "Cột B chưa có tiêu đề"}]}}
        self.assertIsNotNone(match_rows(read_workbook(workbook(headers=("Result", None))), config))


    def test_native_excel_date_and_nonmidnight_datetime(self):
        rows = read_workbook(workbook(date(2026, 10, 30)))["Results"]
        value = rows[1][0]["value"]
        self.assertEqual(convert_value(value, {"data_type": "DATE"}), "2026-10-30")
        with self.assertRaises(ValueError):
            convert_value("2026-10-30T12:30:00", {"data_type": "DATE"})

    def test_dynamic_and_multivalue_choices_not_guessed(self):
        for ui in ({"options": {"source": "lookup", "lookup": "sample"}}, {"control_type": "MULTI_SELECT"}, {"control_type": "CHECKBOX_LIST"}):
            with self.subTest(ui=ui), self.assertRaises(ValueError):
                convert_value("arbitrary", {"data_type": "TEXT", "ui_config": ui})


    def test_context_rejects_foreign_subject_and_study(self):
        with patch("apps.datacapture.application.services.mapped_file_import.get_event_instance_snapshot",
                   return_value={"study_id": 3, "subject_id": 12}):
            with self.assertRaisesMessage(ValueError, "does not belong"):
                MappedFileImportService._context(3, 10, 20)
            with self.assertRaisesMessage(ValueError, "does not belong"):
                MappedFileImportService._context(4, 12, 20)


    def test_runtime_includes_confirmed_mappings_beyond_ui_history_limit(self):
        with TemporaryDirectory() as directory:
            repository = StudyFileImporterRepository(directory)
            for index in range(51):
                upload = repository.create_upload(study_id=3, filename=f"{index}.xlsx", content=b"sample", preview={}, actor_user_id=7)
                repository.confirm(study_id=3, upload_id=upload["id"], mappings=[], actor_user_id=7)
            self.assertEqual(len(repository.list_uploads(3)), 50)
            self.assertEqual(len(repository.list_confirmed_uploads(3)), 51)


    def test_unbounded_numeric_expansion_is_rejected(self):
        with self.assertRaises(ValueError):
            convert_value("1e999999999", {"data_type": "DECIMAL"})

    def test_choice_control_without_choices_rejects_arbitrary_value(self):
        with self.assertRaises(ValueError):
            convert_value("arbitrary", {"data_type": "TEXT", "ui_config": {"control_type": "SELECT"}})


    def test_terminal_visit_status_blocks_even_with_no_page(self):
        for status in ("locked", "finalized", "skipped", "cancelled", "verified"):
            with self.subTest(status=status), patch("apps.datacapture.application.services.mapped_file_import.get_event_instance_snapshot",
                    return_value={"study_id": 3, "subject_id": 10, "status": status}):
                with self.assertRaisesMessage(ValueError, "This visit is locked"):
                    MappedFileImportService._context(3, 10, 20)


    def test_time_type_validates_clock_value(self):
        self.assertEqual(convert_value("12:34", {"data_type": "TIME"}), "12:34:00")
        with self.assertRaises(ValueError):
            convert_value("garbage", {"data_type": "TIME"})


    def test_native_excel_time_is_json_serializable(self):
        data = read_workbook(workbook(time(12, 34)))
        self.assertEqual(data["Results"][1][0]["value"], "12:34:00")
        json.dumps(data)
