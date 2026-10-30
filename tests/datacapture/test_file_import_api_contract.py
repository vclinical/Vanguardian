import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from apps.datacapture.application.services.mapped_file_import import StaleImportPreviewError
from apps.datacapture.presentation.api.views.file_import import (
    FileImportAPIView,
    FileImportApplyAPIView,
    FileImportPreviewAPIView,
)

MODULE = "apps.datacapture.presentation.api.views.file_import"


class FileImportApiContractTests(SimpleTestCase):
    def setUp(self):
        self.scope = {"study_id": 3, "subject_id": 127, "visit_id": 2969}
        self.factory = RequestFactory()
        self.apply_url = reverse("datacapture:file_import_apply", kwargs=self.scope)
        self.preview_url = reverse("datacapture:file_import_preview", kwargs=self.scope)
        self.user = SimpleNamespace(is_authenticated=True, pk=44)

    def request(self, body):
        request = self.factory.post(self.apply_url, json.dumps(body), content_type="application/json")
        request.user = self.user
        return request

    def test_routes_use_api_prefix(self):
        self.assertTrue(self.apply_url.startswith("/api/studies/3/subjects/127/"))
        self.assertTrue(self.preview_url.startswith("/api/studies/3/subjects/127/"))

    def test_anonymous_and_missing_csrf_cannot_import(self):
        for url in (self.preview_url, self.apply_url):
            with self.subTest(url=url):
                self.assertIn(self.client.post(url).status_code, (302, 403))
        self.client.handler.enforce_csrf_checks = True
        self.assertEqual(self.client.post(self.apply_url).status_code, 403)

    def test_capture_permission_checked_before_workbook_processing(self):
        request = self.request({})
        with patch("apps.identity.presentation.mixins.enforce_context_permission", side_effect=PermissionDenied) as check:
            with self.assertRaises(PermissionDenied):
                FileImportPreviewAPIView.as_view()(request, **self.scope)
        self.assertEqual(check.call_args.kwargs["permission"], "CRF.ENTER")

    def test_missing_subject_and_wrong_site_are_denied(self):
        request = self.request({})
        view = FileImportAPIView()
        with patch(MODULE + ".get_subject_site_id", return_value=None):
            with self.assertRaises(ValueError):
                view._scope(request, self.scope)
        with patch(MODULE + ".get_subject_site_id", return_value=9), patch(
            MODULE + ".user_bypasses_context_permission", return_value=False,
        ), patch(MODULE + ".ContextualAuthorizationService") as authorization:
            authorization.return_value.can.return_value = SimpleNamespace(allowed=False, reason="wrong site")
            with self.assertRaises(PermissionDenied):
                view._scope(request, self.scope)
            self.assertEqual(authorization.return_value.can.call_args.kwargs["study_site_id"], 9)
            self.assertFalse(authorization.return_value.can.call_args.kwargs["allow_study_scope"])

    def test_malformed_json_shapes_are_400_and_never_apply(self):
        for body in (None, [], "token", 5, {"token": []}, {"token": {}}):
            with self.subTest(body=body), patch.object(FileImportAPIView, "_scope", return_value=self.scope), patch(
                MODULE + ".MappedFileImportService",
            ) as service:
                response = FileImportApplyAPIView().post(self.request(body), **self.scope)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response["Cache-Control"], "no-store")
                service.return_value.apply.assert_not_called()

    def test_invalid_token_never_calls_apply(self):
        with patch.object(FileImportAPIView, "_scope", return_value=self.scope), patch(MODULE + ".MappedFileImportService") as service:
            response = FileImportApplyAPIView().post(self.request({"token": "tampered", "confirmed": True}), **self.scope)
        self.assertEqual(response.status_code, 400)
        service.return_value.apply.assert_not_called()

    def test_overwrite_requires_update_permission(self):
        scoped = dict(self.scope, actor_user_id=44)
        with patch.object(FileImportAPIView, "_scope", side_effect=[scoped, PermissionDenied("no update")]) as scope, patch(
            MODULE + ".inspect_import_preview", return_value={"overwrite_count": 1, "forms": {}},
        ), patch(MODULE + ".MappedFileImportService") as service:
            response = FileImportApplyAPIView().post(self.request({"token": "signed", "confirmed": True}), **self.scope)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(scope.call_args.kwargs["permission"], "CRF.UPDATE")
        service.return_value.apply.assert_not_called()

    def test_stale_preview_returns_conflict_without_success(self):
        service = Mock()
        service.apply.side_effect = StaleImportPreviewError("changed")
        with patch.object(FileImportAPIView, "_scope", return_value=dict(self.scope, actor_user_id=44)), patch(
            MODULE + ".inspect_import_preview", return_value={"overwrite_count": 0, "forms": {}},
        ), patch(MODULE + ".MappedFileImportService", return_value=service):
            response = FileImportApplyAPIView().post(self.request({"token": "signed", "confirmed": True}), **self.scope)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content), {"error": "changed"})

    def test_filling_empty_field_in_existing_page_requires_update_permission(self):
        scoped = dict(self.scope, actor_user_id=44)
        preview = {"overwrite_count": 0, "forms": {"1": {"updates": {"FIELD": "new"}, "snapshot": {"page_id": 10}}}}
        with patch.object(FileImportAPIView, "_scope", side_effect=[scoped, PermissionDenied("no update")]) as scope, patch(
            MODULE + ".inspect_import_preview", return_value=preview,
        ), patch(MODULE + ".MappedFileImportService") as service:
            response = FileImportApplyAPIView().post(self.request({"token": "signed", "confirmed": True}), **self.scope)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(scope.call_args.kwargs["permission"], "CRF.UPDATE")
        service.return_value.apply.assert_not_called()

    def test_missing_file_rejected_without_preview(self):
        with patch.object(FileImportAPIView, "_scope", return_value=self.scope), patch(MODULE + ".MappedFileImportService") as service:
            response = FileImportPreviewAPIView().post(self.request({}), **self.scope)
        self.assertEqual(response.status_code, 400)
        service.return_value.preview.assert_not_called()
