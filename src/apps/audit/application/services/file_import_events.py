from apps.audit.infrastructure.repositories import DjangoAuditEventRepository


def list_file_import_events(*, visit_id=None, visit_ids=()):
    return DjangoAuditEventRepository.list_file_import_events(visit_id=visit_id, visit_ids=visit_ids)
