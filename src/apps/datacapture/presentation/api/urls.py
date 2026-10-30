from django.urls import path

from apps.datacapture.presentation.api.views import (
    DataCaptureDeleteDraftAPIView,
    DataCaptureEventAttestationRevokeAPIView,
    DataCaptureEventAttestationSubmitAPIView,
    DataCaptureFormInstanceListCreateAPIView,
    DataCaptureSaveAPIView,
    DataCaptureSubmitAPIView,
)
from apps.datacapture.presentation.api.views.file_import import FileImportApplyAPIView, FileImportPreviewAPIView

app_name = "datacapture"

urlpatterns = [
    path("studies/<int:study_id>/subjects/<int:subject_id>/events/<int:visit_id>/file-import/preview/", FileImportPreviewAPIView.as_view(), name="file_import_preview"),
    path("studies/<int:study_id>/subjects/<int:subject_id>/events/<int:visit_id>/file-import/apply/", FileImportApplyAPIView.as_view(), name="file_import_apply"),
    path(
        "studies/<int:study_id>/subjects/<int:subject_id>/events/<int:visit_id>/form-instances/",
        DataCaptureFormInstanceListCreateAPIView.as_view(),
        name="form_instances",
    ),
    path(
        "studies/<int:study_id>/subjects/<int:subject_id>/events/<int:visit_id>/forms/<int:crf_template_id>/save/",
        DataCaptureSaveAPIView.as_view(),
        name="page_save",
    ),
    path(
        "studies/<int:study_id>/subjects/<int:subject_id>/events/<int:visit_id>/forms/<int:crf_template_id>/submit/",
        DataCaptureSubmitAPIView.as_view(),
        name="page_submit",
    ),
    path(
        "studies/<int:study_id>/subjects/<int:subject_id>/events/<int:visit_id>/forms/<int:crf_template_id>/delete-draft/",
        DataCaptureDeleteDraftAPIView.as_view(),
        name="page_delete_draft",
    ),
    path(
        "studies/<int:study_id>/subjects/<int:subject_id>/events/<int:visit_id>/attestations/<int:attestation_policy_id>/submit/",
        DataCaptureEventAttestationSubmitAPIView.as_view(),
        name="event_attestation_submit",
    ),
    path(
        "studies/<int:study_id>/subjects/<int:subject_id>/events/<int:visit_id>/attestations/<int:event_attestation_id>/revoke/",
        DataCaptureEventAttestationRevokeAPIView.as_view(),
        name="event_attestation_revoke",
    ),
]
