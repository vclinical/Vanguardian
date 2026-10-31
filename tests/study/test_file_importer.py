import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse
from openpyxl import Workbook

from apps.study.application.services.file_importer import StudyFileImporterService
from apps.study.infrastructure.file_importer import read_import_workbook
from apps.study.infrastructure.repositories.file_importer import StudyFileImporterRepository
from apps.study.presentation.api.file_importer import StudyFileImporterAPIView, StudyFileImporterConfirmAPIView


def make_excel(headers=("RBC", "HGB"), rows=((5.5, 155),), sheet_name="Results"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(["Laboratory"])
    sheet.append(list(headers))
    sheet.append([])
    for row in rows:
        sheet.append(list(row))
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    return content.getvalue()


class WorkbookReaderTests(SimpleTestCase):
    def test_reads_headers_by_selected_row_and_preserves_sample_cells(self):
        result = read_import_workbook(make_excel(rows=((5.5, "<155"), (5.6, "=1+1"))))
        self.assertEqual(result["sheet"], "Results")
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["columns"][0]["samples"][0], {"cell": "A4", "value": "5.5", "formula": False})
        self.assertEqual(result["columns"][1]["samples"][0]["value"], "<155")
        self.assertTrue(result["columns"][1]["samples"][1]["formula"])

    def test_duplicate_names_remain_distinct_columns_and_blank_columns_survive(self):
        result = read_import_workbook(make_excel(headers=("RBC", "RBC", "Missing", None), rows=((5.5, 6, None, "value"),)))
        self.assertEqual([c["column"] for c in result["columns"]], ["A", "B", "C", "D"])
        self.assertEqual(result["columns"][2]["samples"], [])
        self.assertIn("chưa có tiêu đề", result["columns"][3]["header"])

    def test_caps_samples_without_omitting_data_row_count(self):
        result = read_import_workbook(make_excel(rows=tuple((i, i) for i in range(20))))
        self.assertEqual(result["row_count"], 20)
        self.assertEqual(len(result["columns"][0]["samples"]), 5)

    def test_rejects_bad_sheet_header_formula_and_non_excel(self):
        for content, kwargs in [(b"not excel", {}), (make_excel(), {"sheet_name": "absent"}),
                                (make_excel(), {"header_row": 0}), (make_excel(), {"header_row": 100}),
                                (make_excel(headers=("=1+1", "HGB")), {})]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                read_import_workbook(content, **kwargs)

    def test_rejects_oversized_archive_before_parsing(self):
        content = BytesIO()
        with ZipFile(content, "w", compression=8) as archive:
            archive.writestr("huge.xml", b"x" * (51 * 1024 * 1024))
        with self.assertRaisesMessage(ValueError, "50 MB"):
            read_import_workbook(content.getvalue())


class FileImporterServiceTests(SimpleTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = StudyFileImporterRepository(self.temp.name)
        self.repository.list_bindings = Mock(return_value=[
            {"id": 10, "event_definition_id": 1, "event_definition__code": "SCREENING", "event_definition__name": "Screening",
             "form_definition_id": 100, "form_definition__code": "SCREENING_BLOOD", "study_version": "1"},
            {"id": 20, "event_definition_id": 2, "event_definition__code": "VISIT1", "event_definition__name": "Visit 1",
             "form_definition_id": 200, "form_definition__code": "VISIT1_BLOOD", "study_version": "1"},
        ])
        self.repository.list_events = Mock(return_value=[])
        adapter = Mock()
        adapter.list_template_fields_with_ui_config.side_effect = lambda template_id: [
            {"id": str(template_id + 1), "field_key": "LAB_RBC", "label": "RBC", "data_type": "DECIMAL", "unit": "T/L", "ui_config": None},
            {"id": str(template_id + 2), "field_key": "OTHER_RBC", "label": "Other RBC", "data_type": "DECIMAL", "unit": "T/L"},
        ]
        self.service = StudyFileImporterService(self.repository, adapter)
        self.upload = self.service.upload(study_id=3, filename="results.xlsx", content=make_excel(),
                                          sheet_name="", header_row=2, actor_user_id=99)
        self.mapping = [
            {"column": "A", "targets": [{"binding_id": 10, "field_id": 101}, {"binding_id": 10, "field_id": 102},
                                         {"binding_id": 20, "field_id": 201}], "skip_reason": ""},
            {"column": "B", "targets": [], "skip_reason": "Cần hỏi thêm field"},
        ]

    def confirm(self, mapping=None):
        return self.service.confirm(study_id=3, upload_id=self.upload["id"],
                                    mappings=self.mapping if mapping is None else mapping, actor_user_id=99)

    def test_translation_search_labels_preserve_mapping_code(self):
        self.service.crf_adapter.list_template_fields_with_ui_config.side_effect = None
        self.service.crf_adapter.list_template_fields_with_ui_config.return_value = [
            {"id": "101", "field_key": "LAB_BIOCHEM_GPT", "label": "ALT (GPT)",
             "labels": ["ALT (GPT)", "Đo hoạt độ ALT (GPT) [Máu]"], "data_type": "DECIMAL", "unit": "U/L"},
        ]
        field = self.service.catalog(3)["bindings"][0]["fields"][0]
        self.assertEqual(field["labels"], ["ALT (GPT)", "Đo hoạt độ ALT (GPT) [Máu]"])
        result = self.confirm([
            {"column": "A", "targets": [{"binding_id": 10, "field_id": 101, "field_key": "forged"}], "skip_reason": ""},
            {"column": "B", "targets": [], "skip_reason": "Không dùng"},
        ])
        self.assertEqual(result["mappings"][0]["targets"][0]["field_key"], "LAB_BIOCHEM_GPT")

    def test_one_column_maps_to_multiple_fields_and_visits_with_persistent_confirmation(self):
        saved = self.confirm()
        self.assertEqual(len(saved["mappings"][0]["targets"]), 3)
        self.assertEqual(saved["mappings"][0]["targets"][2]["visit_code"], "VISIT1")
        reopened = StudyFileImporterRepository(self.temp.name).get_upload(3, self.upload["id"])
        self.assertEqual(reopened["confirmation"], saved)
        self.assertEqual(saved["confirmed_by"], 99)
        self.assertEqual(self.repository.list_uploads(3)[0]["confirmed"], True)
        self.assertEqual(self.repository.list_uploads(4), [])

    def test_rejects_target_shared_by_two_source_columns(self):
        self.mapping[1]["targets"] = [{"binding_id": 10, "field_id": 101}]
        with self.assertRaisesMessage(ValueError, "hai cột"):
            self.confirm()
        self.assertIsNone(self.repository.get_upload(3, self.upload["id"])["confirmation"])

    def test_revalidates_current_catalog_on_confirmation(self):
        self.repository.list_bindings.return_value = []
        with self.assertRaisesMessage(ValueError, "không thuộc study"):
            self.confirm()

    def test_rejects_fields_from_another_binding_or_study(self):
        self.mapping[0]["targets"] = [{"binding_id": 10, "field_id": 201}]
        with self.assertRaisesMessage(ValueError, "không thuộc study"):
            self.confirm()
        with self.assertRaises(ValueError):
            self.repository.get_upload(4, self.upload["id"])

    def test_requires_decision_for_every_column(self):
        with self.assertRaises(ValueError):
            self.confirm(self.mapping[:1])
        self.mapping[1]["skip_reason"] = ""
        with self.assertRaisesMessage(ValueError, "lý do bỏ qua"):
            self.confirm()

    def test_duplicate_column_and_malformed_targets_are_rejected(self):
        for mappings in [[self.mapping[0], self.mapping[0]], [{"column": [], "targets": []}, self.mapping[1]],
                         [{"column": "A", "targets": [None]}, self.mapping[1]]]:
            with self.assertRaises(ValueError):
                self.confirm(mappings)

    def test_confirmation_cannot_overwrite_existing_mapping(self):
        original = self.confirm()
        with self.assertRaisesMessage(ValueError, "đã được xác nhận"):
            self.confirm()
        self.assertEqual(self.repository.get_upload(3, self.upload["id"])["confirmation"], original)
        with self.assertRaisesMessage(ValueError, "đã được xác nhận"):
            self.repository.confirm(study_id=3, upload_id=self.upload["id"], mappings=[], actor_user_id=8)

    def test_prevents_path_traversal(self):
        for identifier in ["../../outside", "../3/file", None]:
            with self.assertRaises(ValueError):
                self.repository.get_upload(3, identifier)

    def test_edit_preserves_source_and_archives_previous_confirmation(self):
        original = self.confirm()
        directory = self.repository._directory(3, self.upload["id"])
        source = (directory / "source.xlsx").read_bytes()
        self.mapping[0]["targets"] = [{"binding_id": 20, "field_id": 202}]
        self.mapping[1]["skip_reason"] = "Không sử dụng"
        updated = self.service.confirm(study_id=3, upload_id=self.upload["id"], mappings=self.mapping,
                                       actor_user_id=8, expected_confirmed_at=original["confirmed_at"])
        self.assertEqual(updated["confirmed_by"], 8)
        self.assertEqual(updated["mappings"][0]["targets"][0]["field_id"], 202)
        self.assertEqual(updated["mappings"][1]["skip_reason"], "Không sử dụng")
        self.assertEqual(self.repository.get_upload(3, self.upload["id"])["confirmation"], updated)
        self.assertEqual(self.repository.list_confirmed_uploads(3)[0]["confirmation"], updated)
        self.assertEqual(len(self.repository.list_uploads(3)), 1)
        self.assertEqual((directory / "source.xlsx").read_bytes(), source)
        archived = list((directory / "revisions").glob("*.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(json.loads(archived[0].read_text()), original)

    def test_edit_revalidates_mapping_and_preserves_original_on_failure(self):
        original = self.confirm()
        self.repository.list_bindings.return_value = []
        with self.assertRaisesMessage(ValueError, "không thuộc study"):
            self.service.confirm(study_id=3, upload_id=self.upload["id"], mappings=self.mapping,
                                 actor_user_id=8, expected_confirmed_at=original["confirmed_at"])
        self.assertEqual(self.repository.get_upload(3, self.upload["id"])["confirmation"], original)

    def test_stale_invalid_and_cross_study_edits_are_rejected(self):
        original = self.confirm()
        for token in ["old-version", "", True, 123, []]:
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.service.confirm(study_id=3, upload_id=self.upload["id"], mappings=self.mapping,
                                     actor_user_id=8, expected_confirmed_at=token)
        with self.assertRaises(ValueError):
            self.service.confirm(study_id=4, upload_id=self.upload["id"], mappings=self.mapping,
                                 actor_user_id=8, expected_confirmed_at=original["confirmed_at"])
        self.assertEqual(self.repository.get_upload(3, self.upload["id"])["confirmation"], original)

    def test_concurrent_edits_allow_only_one_writer(self):
        original = self.confirm()
        barrier = Barrier(2)

        def save(actor):
            barrier.wait(timeout=5)
            try:
                return self.repository.confirm(study_id=3, upload_id=self.upload["id"], mappings=self.mapping,
                                               actor_user_id=actor, expected_confirmed_at=original["confirmed_at"])
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(save, [8, 9]))
        successful = [result for result in results if result is not None]
        self.assertEqual(len(successful), 1)
        self.assertEqual(self.repository.get_upload(3, self.upload["id"])["confirmation"], successful[0])

    def test_failed_publish_keeps_current_mapping_readable(self):
        original = self.confirm()
        with patch("apps.study.infrastructure.repositories.file_importer.os.replace", side_effect=OSError), self.assertRaises(OSError):
            self.repository.confirm(study_id=3, upload_id=self.upload["id"], mappings=self.mapping,
                                    actor_user_id=8, expected_confirmed_at=original["confirmed_at"])
        self.assertEqual(self.repository.get_upload(3, self.upload["id"])["confirmation"], original)


class FileImporterAPITests(SimpleTestCase):
    def setUp(self):
        self.url = reverse("study_api:file_importer", kwargs={"study_id": 3})
        self.factory = RequestFactory()

    def test_urls_use_api_prefix_and_tab_has_own_page(self):
        self.assertEqual(self.url, "/api/studies/3/file-importer/")
        self.assertEqual(reverse("study:study_file_importer", kwargs={"study_id": 3}), "/studies/3/file-importer")

    def test_anonymous_and_csrf_requests_cannot_save_mapping(self):
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.client.handler.enforce_csrf_checks = True
        self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_manage_permission_is_checked_before_upload(self):
        request = self.factory.post(self.url)
        request.user = SimpleNamespace(is_authenticated=True)
        with patch("apps.identity.presentation.mixins.enforce_context_permission", side_effect=PermissionDenied) as check:
            with self.assertRaises(PermissionDenied):
                StudyFileImporterAPIView.as_view()(request, study_id=3)
        self.assertEqual(check.call_args.kwargs["permission"], "STUDY_CONFIG.MANAGE")

    def test_edit_passes_version_and_authenticated_actor_to_service(self):
        payload = {"confirmed": True, "mappings": [], "expected_confirmed_at": "version-one", "actor_user_id": 8}
        request = self.factory.post(self.url, json.dumps(payload), content_type="application/json")
        request.user = SimpleNamespace(is_authenticated=True, pk=99)
        service = Mock()
        service.confirm.return_value = {"confirmed_at": "version-two"}
        with patch("apps.identity.presentation.mixins.enforce_context_permission", return_value=None) as check, patch.object(
            StudyFileImporterConfirmAPIView, "get_service", return_value=service,
        ):
            response = StudyFileImporterConfirmAPIView.as_view()(request, study_id=3, upload_id="upload")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(check.call_args.kwargs["permission"], "STUDY_CONFIG.MANAGE")
        service.confirm.assert_called_once_with(study_id=3, upload_id="upload", mappings=[], actor_user_id=99,
                                                expected_confirmed_at="version-one")

    def test_edit_without_manage_permission_does_not_call_service(self):
        request = self.factory.post(self.url, json.dumps({"confirmed": True, "expected_confirmed_at": "v1"}), content_type="application/json")
        request.user = SimpleNamespace(is_authenticated=True, pk=99)
        with patch("apps.identity.presentation.mixins.enforce_context_permission", side_effect=PermissionDenied), patch.object(
            StudyFileImporterConfirmAPIView, "get_service",
        ) as get_service, self.assertRaises(PermissionDenied):
            StudyFileImporterConfirmAPIView.as_view()(request, study_id=3, upload_id="upload")
        get_service.assert_not_called()

    def test_invalid_file_and_missing_confirmation_return_400(self):
        request = self.factory.post(self.url, {"file": SimpleUploadedFile("bad.xlsx", b"bad")})
        request.user = SimpleNamespace(is_authenticated=True, pk=99)
        service = Mock()
        service.upload.side_effect = ValueError("Không đọc được Excel")
        with patch("apps.identity.presentation.mixins.enforce_context_permission", return_value=None), patch.object(
            StudyFileImporterAPIView, "get_service", return_value=service,
        ):
            response = StudyFileImporterAPIView.as_view()(request, study_id=3)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["Cache-Control"], "no-store")
        request = self.factory.post(self.url, json.dumps({"confirmed": False}), content_type="application/json")
        request.user = SimpleNamespace(is_authenticated=True, pk=99)
        with patch("apps.identity.presentation.mixins.enforce_context_permission", return_value=None), patch.object(
            StudyFileImporterConfirmAPIView, "get_service", return_value=service,
        ):
            response = StudyFileImporterConfirmAPIView.as_view()(request, study_id=3, upload_id="unused")
        self.assertEqual(response.status_code, 400)
        service.confirm.assert_not_called()
