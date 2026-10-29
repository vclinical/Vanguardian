from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View

from apps.datacapture.application.services.laboratory_import import parse_laboratory_workbook
from apps.identity.presentation.mixins import ContextPermissionRequiredMixin
from apps.subject.public import SubjectAbstractVerifyStudy


class LaboratoryImportPreviewView(LoginRequiredMixin, ContextPermissionRequiredMixin, SubjectAbstractVerifyStudy, View):
    permission_required = "CRF.ENTER"
    authorization_scope = "STUDY_SITE"
    require_site_context = True
    raise_exception = True

    def post(self, request, *args, **kwargs):
        upload = request.FILES.get("file")
        if upload is None or not upload.name.lower().endswith(".xlsx"):
            return JsonResponse({"error": "Chọn file kết quả .xlsx."}, status=400)
        try:
            results = parse_laboratory_workbook(upload)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse({"results": results})
