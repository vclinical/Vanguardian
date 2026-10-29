from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse
from openpyxl import Workbook

from apps.datacapture.application.services.laboratory_import import (
    MAX_UPLOAD_BYTES,
    convert_result,
    parse_laboratory_workbook,
)
from apps.datacapture.presentation.api.views.laboratory_import import LaboratoryImportPreviewView


def workbook_file(headers, values, extra_row=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Kết quả"])
    sheet.append(headers)
    sheet.append([])
    sheet.append([])
    sheet.append(values)
    if extra_row:
        sheet.append(extra_row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    stream.seek(0)
    return stream


class LaboratoryWorkbookTests(SimpleTestCase):
    def test_maps_by_header_with_reordered_columns_and_preserves_source(self):
        result = parse_laboratory_workbook(workbook_file(
            ["Định lượng Glucose [Máu]", "Hematocrit (HCT) (0)", " HIV Ag/Ab miễn dịch tự động "],
            ["4,4", 0.49, "0.21 (Âm tính)"],
        ))
        self.assertEqual([item["field_key"] for item in result], ["LAB_BIOCHEM_GLU", "LAB_CBC_HCT", "HIV_RESULT"])
        self.assertEqual([item["value"] for item in result], ["4.4", "0.49", "0"])
        self.assertEqual(result[2]["raw"], "0.21 (Âm tính)")
        self.assertEqual(result[0]["cell"], "A5")

    def test_urine_and_serology_have_different_negative_codes(self):
        self.assertEqual(convert_result("LAB_URINE_GLU", "Âm tính"), "1")
        self.assertEqual(convert_result("HIV_RESULT", "Âm tính"), "0")
        self.assertEqual(convert_result("HBSAG_RESULT", "2.5 (Dương tính)"), "1")

    def test_lymphocyte_count_and_percentage_are_not_swapped(self):
        result = parse_laboratory_workbook(workbook_file(
            ["Số lượng bạch cầu Lympho (LYMPT) (0)", "Tỷ lệ bạch cầu Lympho (LYM%) (0)"],
            [2.61, 29.4],
        ))
        self.assertEqual([item["field_key"] for item in result], ["LAB_CBC_LYMPT", "LAB_CBC_LYMP"])

    def test_blank_cells_do_not_erase_and_unmapped_columns_are_reported(self):
        result = parse_laboratory_workbook(workbook_file(
            ["Đo hoạt độ ALT (GPT) [Máu]", "APTT bệnh/chứng (0)", "Đo hoạt độ AST (GOT) [Máu]"],
            [0, 1.14, None],
        ))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["value"], "0")
        self.assertIsNone(result[1]["field_key"])
        self.assertTrue(result[1]["warning"])

    def test_rejects_ambiguous_results_instead_of_guessing(self):
        for key, value in [("LAB_CBC_HGB", "<5"), ("LAB_CBC_HGB", "155 g/L"), ("HIV_RESULT", "0.21"),
                           ("LAB_URINE_GLU", "+"), ("LAB_CBC_HGB", "NaN")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                convert_result(key, value)

    def test_rejects_duplicate_headers_formulas_and_multiple_result_rows(self):
        header = "Đo hoạt độ ALT (GPT) [Máu]"
        for stream in [workbook_file([header, header], [1, 2]), workbook_file([header], ["=1+1"]),
                       workbook_file([header], [1], extra_row=[2])]:
            with self.assertRaises(ValueError):
                parse_laboratory_workbook(stream)

    def test_rejects_invalid_or_oversized_file(self):
        for stream in [BytesIO(b"not excel"), BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1))]:
            with self.assertRaises(ValueError):
                parse_laboratory_workbook(stream)


class LaboratoryImportPreviewTests(SimpleTestCase):
    def setUp(self):
        self.url = reverse("datacapture:laboratory_import_preview", kwargs={"study_id": 3, "subject_id": 1})

    def test_anonymous_user_cannot_upload(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(response.url, ("/", "/login/?next=" + self.url))

    def test_csrf_is_required(self):
        self.client.handler.enforce_csrf_checks = True
        self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_permission_is_enforced_before_reading_file(self):
        request = RequestFactory().post(self.url)
        request.user = SimpleNamespace(is_authenticated=True)
        with patch("apps.identity.presentation.mixins.enforce_context_permission", side_effect=PermissionDenied) as check:
            with self.assertRaises(PermissionDenied):
                LaboratoryImportPreviewView.as_view()(request, study_id=3, subject_id=1)
        self.assertEqual(check.call_args.kwargs["permission"], "CRF.ENTER")

    def test_invalid_file_returns_actionable_error(self):
        request = RequestFactory().post(self.url, {"file": SimpleUploadedFile("result.xlsx", b"bad")})
        response = LaboratoryImportPreviewView().post(request)
        self.assertEqual(response.status_code, 400)

    def test_preview_returns_values_without_database_writes(self):
        stream = workbook_file(["Đo hoạt độ ALT (GPT) [Máu]"], [52])
        request = RequestFactory().post(self.url, {"file": SimpleUploadedFile("result.xlsx", stream.read())})
        response = LaboratoryImportPreviewView().post(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"value": "52"', response.content)

    def test_dialog_renders_for_subject_and_current_form(self):
        html = render_to_string("subject/includes/laboratory_import.html", {
            "subject_obj": SimpleNamespace(pk=1, study_id=3),
            "focused_event": {"id": 2, "name": "Screening"},
            "focused_form": {"id": 4, "title": "Blood"},
        })
        self.assertIn(self.url, html)
        self.assertIn("data-lab-import-confirm", html)
