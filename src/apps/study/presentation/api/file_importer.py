import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.views import View

from apps.identity.presentation.mixins import ContextPermissionRequiredMixin
from apps.study.application.services.file_importer import MAX_FILE_BYTES, StudyFileImporterService
from apps.study.application.services.study_directory import StudyDirectoryQueryService

logger = logging.getLogger(__name__)


class FileImporterAccessView(LoginRequiredMixin, ContextPermissionRequiredMixin, View):
    permission_required = "STUDY_CONFIG.MANAGE"
    authorization_scope = "STUDY"
    raise_exception = True

    def dispatch(self, request, *args, **kwargs):
        try:
            response = super().dispatch(request, *args, **kwargs)
        except ValueError as exc:
            response = JsonResponse({"error": str(exc)}, status=400)
        except OSError:
            logger.exception("File Importer private storage failure")
            response = JsonResponse({"error": "Không lưu được file/mapping. Vui lòng thử lại."}, status=500)
        response["Cache-Control"] = "no-store"
        return response

    def get_service(self):
        if StudyDirectoryQueryService.get_study_id(study_id=self.kwargs["study_id"]) is None:
            raise Http404
        return StudyFileImporterService()


class StudyFileImporterAPIView(FileImporterAccessView):
    def get(self, request, study_id):
        service = self.get_service()
        return JsonResponse({"catalog": service.catalog(study_id), "uploads": service.repository.list_uploads(study_id)})

    def post(self, request, study_id):
        service = self.get_service()
        upload = request.FILES.get("file")
        if upload is None or upload.size > MAX_FILE_BYTES:
            raise ValueError("Chọn file .xlsx không quá 5 MB.")
        try:
            header_row = int(request.POST.get("header_row", "2"))
        except (ValueError, TypeError) as exc:
            raise ValueError("Dòng tiêu đề phải là số nguyên.") from exc
        record = service.upload(study_id=study_id, filename=upload.name, content=upload.read(MAX_FILE_BYTES + 1),
                                header_row=header_row, sheet_name=request.POST.get("sheet_name", ""), actor_user_id=request.user.pk)
        return JsonResponse(record, status=201)


class StudyFileImporterDetailAPIView(FileImporterAccessView):
    def get(self, request, study_id, upload_id):
        return JsonResponse(self.get_service().repository.get_upload(study_id, upload_id))


class StudyFileImporterConfirmAPIView(FileImporterAccessView):
    def post(self, request, study_id, upload_id):
        service = self.get_service()
        if len(request.body) > 512 * 1024:
            raise ValueError("Mapping vượt quá kích thước cho phép.")
        try:
            payload = json.loads(request.body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Nội dung mapping không hợp lệ.") from exc
        if not isinstance(payload, dict) or payload.get("confirmed") is not True:
            raise ValueError("Cần xác nhận đã đối chiếu các cột và field đích.")
        record = service.confirm(study_id=study_id, upload_id=upload_id,
                                 mappings=payload.get("mappings"), actor_user_id=request.user.pk,
                                 expected_confirmed_at=payload.get("expected_confirmed_at"))
        return JsonResponse(record)
