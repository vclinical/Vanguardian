from apps.audit.application import RecordAuditEventCommand, RecordAuditEventService
from apps.audit.application.services.file_import_events import list_file_import_events


def build_audit_request_context(request, *, actor_user_id=None):
    return {
        "actor_user_id": actor_user_id or AuditContextAdapter.resolve_actor_user_id(request),
        "ip_address": AuditContextAdapter.resolve_ip_address(request),
        "user_agent": AuditContextAdapter.resolve_user_agent(request),
    }


class AuditContextAdapter:
    def __init__(self, record_audit_event_service=None):
        self.record_audit_event_service = record_audit_event_service or RecordAuditEventService()

    def record_event(
        self,
        *,
        action,
        object_type,
        object_id,
        request=None,
        before_data=None,
        after_data=None,
        user_id=None,
        actor_user_id=None,
        ip_address=None,
        user_agent=None,
    ):
        command = RecordAuditEventCommand(
            action=action,
            object_type=object_type,
            object_id=object_id,
            before_data=before_data,
            after_data=after_data,
            user_id=user_id,
            actor_user_id=actor_user_id or self.resolve_actor_user_id(request),
            ip_address=ip_address if ip_address is not None else self.resolve_ip_address(request),
            user_agent=user_agent if user_agent is not None else self.resolve_user_agent(request),
        )
        return self.record_audit_event_service.execute(command)

    @staticmethod
    def resolve_actor_user_id(request):
        request_user = getattr(request, "user", None)
        if request_user is None or not getattr(request_user, "is_authenticated", False):
            return None
        return request_user.pk

    @staticmethod
    def resolve_ip_address(request):
        if request is None:
            return None

        forwarded_for = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()[:39] or None

        return ((request.META.get("REMOTE_ADDR") or "").strip()[:39]) or None

    @staticmethod
    def resolve_user_agent(request):
        if request is None:
            return ""
        return (request.META.get("HTTP_USER_AGENT") or "").strip()[:255]


__all__ = ["AuditContextAdapter", "build_audit_request_context", "list_file_import_events"]
