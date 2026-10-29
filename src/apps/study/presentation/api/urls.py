from django.urls import path

from apps.study.presentation.api.file_importer import (
    StudyFileImporterAPIView,
    StudyFileImporterConfirmAPIView,
    StudyFileImporterDetailAPIView,
)

app_name = "study_api"

urlpatterns = [
    path("studies/<int:study_id>/file-importer/", StudyFileImporterAPIView.as_view(), name="file_importer"),
    path("studies/<int:study_id>/file-importer/<uuid:upload_id>/", StudyFileImporterDetailAPIView.as_view(), name="file_importer_detail"),
    path("studies/<int:study_id>/file-importer/<uuid:upload_id>/confirm/", StudyFileImporterConfirmAPIView.as_view(), name="file_importer_confirm"),
]
