import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views import View

from apps.datacapture.application.exceptions import DataCaptureValidationError
from apps.datacapture.application.services.mapped_file_import import MAX_FILE_BYTES, MappedFileImportService, StaleImportPreviewError, inspect_import_preview
from apps.identity.application.authorization import ContextualAuthorizationService, user_bypasses_context_permission
from apps.identity.presentation.mixins import ContextPermissionRequiredMixin
from apps.subject.public import SubjectAbstractVerifyStudy, get_subject_site_id


class FileImportAPIView(LoginRequiredMixin, ContextPermissionRequiredMixin, SubjectAbstractVerifyStudy, View):
    permission_required = "CRF.ENTER"
    authorization_scope = "STUDY_SITE"
    require_site_context = True
    raise_exception = True

    def _scope(self, request, kwargs, permission="CRF.ENTER"):
        site_id = get_subject_site_id(study_id=kwargs["study_id"], subject_id=kwargs["subject_id"])
        if site_id is None:
            raise ValueError(_("The participant was not found in this study."))
        if not user_bypasses_context_permission(request.user):
            decision = ContextualAuthorizationService(request=request).can(user=request.user, permission=permission,
                study_id=kwargs["study_id"], study_site_id=site_id, allow_study_scope=False, allow_site_scope=True)
            if not decision.allowed:
                raise PermissionDenied(decision.reason)
        return {key: kwargs[key] for key in ("study_id", "subject_id", "visit_id")} | {"actor_user_id": request.user.pk}

    def post(self, request, *args, **kwargs):
        try:
            result = self.execute(request, kwargs)
            response = JsonResponse(result)
        except StaleImportPreviewError as exc:
            response = JsonResponse({"error": str(exc)}, status=409)
        except DataCaptureValidationError as exc:
            response = JsonResponse({"error": "; ".join(exc.messages)}, status=400)
        except (ValueError, TypeError, KeyError) as exc:
            response = JsonResponse({"error": str(exc)}, status=400)
        except PermissionDenied as exc:
            response = JsonResponse({"error": str(exc)}, status=403)
        response["Cache-Control"] = "no-store"
        return response


class FileImportPreviewAPIView(FileImportAPIView):
    def execute(self, request, kwargs):
        scope = self._scope(request, kwargs)
        upload = request.FILES.get("file")
        if upload is None or upload.size > MAX_FILE_BYTES:
            raise ValueError(_("Choose an .xlsx file no larger than 5 MB."))
        row = request.POST.get("row_number")
        return MappedFileImportService().preview(**scope, filename=upload.name, content=upload.read(),
            mapping_id=request.POST.get("mapping_id") or None, row_number=int(row) if row else None,
            value_overrides=json.loads(request.POST.get("value_overrides") or "{}"))


class FileImportApplyAPIView(FileImportAPIView):
    def execute(self, request, kwargs):
        scope = self._scope(request, kwargs)
        if len(request.body) > 2 * 1024 * 1024:
            raise ValueError(_("The import request exceeds the size limit."))
        body = json.loads(request.body)
        if not isinstance(body, dict) or not isinstance(body.get("token"), str):
            raise ValueError(_("The confirmation request must be a JSON object with a valid token."))
        preview = inspect_import_preview(body.get("token") or "")
        if preview.get("overwrite_count") or any(form.get("updates") and form["snapshot"].get("page_id") for form in preview.get("forms", {}).values()):
            self._scope(request, kwargs, permission="CRF.UPDATE")
        return MappedFileImportService().apply(**scope, token=body.get("token") or "", confirmed=body.get("confirmed"),
            field_decisions=body.get("field_decisions"), reason=body.get("reason", ""))
