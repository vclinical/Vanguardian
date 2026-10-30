from django.db import transaction

from apps.reconcile.application import (
    ReconcileChangeReasonItem,
    ReconcileDataQueryReadService,
    ReconcileDataQueryWriteService,
)


def create_data_queries_for_page_change_reasons(
    *,
    page_state_id: int,
    crf_template_id: int,
    reasons: list[dict[str, str]],
    actor_user_id: int | None,
) -> int:
    reason_items = [
        ReconcileChangeReasonItem(
            field_key=str(item.get("field_key") or "").strip(),
            field_label=str(item.get("field_label") or "").strip(),
            reason=str(item.get("reason") or "").strip(),
        )
        for item in reasons
        if isinstance(item, dict)
    ]
    return ReconcileDataQueryWriteService().create_change_reason_data_queries(
        page_state_id=page_state_id,
        crf_template_id=crf_template_id,
        reasons=reason_items,
        actor_user_id=actor_user_id,
    )


def create_reconcile_records_for_validation_failures(
    *,
    page_state_id: int,
    failures: list[object],
    actor_user_id: int | None,
) -> dict[str, int]:
    return ReconcileDataQueryWriteService().create_validation_failure_records(
        page_state_id=page_state_id,
        failures=failures,
        actor_user_id=actor_user_id,
    )


def list_open_reconcile_validation_issues_by_fields(
    *,
    page_state_id: int,
    field_template_ids: tuple[int, ...],
) -> dict[int, list[dict[str, object]]]:
    return ReconcileDataQueryReadService().list_open_validation_issues_by_page_state_and_field_templates(
        page_state_id=page_state_id,
        field_template_ids=field_template_ids,
    )


def has_open_reconcile_validation_issue_for_page_field(*, page_state_id: int, field_template_id: int) -> bool:
    return ReconcileDataQueryReadService().has_open_validation_issue_for_page_field(
        page_state_id=page_state_id,
        field_template_id=field_template_id,
    )


def list_field_template_ids_with_reconcile_validation_issues(
    *,
    page_state_id: int,
    field_template_ids: tuple[int, ...],
) -> set[int]:
    return ReconcileDataQueryReadService().list_field_template_ids_with_validation_issues(
        page_state_id=page_state_id,
        field_template_ids=field_template_ids,
    )


@transaction.atomic
def acknowledge_reconcile_validation_issues(
    *,
    page_state_id: int,
    issues: list[dict[str, object]],
    actor_user_id: int | None,
    current_failures: list[object] | None = None,
) -> dict[str, object]:
    return ReconcileDataQueryWriteService().acknowledge_validation_issues(
        page_state_id=page_state_id,
        issues=issues,
        actor_user_id=actor_user_id,
        **({"current_failures": current_failures} if current_failures is not None else {}),
    )


def open_reconcile_query(
    *,
    page_state_id: int,
    field_template_id: int,
    message_text: str,
    actor_user_id: int | None,
    field_key: str = "",
) -> dict[str, object]:
    return ReconcileDataQueryWriteService().open_query(
        page_state_id=page_state_id,
        field_template_id=field_template_id,
        field_key=field_key,
        message_text=message_text,
        actor_user_id=actor_user_id,
    )


def has_verified_reconcile_query_for_page_field(*, page_state_id: int, field_template_id: int) -> bool:
    return ReconcileDataQueryReadService().has_verified_query_for_page_field(
        page_state_id=page_state_id,
        field_template_id=field_template_id,
    )


def list_field_template_ids_with_reconcile_queries(
    *,
    page_state_id: int,
    field_template_ids: tuple[int, ...],
) -> set[int]:
    return ReconcileDataQueryReadService().list_field_template_ids_with_queries(
        page_state_id=page_state_id,
        field_template_ids=field_template_ids,
    )


def summarize_reconcile_workbench_for_page_states(*, page_state_ids: tuple[int, ...]) -> dict[str, int]:
    return ReconcileDataQueryReadService().summarize_workbench(page_state_ids=page_state_ids)


def list_page_state_ids_with_open_reconcile_workbench_items(
    *,
    page_state_ids: tuple[int, ...],
) -> set[int]:
    return ReconcileDataQueryReadService().list_page_state_ids_with_open_workbench_items(
        page_state_ids=page_state_ids,
    )


def reply_to_reconcile_query(
    *,
    dataquery_id: int,
    page_state_id: int,
    field_template_id: int | None,
    message_text: str,
    actor_user_id: int | None,
) -> dict[str, object]:
    return ReconcileDataQueryWriteService().reply_to_query(
        dataquery_id=dataquery_id,
        page_state_id=page_state_id,
        field_template_id=field_template_id,
        message_text=message_text,
        actor_user_id=actor_user_id,
    )


def resolve_reconcile_query(
    *,
    dataquery_id: int,
    page_state_id: int,
    field_template_id: int | None,
    message_text: str,
    actor_user_id: int | None,
) -> dict[str, object]:
    return ReconcileDataQueryWriteService().resolve_query(
        dataquery_id=dataquery_id,
        page_state_id=page_state_id,
        field_template_id=field_template_id,
        message_text=message_text,
        actor_user_id=actor_user_id,
    )


def close_reconcile_query(
    *,
    dataquery_id: int,
    page_state_id: int,
    field_template_id: int | None,
    message_text: str,
    actor_user_id: int | None,
) -> dict[str, object]:
    return ReconcileDataQueryWriteService().close_resolved_query(
        dataquery_id=dataquery_id,
        page_state_id=page_state_id,
        field_template_id=field_template_id,
        message_text=message_text,
        actor_user_id=actor_user_id,
    )


def reopen_reconcile_query(
    *,
    dataquery_id: int,
    page_state_id: int,
    field_template_id: int | None,
    message_text: str,
    actor_user_id: int | None,
) -> dict[str, object]:
    return ReconcileDataQueryWriteService().reopen_query(
        dataquery_id=dataquery_id,
        page_state_id=page_state_id,
        field_template_id=field_template_id,
        message_text=message_text,
        actor_user_id=actor_user_id,
    )


def request_clarification_reconcile_query(
    *,
    dataquery_id: int,
    page_state_id: int,
    field_template_id: int | None,
    message_text: str,
    actor_user_id: int | None,
) -> dict[str, object]:
    return ReconcileDataQueryWriteService().request_clarification(
        dataquery_id=dataquery_id,
        page_state_id=page_state_id,
        field_template_id=field_template_id,
        message_text=message_text,
        actor_user_id=actor_user_id,
    )


def cancel_reconcile_dataquery(
    *,
    dataquery_id: int,
    page_state_id: int,
    field_template_id: int | None,
    message_text: str,
    actor_user_id: int | None,
) -> dict[str, object]:
    return ReconcileDataQueryWriteService().cancel_dataquery(
        dataquery_id=dataquery_id,
        page_state_id=page_state_id,
        field_template_id=field_template_id,
        message_text=message_text,
        actor_user_id=actor_user_id,
    )


def get_reconcile_query_action_scope(*, dataquery_id: int) -> dict[str, object] | None:
    return ReconcileDataQueryWriteService().query_action_scope(dataquery_id=dataquery_id)


def get_reconcile_query_site_id(*, study_id: int, dataquery_id: int) -> int | None:
    from apps.datacapture.public import get_page_state_contexts

    scope = get_reconcile_query_action_scope(dataquery_id=dataquery_id)
    if scope is None:
        return None
    page_state_id = scope.get("page_state_id")
    if page_state_id is None:
        return None
    page_context = get_page_state_contexts(page_state_ids=[int(page_state_id)]).get(int(page_state_id))
    if page_context is None or int(getattr(page_context, "study_id", 0) or 0) != int(study_id):
        return None
    return getattr(page_context, "site_id", None)


def cancel_reconcile_query(
    *,
    dataquery_id: int,
    page_state_id: int,
    field_template_id: int,
    actor_user_id: int | None,
) -> dict[str, object]:
    return ReconcileDataQueryWriteService().cancel_query(
        dataquery_id=dataquery_id,
        page_state_id=page_state_id,
        field_template_id=field_template_id,
        actor_user_id=actor_user_id,
    )


__all__ = [
    "acknowledge_reconcile_validation_issues",
    "cancel_reconcile_dataquery",
    "cancel_reconcile_query",
    "close_reconcile_query",
    "create_data_queries_for_page_change_reasons",
    "create_reconcile_records_for_validation_failures",
    "get_reconcile_query_action_scope",
    "get_reconcile_query_site_id",
    "list_field_template_ids_with_reconcile_queries",
    "list_field_template_ids_with_reconcile_validation_issues",
    "list_page_state_ids_with_open_reconcile_workbench_items",
    "has_open_reconcile_validation_issue_for_page_field",
    "has_verified_reconcile_query_for_page_field",
    "list_open_reconcile_validation_issues_by_fields",
    "open_reconcile_query",
    "reopen_reconcile_query",
    "request_clarification_reconcile_query",
    "resolve_reconcile_query",
    "reply_to_reconcile_query",
    "summarize_reconcile_workbench_for_page_states",
]
