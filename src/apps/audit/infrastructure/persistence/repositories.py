from django.utils import timezone

from apps.audit.infrastructure.persistence.models import AuditEvent


class DjangoAuditEventRepository:
    @staticmethod
    def list_file_import_events(*, visit_id=None, visit_ids=()):
        identifiers = [str(visit_id)] if visit_id is not None else [str(value) for value in visit_ids]
        return list(AuditEvent.objects.filter(
            action="file_import", object_type="subject_event", object_id__in=identifiers, deleted=False,
        ).order_by("created_at", "id").values(
            "id", "object_id", "created_at", "created_by_id", "created_by__username", "created_by__display_name", "after_data",
        ))

    def create(
        self,
        *,
        action,
        object_type,
        object_id,
        before_data,
        after_data,
        ip_address,
        user_agent,
        user_id,
        created_by_id,
        updated_by_id,
    ):
        now = timezone.now()
        return AuditEvent.objects.create(
            created_at=now,
            updated_at=now,
            deleted=False,
            action=action,
            object_type=object_type,
            object_id=object_id,
            before_data=before_data,
            after_data=after_data,
            ip_address=ip_address,
            user_agent=user_agent,
            user_id=user_id,
            created_by_id=created_by_id,
            updated_by_id=updated_by_id,
        )
