import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.core.form_data_document import flatten_form_data_for_export, normalize_form_data
from apps.crf.public import CrfContextAdapter
from apps.datacapture.application.exceptions import DataCaptureValidationError
from apps.datacapture.domain import DataCapturePageState
from apps.datacapture.public import (
    certify_page_for_subject_visit_crf,
    finalize_page_data_for_subject_visit_crf,
    get_current_validation_failures_for_acknowledgement,
    get_latest_submitted_page_entry_for_subject_visit_crf,
    get_page_entry_for_subject_visit_crf,
    get_page_state_contexts,
    get_page_state_id_for_subject_visit_crf,
    get_page_state_status_for_subject_visit_crf,
    lock_page_for_subject_visit_crf,
    merge_form_verification_checked_fields_into_page_state_final_data,
    reopen_verified_form_verification_page_state,
)
from apps.identity.presentation.mixins import ContextPermissionRequiredMixin
from apps.reconcile.public import (
    acknowledge_reconcile_validation_issues,
    cancel_reconcile_dataquery,
    cancel_reconcile_query,
    close_reconcile_query,
    open_reconcile_query,
    reopen_reconcile_query,
    reply_to_reconcile_query,
    request_clarification_reconcile_query,
    resolve_reconcile_query,
)
from apps.shared.navigation import user_can_access_permission
from apps.subject.application import (
    SubjectFormVerificationRequestValidator,
    SubjectValidationError,
)
from apps.subject.public import SubjectAbstractVerifyStudy

SELF_REVIEW_ERROR = "Bạn không được verify hoặc thao tác Query cho form do chính bạn cập nhật."
STALE_REVIEW_ERROR = "Dữ liệu đã bị thao tác, vui lòng reload lại trang để tiếp tục"
FIELD_STALE_REVIEW_ERROR_TEMPLATE = "field {field_key} đã bị thay đổi, vui lòng kiểm tra lại."
QUERY_ACTION_PERMISSION_BY_ACTION = {
    "request_clarification": "QUERY.RETURN",
    "resolve": "QUERY.CLOSE",
    "close": "QUERY.CLOSE",
    "reopen": "QUERY.RETURN",
    "cancel": "QUERY.CANCEL",
}


def _same_user(left, right) -> bool:
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return False


def _current_user_matches_submitted_entry_editor(
    *,
    request,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
) -> bool:
    submitted_entry = get_latest_submitted_page_entry_for_subject_visit_crf(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        **_event_form_binding_kwargs_from_request(request),
    )
    return _same_user(getattr(request.user, "id", None), getattr(submitted_entry, "updated_by_id", None))


def _parse_int_or_none(value) -> int | None:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def _event_form_binding_id_from_request(request) -> int | None:
    return (
        _parse_int_or_none(request.GET.get("form"))
        or _parse_int_or_none(request.POST.get("form"))
        or _parse_int_or_none(request.GET.get("event_form_binding_id"))
        or _parse_int_or_none(request.POST.get("event_form_binding_id"))
    )


def _event_form_binding_kwargs(event_form_binding_id: int | None) -> dict[str, int]:
    if event_form_binding_id is None:
        return {}
    return {"event_form_binding_id": event_form_binding_id}


def _event_form_binding_kwargs_from_request(request) -> dict[str, int]:
    return _event_form_binding_kwargs(_event_form_binding_id_from_request(request))


def _load_entry_payload_map(raw_payload) -> dict:
    if not raw_payload:
        return {}
    try:
        loaded = json.loads(raw_payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    doc = normalize_form_data(loaded, strict=False)
    return flatten_form_data_for_export(doc, repeat_strategy="legacy_repeat_suffix")


def _field_display_key(field_row: dict, field_template_id: int) -> str:
    return (
        str(field_row.get("field_key") or "").strip()
        or str(field_row.get("brief_description") or "").strip()
        or f"field_{field_template_id}"
    )


def _field_value_marker(*, payload: dict, field_row: dict, field_template_id: int) -> str:
    field_key = str(field_row.get("field_key") or "").strip()
    aliases = [alias for alias in (field_key, f"field_{field_template_id}") if alias]
    for alias in aliases:
        if alias in payload:
            return json.dumps(payload.get(alias), ensure_ascii=False, sort_keys=True, default=str)
    return ""


def _review_context_matches_submitted_entry(*, submitted_entry, review_page_entry_id: str, review_entry_version: str) -> bool:
    normalized_review_page_entry_id = _parse_int_or_none(review_page_entry_id)
    if normalized_review_page_entry_id is not None:
        return int(getattr(submitted_entry, "id", 0) or 0) == normalized_review_page_entry_id
    normalized_review_entry_version = str(review_entry_version or "").strip()
    if not normalized_review_entry_version or normalized_review_entry_version == "—":
        return False
    return str(getattr(submitted_entry, "entry_version", "") or "").strip() == normalized_review_entry_version


def _filter_changed_review_fields(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    normalized_payload: dict[str, object],
    submitted_entry,
    event_form_binding_id: int | None = None,
) -> tuple[bool, list[int], list[dict[str, object]], bool]:
    checked_field_template_ids = list(normalized_payload["field_template_ids"])
    if _review_context_matches_submitted_entry(
        submitted_entry=submitted_entry,
        review_page_entry_id=str(normalized_payload["review_page_entry_id"]),
        review_entry_version=str(normalized_payload["review_entry_version"]),
    ):
        return True, checked_field_template_ids, [], False

    review_page_entry_id = _parse_int_or_none(normalized_payload["review_page_entry_id"])
    if review_page_entry_id is None:
        return False, [], [], False
    reviewed_entry = get_page_entry_for_subject_visit_crf(
        page_entry_id=review_page_entry_id,
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        **_event_form_binding_kwargs(event_form_binding_id),
    )
    if reviewed_entry is None:
        return False, [], [], False

    reviewed_payload = _load_entry_payload_map(getattr(reviewed_entry, "data", ""))
    submitted_payload = _load_entry_payload_map(getattr(submitted_entry, "data", ""))
    field_rows = CrfContextAdapter().list_template_fields_with_ui_config(template_id=crf_template_id)
    field_row_by_id = {}
    for field_row in field_rows:
        field_template_id = _parse_int_or_none(field_row.get("id"))
        if field_template_id is None:
            continue
        field_row_by_id[field_template_id] = field_row

    changed_fields: list[dict[str, object]] = []
    allowed_field_template_ids: list[int] = []
    for field_template_id in checked_field_template_ids:
        field_row = field_row_by_id.get(int(field_template_id), {})
        reviewed_value = _field_value_marker(
            payload=reviewed_payload,
            field_row=field_row,
            field_template_id=int(field_template_id),
        )
        submitted_value = _field_value_marker(
            payload=submitted_payload,
            field_row=field_row,
            field_template_id=int(field_template_id),
        )
        if reviewed_value != submitted_value:
            field_key = _field_display_key(field_row, int(field_template_id))
            changed_fields.append(
                {
                    "field_template_id": int(field_template_id),
                    "field_key": field_key,
                    "message": FIELD_STALE_REVIEW_ERROR_TEMPLATE.format(field_key=field_key),
                }
            )
            continue
        allowed_field_template_ids.append(int(field_template_id))

    return True, allowed_field_template_ids, changed_fields, bool(changed_fields)


def _review_context_status_and_submitted_entry(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    event_form_binding_id: int | None = None,
):
    current_page_status = get_page_state_status_for_subject_visit_crf(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        **_event_form_binding_kwargs(event_form_binding_id),
    )
    normalized_current_status = (current_page_status or "").strip().lower()
    if not DataCapturePageState.can_start_or_continue_review(normalized_current_status):
        return False, current_page_status, None

    submitted_entry = get_latest_submitted_page_entry_for_subject_visit_crf(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        **_event_form_binding_kwargs(event_form_binding_id),
    )
    if submitted_entry is None:
        return False, current_page_status, None
    return True, current_page_status, submitted_entry


@method_decorator(csrf_exempt, name="dispatch")
class SubjectFormVerificationVerifyCheckedView(
    LoginRequiredMixin,
    ContextPermissionRequiredMixin,
    SubjectAbstractVerifyStudy,
    View,
):
    permission_required = "SDV.MARK"
    authorization_scope = "STUDY_SITE"
    require_site_context = True
    raise_exception = True

    def post(self, request, *args, **kwargs):
        try:
            normalized = SubjectFormVerificationRequestValidator.parse_verify_checked_payload(
                request.body
            )
            subject_id = int(kwargs["subject_id"])
            visit_id = int(kwargs["visit_id"])
            crf_template_id = int(kwargs["crf_template_id"])
            event_form_binding_id = _event_form_binding_id_from_request(request)
            context_is_reviewable, current_page_status, submitted_entry = (
                _review_context_status_and_submitted_entry(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    event_form_binding_id=event_form_binding_id,
                )
            )
            if not context_is_reviewable:
                return JsonResponse({"error": [STALE_REVIEW_ERROR]}, status=400)
            context_can_continue, checked_field_template_ids, stale_review_fields, reload_required = (
                _filter_changed_review_fields(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    normalized_payload=normalized,
                    submitted_entry=submitted_entry,
                    event_form_binding_id=event_form_binding_id,
                )
            )
            if not context_can_continue:
                return JsonResponse({"error": [STALE_REVIEW_ERROR]}, status=400)
            if not checked_field_template_ids and stale_review_fields:
                return JsonResponse(
                    {
                        "ok": True,
                        "field_template_ids": [],
                        "all_verified": False,
                        "page_status": current_page_status,
                        "blocking_reasons": [],
                        "unverified_field_template_ids": [],
                        "stale_review_fields": stale_review_fields,
                        "reload_required": reload_required,
                    }
                )
            (
                all_verified,
                page_status,
                blocking_reasons,
                unverified_field_template_ids,
            ) = merge_form_verification_checked_fields_into_page_state_final_data(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                checked_field_template_ids=checked_field_template_ids,
                unverify_reason_text=normalized["reason_text"],
                actor_user_id=getattr(request.user, "id", None),
                **_event_form_binding_kwargs(event_form_binding_id),
            )
        except (SubjectValidationError, DataCaptureValidationError) as exc:
            return JsonResponse({"error": list(exc.messages)}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "field_template_ids": checked_field_template_ids,
                "all_verified": all_verified,
                "page_status": page_status,
                "blocking_reasons": blocking_reasons,
                "unverified_field_template_ids": unverified_field_template_ids,
                "stale_review_fields": stale_review_fields,
                "reload_required": reload_required,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class SubjectFormVerificationReopenView(
    LoginRequiredMixin,
    ContextPermissionRequiredMixin,
    SubjectAbstractVerifyStudy,
    View,
):
    permission_required = "SDV.MARK"
    authorization_scope = "STUDY_SITE"
    require_site_context = True
    raise_exception = True

    def post(self, request, *args, **kwargs):
        try:
            reason_text = SubjectFormVerificationRequestValidator.parse_reopen_reason_text(
                request.body
            )
            page_status = reopen_verified_form_verification_page_state(
                subject_id=int(kwargs["subject_id"]),
                visit_id=int(kwargs["visit_id"]),
                crf_template_id=int(kwargs["crf_template_id"]),
                reason_text=reason_text,
                actor_user_id=getattr(request.user, "id", None),
                **_event_form_binding_kwargs_from_request(request),
            )
        except (SubjectValidationError, DataCaptureValidationError) as exc:
            return JsonResponse({"error": list(exc.messages)}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "page_status": page_status,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class SubjectFormVerificationFinalizePageDataView(
    LoginRequiredMixin,
    ContextPermissionRequiredMixin,
    SubjectAbstractVerifyStudy,
    View,
):
    permission_required = "SDV.MARK"
    authorization_scope = "STUDY_SITE"
    require_site_context = True
    raise_exception = True

    def post(self, request, *args, **kwargs):
        try:
            subject_id = int(kwargs["subject_id"])
            visit_id = int(kwargs["visit_id"])
            crf_template_id = int(kwargs["crf_template_id"])
            page_status = finalize_page_data_for_subject_visit_crf(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                actor_user_id=getattr(request.user, "id", None),
                **_event_form_binding_kwargs_from_request(request),
            )
        except (DataCaptureValidationError, ValueError) as exc:
            messages = list(exc.messages) if hasattr(exc, "messages") else [str(exc)]
            return JsonResponse({"error": messages}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "page_status": page_status,
                "reload_required": True,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class SubjectFormVerificationCertifyPageView(
    LoginRequiredMixin,
    ContextPermissionRequiredMixin,
    SubjectAbstractVerifyStudy,
    View,
):
    permission_required = "EVENT_CERTIFICATION.CERTIFY"
    authorization_scope = "STUDY_SITE"
    require_site_context = True
    raise_exception = True

    def post(self, request, *args, **kwargs):
        try:
            page_status = certify_page_for_subject_visit_crf(
                subject_id=int(kwargs["subject_id"]),
                visit_id=int(kwargs["visit_id"]),
                crf_template_id=int(kwargs["crf_template_id"]),
                actor_user_id=getattr(request.user, "id", None),
                **_event_form_binding_kwargs_from_request(request),
            )
        except (DataCaptureValidationError, ValueError) as exc:
            messages = list(exc.messages) if hasattr(exc, "messages") else [str(exc)]
            return JsonResponse({"error": messages}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "page_status": page_status,
                "reload_required": True,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class SubjectFormVerificationLockPageView(
    LoginRequiredMixin,
    ContextPermissionRequiredMixin,
    SubjectAbstractVerifyStudy,
    View,
):
    permission_required = "DATA.LOCK"
    authorization_scope = "STUDY_SITE"
    require_site_context = True
    raise_exception = True

    def post(self, request, *args, **kwargs):
        try:
            page_status = lock_page_for_subject_visit_crf(
                subject_id=int(kwargs["subject_id"]),
                visit_id=int(kwargs["visit_id"]),
                crf_template_id=int(kwargs["crf_template_id"]),
                actor_user_id=getattr(request.user, "id", None),
                **_event_form_binding_kwargs_from_request(request),
            )
        except (DataCaptureValidationError, ValueError) as exc:
            messages = list(exc.messages) if hasattr(exc, "messages") else [str(exc)]
            return JsonResponse({"error": messages}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "page_status": page_status,
                "reload_required": True,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class SubjectFormVerificationQueryThreadView(
    LoginRequiredMixin,
    ContextPermissionRequiredMixin,
    SubjectAbstractVerifyStudy,
    View,
):
    permission_required = "SDV.MARK"
    authorization_scope = "STUDY_SITE"
    require_site_context = True
    raise_exception = True

    def post(self, request, *args, **kwargs):
        try:
            normalized = SubjectFormVerificationRequestValidator.parse_query_thread_action(
                request.body
            )
            page_state_id = get_page_state_id_for_subject_visit_crf(
                subject_id=int(kwargs["subject_id"]),
                visit_id=int(kwargs["visit_id"]),
                crf_template_id=int(kwargs["crf_template_id"]),
                **_event_form_binding_kwargs_from_request(request),
            )
            if page_state_id is None:
                return JsonResponse({"error": ["Page state not found."]}, status=400)
            action = str(normalized.get("action") or "").strip().lower()
            permission_code = QUERY_ACTION_PERMISSION_BY_ACTION.get(action)
            if permission_code is not None:
                page_context = get_page_state_contexts(page_state_ids=[int(page_state_id)]).get(int(page_state_id))
                if not user_can_access_permission(
                    request.user,
                    permission_code,
                    study_id=int(kwargs["study_id"]),
                    site_id=getattr(page_context, "site_id", None),
                ):
                    return JsonResponse({"error": ["Permission denied."]}, status=403)
            if action == "cancel":
                result = cancel_reconcile_dataquery(
                    dataquery_id=int(normalized["dataquery_id"]),
                    page_state_id=int(page_state_id),
                    field_template_id=int(normalized["field_template_id"]),
                    message_text=str(normalized["message_text"]),
                    actor_user_id=getattr(request.user, "id", None),
                )
            elif normalized["cancel_query"]:
                result = cancel_reconcile_query(
                    dataquery_id=int(normalized["dataquery_id"]),
                    page_state_id=int(page_state_id),
                    field_template_id=int(normalized["field_template_id"]),
                    actor_user_id=getattr(request.user, "id", None),
                )
            elif action == "request_clarification":
                result = request_clarification_reconcile_query(
                    dataquery_id=int(normalized["dataquery_id"]),
                    page_state_id=int(page_state_id),
                    field_template_id=int(normalized["field_template_id"]),
                    message_text=str(normalized["message_text"]),
                    actor_user_id=getattr(request.user, "id", None),
                )
            elif action == "resolve":
                result = resolve_reconcile_query(
                    dataquery_id=int(normalized["dataquery_id"]),
                    page_state_id=int(page_state_id),
                    field_template_id=int(normalized["field_template_id"]),
                    message_text=str(normalized["message_text"]),
                    actor_user_id=getattr(request.user, "id", None),
                )
            elif normalized["close_query"] or action == "close":
                result = close_reconcile_query(
                    dataquery_id=int(normalized["dataquery_id"]),
                    page_state_id=int(page_state_id),
                    field_template_id=int(normalized["field_template_id"]),
                    message_text=str(normalized["message_text"]),
                    actor_user_id=getattr(request.user, "id", None),
                )
            elif action == "reopen":
                result = reopen_reconcile_query(
                    dataquery_id=int(normalized["dataquery_id"]),
                    page_state_id=int(page_state_id),
                    field_template_id=int(normalized["field_template_id"]),
                    message_text=str(normalized["message_text"]),
                    actor_user_id=getattr(request.user, "id", None),
                )
            else:
                result = reply_to_reconcile_query(
                    dataquery_id=int(normalized["dataquery_id"]),
                    page_state_id=int(page_state_id),
                    field_template_id=int(normalized["field_template_id"]),
                    message_text=str(normalized["message_text"]),
                    actor_user_id=getattr(request.user, "id", None),
                )
        except (SubjectValidationError, DataCaptureValidationError, ValueError) as exc:
            messages = list(exc.messages) if hasattr(exc, "messages") else [str(exc)]
            return JsonResponse({"error": messages}, status=400)
        if result.get("changed") is False:
            return JsonResponse({"error": ["Action is not allowed for current query status."]}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "dataquery_id": result["dataquery_id"],
                "message_text": result.get("message_text", ""),
                "message_type": result.get("message_type", ""),
                "created_at": result.get("created_at", ""),
                "closed": result.get("closed", result.get("status") == "closed"),
                "status": result.get("status", ""),
                "cancelled": result.get("cancelled", False),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class SubjectFormVerificationOpenQueryView(
    LoginRequiredMixin,
    ContextPermissionRequiredMixin,
    SubjectAbstractVerifyStudy,
    View,
):
    permission_required = "SDV.MARK"
    authorization_scope = "STUDY_SITE"
    require_site_context = True
    raise_exception = True

    def post(self, request, *args, **kwargs):
        try:
            normalized = SubjectFormVerificationRequestValidator.parse_open_query_action(
                request.body
            )
            page_state_id = get_page_state_id_for_subject_visit_crf(
                subject_id=int(kwargs["subject_id"]),
                visit_id=int(kwargs["visit_id"]),
                crf_template_id=int(kwargs["crf_template_id"]),
                **_event_form_binding_kwargs_from_request(request),
            )
            if page_state_id is None:
                return JsonResponse({"error": ["Page state not found."]}, status=400)
            page_state_status = get_page_state_status_for_subject_visit_crf(
                subject_id=int(kwargs["subject_id"]),
                visit_id=int(kwargs["visit_id"]),
                crf_template_id=int(kwargs["crf_template_id"]),
                **_event_form_binding_kwargs_from_request(request),
            )
            if (page_state_status or "").strip().lower() not in {
                DataCapturePageState.SUBMITTED,
                DataCapturePageState.VERIFIED,
            }:
                return JsonResponse(
                    {"error": ["Chỉ được tạo Query khi Page State ở trạng thái Submitted hoặc Verified."]},
                    status=400,
                )
            result = open_reconcile_query(
                page_state_id=int(page_state_id),
                field_template_id=int(normalized["field_template_id"]),
                field_key=str(normalized.get("field_key") or ""),
                message_text=str(normalized["message_text"]),
                actor_user_id=getattr(request.user, "id", None),
            )
        except (SubjectValidationError, DataCaptureValidationError, ValueError) as exc:
            messages = list(exc.messages) if hasattr(exc, "messages") else [str(exc)]
            return JsonResponse({"error": messages}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "dataquery_id": result["dataquery_id"],
                "field_template_id": result["field_template_id"],
                "message_text": result["message_text"],
                "message_type": result["message_type"],
                "created_at": result["created_at"],
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class SubjectValidationIssueAcknowledgeView(
    LoginRequiredMixin,
    ContextPermissionRequiredMixin,
    SubjectAbstractVerifyStudy,
    View,
):
    permission_required = "VALIDATION_ISSUE.ACKNOWLEDGE"
    authorization_scope = "STUDY_SITE"
    require_site_context = True
    raise_exception = True

    def post(self, request, *args, **kwargs):
        try:
            normalized = SubjectFormVerificationRequestValidator.parse_validation_issue_acknowledgements(
                request.body
            )
            page_state_id = get_page_state_id_for_subject_visit_crf(
                subject_id=int(kwargs["subject_id"]),
                visit_id=int(kwargs["visit_id"]),
                crf_template_id=int(kwargs["crf_template_id"]),
                **_event_form_binding_kwargs_from_request(request),
            )
            if page_state_id is None:
                return JsonResponse({"error": ["Page state not found."]}, status=400)
            result = acknowledge_reconcile_validation_issues(
                page_state_id=int(page_state_id),
                issues=normalized,
                actor_user_id=getattr(request.user, "id", None),
                current_failures=get_current_validation_failures_for_acknowledgement(
                    subject_id=int(kwargs["subject_id"]), visit_id=int(kwargs["visit_id"]),
                    crf_template_id=int(kwargs["crf_template_id"]),
                    **_event_form_binding_kwargs_from_request(request),
                ),
            )
        except (SubjectValidationError, DataCaptureValidationError, ValueError) as exc:
            messages = list(exc.messages) if hasattr(exc, "messages") else [str(exc)]
            return JsonResponse({"error": messages}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "acknowledged_issue_ids": result["acknowledged_issue_ids"],
                "acknowledged_count": result["acknowledged_count"],
                "page_status": get_page_state_status_for_subject_visit_crf(
                    subject_id=int(kwargs["subject_id"]),
                    visit_id=int(kwargs["visit_id"]),
                    crf_template_id=int(kwargs["crf_template_id"]),
                    **_event_form_binding_kwargs_from_request(request),
                )
                or "",
            }
        )


__all__ = [
    "SubjectFormVerificationCertifyPageView",
    "SubjectFormVerificationFinalizePageDataView",
    "SubjectFormVerificationLockPageView",
    "SubjectFormVerificationOpenQueryView",
    "SubjectFormVerificationQueryThreadView",
    "SubjectFormVerificationReopenView",
    "SubjectFormVerificationVerifyCheckedView",
    "SubjectValidationIssueAcknowledgeView",
]
