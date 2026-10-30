from django.db.models import Count, Max, Q
from django.db.models.functions import Coalesce

from apps.reconcile.models import (
    ReconcileDataQuery,
    ReconcileDataQueryStatusChoices,
    ReconcileQueryThread,
    ReconcileValidationIssue,
    ReconcileValidationIssueSnapshot,
    ReconcileValidationIssueStatusChoices,
)


class DjangoReconcileDataQueryReadRepository:
    OPEN_VALIDATION_ISSUE_STATUSES = (
        ReconcileValidationIssueStatusChoices.OPEN,
        ReconcileValidationIssueStatusChoices.ACKNOWLEDGEMENT_REQUIRED,
    )
    WORKBENCH_OPEN_VALIDATION_ISSUE_STATUSES = (
        ReconcileValidationIssueStatusChoices.OPEN,
        ReconcileValidationIssueStatusChoices.ACKNOWLEDGEMENT_REQUIRED,
        ReconcileValidationIssueStatusChoices.QUERY_CREATED,
    )

    @staticmethod
    def _active_status_excludes() -> tuple[str, ...]:
        return ("cancelled", "closed")

    @staticmethod
    def _active_query_statuses() -> tuple[str, ...]:
        return (
            ReconcileDataQueryStatusChoices.OPEN,
            ReconcileDataQueryStatusChoices.ANSWERED,
            ReconcileDataQueryStatusChoices.RESOLVED,
        )

    def count_open_queries_by_page_state_and_field_templates(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> dict[int, int]:
        if not field_template_ids:
            return {}
        rows = (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                status__in=self._active_query_statuses(),
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .values("field_template_id")
            .annotate(query_count=Count("id"))
        )
        return {int(row["field_template_id"]): int(row["query_count"]) for row in rows}

    def list_open_validation_issues_by_page_state_and_field_templates(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> dict[int, list[dict[str, object]]]:
        if not field_template_ids:
            return {}
        rows = (
            ReconcileValidationIssue.objects.filter(
                form_instance_id=page_state_id,
                status__in=self.OPEN_VALIDATION_ISSUE_STATUSES,
            )
            .filter(
                Q(field_instance__field_template_id__in=field_template_ids)
                | Q(field_instance_id__isnull=True, rule__field_template_id__in=field_template_ids)
            )
            .values(
                "id",
                "rule_id",
                "field_instance_id",
                "field_instance__field_template_id",
                "rule__field_template_id",
                "mode",
                "severity",
                "status",
                "message",
                "failed_value",
                "created_at",
            )
        )
        grouped: dict[int, list[dict[str, object]]] = {}
        seen_ids: set[int] = set()
        for row in rows:
            issue_id = int(row["id"])
            if issue_id in seen_ids:
                continue
            seen_ids.add(issue_id)
            field_template_id = row["field_instance__field_template_id"] or row["rule__field_template_id"]
            if field_template_id is None:
                continue
            grouped.setdefault(int(field_template_id), []).append(
                {
                    "id": issue_id,
                    "rule_id": row["rule_id"],
                    "field_instance_id": row["field_instance_id"],
                    "mode": row["mode"],
                    "severity": row["severity"],
                    "status": row["status"],
                    "message": row["message"],
                    "failed_value": row["failed_value"],
                    "created_at": row["created_at"],
                }
            )
        for issues in grouped.values():
            issues.sort(key=lambda item: (item.get("created_at") is None, item.get("created_at"), item.get("id")))
        return grouped

    def list_validation_issue_histories_by_page_state_and_field_templates(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> dict[int, list[dict[str, object]]]:
        if not field_template_ids:
            return {}
        rows = (
            ReconcileValidationIssueSnapshot.objects.filter(
                validation_issue__form_instance_id=page_state_id,
            )
            .filter(
                Q(validation_issue__field_instance__field_template_id__in=field_template_ids)
                | Q(
                    validation_issue__field_instance_id__isnull=True,
                    validation_issue__rule__field_template_id__in=field_template_ids,
                )
            )
            .order_by(
                "validation_issue__field_instance__field_template_id",
                "validation_issue__rule__field_template_id",
                "-created_at",
                "-id",
            )
            .values(
                "id",
                "validation_issue_id",
                "validation_issue__rule_id",
                "validation_issue__field_instance_id",
                "validation_issue__field_instance__field_template_id",
                "validation_issue__rule__field_template_id",
                "validation_issue__mode",
                "result",
                "evaluated_values_json",
                "validation_run_id",
                "validation_run__source",
                "validation_run__triggered_by",
                "severity",
                "message",
                "created_at",
            )
        )
        grouped: dict[int, list[dict[str, object]]] = {}
        for row in rows:
            field_template_id = (
                row["validation_issue__field_instance__field_template_id"]
                or row["validation_issue__rule__field_template_id"]
            )
            if field_template_id is None:
                continue
            grouped.setdefault(int(field_template_id), []).append(
                {
                    "id": int(row["id"]),
                    "validation_issue_id": (
                        int(row["validation_issue_id"]) if row["validation_issue_id"] is not None else None
                    ),
                    "rule_id": (
                        int(row["validation_issue__rule_id"])
                        if row["validation_issue__rule_id"] is not None
                        else None
                    ),
                    "field_instance_id": row["validation_issue__field_instance_id"],
                    "mode": row["validation_issue__mode"],
                    "result": row["result"],
                    "evaluated_value": (
                        row["evaluated_values_json"].get("value")
                        if isinstance(row["evaluated_values_json"], dict) and "_validation_context" in row["evaluated_values_json"]
                        else row["evaluated_values_json"]
                    ),
                    "acknowledgment_preserved": (
                        bool(row["evaluated_values_json"].get("acknowledged"))
                        if isinstance(row["evaluated_values_json"], dict) and "_validation_context" in row["evaluated_values_json"]
                        else False
                    ),
                    "validation_run_id": row["validation_run_id"],
                    "source": row["validation_run__source"],
                    "triggered_by": row["validation_run__triggered_by"],
                    "severity": row["severity"],
                    "message": row["message"],
                    "created_at": row["created_at"],
                }
            )
        return grouped

    def has_open_validation_issue_for_page_field(self, *, page_state_id: int, field_template_id: int) -> bool:
        return (
            ReconcileValidationIssue.objects.filter(
                form_instance_id=page_state_id,
                status__in=self.OPEN_VALIDATION_ISSUE_STATUSES,
            )
            .filter(
                Q(field_instance__field_template_id=field_template_id)
                | Q(field_instance_id__isnull=True, rule__field_template_id=field_template_id)
            )
            .exists()
        )

    def list_field_template_ids_with_validation_issues(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> set[int]:
        if not field_template_ids:
            return set()
        rows = (
            ReconcileValidationIssue.objects.filter(form_instance_id=page_state_id)
            .filter(
                Q(field_instance__field_template_id__in=field_template_ids)
                | Q(field_instance_id__isnull=True, rule__field_template_id__in=field_template_ids)
            )
            .annotate(resolved_field_template_id=Coalesce("field_instance__field_template_id", "rule__field_template_id"))
            .values_list("resolved_field_template_id", flat=True)
            .distinct()
        )
        return {
            int(field_template_id)
            for field_template_id in rows
            if field_template_id is not None
        }

    def list_field_template_ids_with_verified_queries(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> set[int]:
        if not field_template_ids:
            return set()
        rows = (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                status="verified",
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .values_list("field_template_id", flat=True)
            .distinct()
        )
        return {int(field_template_id) for field_template_id in rows}

    def list_field_template_ids_with_queries(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> set[int]:
        if not field_template_ids:
            return set()
        rows = (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .values_list("field_template_id", flat=True)
            .distinct()
        )
        return {int(field_template_id) for field_template_id in rows}

    @staticmethod
    def _normalize_field_path(value: object) -> str:
        return str(value or "").strip()

    def list_verified_query_keys_by_page_state_and_field_templates(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> set[tuple[int, str]]:
        if not field_template_ids:
            return set()
        rows = (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                status="verified",
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .values("field_template_id", "field_path")
            .distinct()
        )
        return {
            (int(row["field_template_id"]), self._normalize_field_path(row["field_path"]))
            for row in rows
        }

    def count_open_queries_by_page_state_field_paths(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> dict[tuple[int, str], int]:
        if not field_template_ids:
            return {}
        rows = (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                status=ReconcileDataQueryStatusChoices.OPEN,
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .values("field_template_id", "field_path")
            .annotate(query_count=Count("id"))
        )
        return {
            (int(row["field_template_id"]), self._normalize_field_path(row["field_path"])): int(row["query_count"])
            for row in rows
        }

    def list_latest_active_query_contexts_by_page_state_and_field_templates(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> dict[tuple[int, str], dict[str, object]]:
        if not field_template_ids:
            return {}
        rows = (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                status=ReconcileDataQueryStatusChoices.OPEN,
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .annotate(sort_at=Coalesce("opened_at", "created_at"))
            .order_by("field_template_id", "field_path", "-sort_at", "-id")
            .values(
                "id",
                "field_template_id",
                "field_path",
                "opened_by_id",
                "assigned_to_id",
                "status",
                "answered_at",
                "answered_by_id",
            )
        )
        out: dict[tuple[int, str], dict[str, object]] = {}
        for row in rows:
            key = (int(row["field_template_id"]), self._normalize_field_path(row["field_path"]))
            out.setdefault(
                key,
                {
                    "active_query_id": int(row["id"]),
                    "opened_by_id": row["opened_by_id"],
                    "assigned_to_id": row["assigned_to_id"],
                    "active_query_status": row["status"],
                    "active_query_is_answered": (
                        row["answered_at"] is not None or row["answered_by_id"] is not None
                    ),
                },
            )
        return out

    def list_latest_query_messages_by_dataquery_ids(
        self,
        *,
        dataquery_ids: tuple[int, ...],
        limit_per_query: int = 10,
    ) -> dict[int, list[dict[str, object]]]:
        if not dataquery_ids:
            return {}
        rows = (
            ReconcileQueryThread.objects.filter(
                dataquery_id__in=dataquery_ids,
                deleted=False,
            )
            .order_by("dataquery_id", "-created_at", "-id")
            .values(
                "dataquery_id",
                "message_text",
                "message_type",
                "created_at",
                "author_id",
            )
        )
        grouped: dict[int, list[dict[str, object]]] = {}
        for row in rows:
            dataquery_id = int(row["dataquery_id"])
            bucket = grouped.setdefault(dataquery_id, [])
            if len(bucket) >= limit_per_query:
                continue
            bucket.append(
                {
                    "dataquery_id": dataquery_id,
                    "text": row["message_text"],
                    "status": row["message_type"],
                    "created_at": row["created_at"],
                    "opened_by_id": row["author_id"],
                },
            )
        return grouped

    def count_query_threads_since_current_user_last_comment_by_dataquery_ids(
        self,
        *,
        dataquery_ids: tuple[int, ...],
        current_user_id: int | None,
    ) -> dict[int, int]:
        if not dataquery_ids or current_user_id is None:
            return {}
        rows = (
            ReconcileQueryThread.objects.filter(
                dataquery_id__in=dataquery_ids,
                deleted=False,
            )
            .order_by("dataquery_id", "created_at", "id")
            .values("dataquery_id", "created_at", "author_id", "created_by_id")
        )
        latest_current_user_comment_by_query: dict[int, object] = {}
        buffered_rows = list(rows)
        for row in buffered_rows:
            if row["author_id"] == current_user_id or row["created_by_id"] == current_user_id:
                latest_current_user_comment_by_query[int(row["dataquery_id"])] = row["created_at"]

        counts: dict[int, int] = {}
        for row in buffered_rows:
            dataquery_id = int(row["dataquery_id"])
            last_current_user_comment_at = latest_current_user_comment_by_query.get(dataquery_id)
            is_current_user_comment = row["author_id"] == current_user_id or row["created_by_id"] == current_user_id
            if is_current_user_comment:
                continue
            if last_current_user_comment_at is not None and row["created_at"] <= last_current_user_comment_at:
                continue
            counts[dataquery_id] = counts.get(dataquery_id, 0) + 1
        return counts

    def has_verified_query_for_page_field(self, *, page_state_id: int, field_template_id: int) -> bool:
        return ReconcileDataQuery.objects.filter(
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            deleted=False,
            status="verified",
        ).exists()

    def has_open_query_for_page_field(self, *, page_state_id: int, field_template_id: int) -> bool:
        return ReconcileDataQuery.objects.filter(
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            deleted=False,
            status=ReconcileDataQueryStatusChoices.OPEN,
        ).exists()

    def has_active_blocking_query_for_page_field(self, *, page_state_id: int, field_template_id: int) -> bool:
        return (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                deleted=False,
                is_blocking=True,
            )
            .exclude(status__in=self._active_status_excludes())
            .exists()
        )

    def has_active_blocking_query_for_page(self, *, page_state_id: int) -> bool:
        return (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                is_blocking=True,
            )
            .exclude(status__in=self._active_status_excludes())
            .exists()
        )

    def has_unclosed_query_for_page(self, *, page_state_id: int) -> bool:
        return (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
            )
            .exclude(status__in=self._active_status_excludes())
            .exists()
        )

    def list_latest_query_messages_by_page_state_and_field_templates(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
        limit_per_field: int = 10,
    ) -> dict[int, list[dict[str, object]]]:
        if not field_template_ids:
            return {}
        open_query_ids_by_field = self.list_latest_active_query_ids_by_page_state_and_field_templates(
            page_state_id=page_state_id,
            field_template_ids=field_template_ids,
        )
        field_id_by_query_id = {query_id: field_id for field_id, query_id in open_query_ids_by_field.items()}
        if not field_id_by_query_id:
            return {}
        rows = (
            ReconcileQueryThread.objects.filter(
                dataquery_id__in=tuple(field_id_by_query_id),
                deleted=False,
            )
            .order_by("dataquery_id", "-created_at", "-id")
            .values(
                "dataquery_id",
                "message_text",
                "message_type",
                "created_at",
                "author_id",
            )
        )
        grouped: dict[int, list[dict[str, object]]] = {}
        for row in rows:
            field_template_id = field_id_by_query_id[int(row["dataquery_id"])]
            bucket = grouped.setdefault(field_template_id, [])
            if len(bucket) >= limit_per_field:
                continue
            bucket.append(
                {
                    "dataquery_id": row["dataquery_id"],
                    "text": row["message_text"],
                    "status": row["message_type"],
                    "created_at": row["created_at"],
                    "opened_by_id": row["author_id"],
                },
            )
        return grouped

    def list_closed_query_histories_by_page_state_and_field_templates(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
        limit_per_query: int = 10,
    ) -> dict[int, list[dict[str, object]]]:
        if not field_template_ids:
            return {}
        query_rows = list(
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                status=ReconcileDataQueryStatusChoices.CLOSED,
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .annotate(sort_at=Coalesce("closed_at", "updated_at", "created_at"))
            .order_by("field_template_id", "-sort_at", "-id")
            .values(
                "id",
                "field_template_id",
                "status",
                "question_text",
                "opened_at",
                "closed_at",
                "created_at",
                "updated_at",
            )
        )
        query_ids = tuple(int(row["id"]) for row in query_rows)
        messages_by_query_id: dict[int, list[dict[str, object]]] = {}
        if query_ids:
            thread_rows = (
                ReconcileQueryThread.objects.filter(
                    dataquery_id__in=query_ids,
                    deleted=False,
                )
                .order_by("dataquery_id", "-created_at", "-id")
                .values(
                    "dataquery_id",
                    "message_text",
                    "message_type",
                    "created_at",
                    "author_id",
                )
            )
            for row in thread_rows:
                dataquery_id = int(row["dataquery_id"])
                bucket = messages_by_query_id.setdefault(dataquery_id, [])
                if len(bucket) >= limit_per_query:
                    continue
                bucket.append(
                    {
                        "dataquery_id": dataquery_id,
                        "text": row["message_text"],
                        "status": row["message_type"],
                        "created_at": row["created_at"],
                        "opened_by_id": row["author_id"],
                    },
                )

        grouped: dict[int, list[dict[str, object]]] = {}
        for row in query_rows:
            dataquery_id = int(row["id"])
            field_template_id = int(row["field_template_id"])
            grouped.setdefault(field_template_id, []).append(
                {
                    "dataquery_id": dataquery_id,
                    "status": row["status"],
                    "question_text": row["question_text"],
                    "opened_at": row["opened_at"],
                    "closed_at": row["closed_at"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "messages": messages_by_query_id.get(dataquery_id, []),
                },
            )
        return grouped

    def list_closed_query_histories_by_page_state_field_paths(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
        limit_per_query: int = 10,
    ) -> dict[tuple[int, str], list[dict[str, object]]]:
        if not field_template_ids:
            return {}
        query_rows = list(
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                status=ReconcileDataQueryStatusChoices.CLOSED,
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .annotate(sort_at=Coalesce("closed_at", "updated_at", "created_at"))
            .order_by("field_template_id", "field_path", "-sort_at", "-id")
            .values(
                "id",
                "field_template_id",
                "field_path",
                "status",
                "question_text",
                "opened_at",
                "closed_at",
                "created_at",
                "updated_at",
            )
        )
        query_ids = tuple(int(row["id"]) for row in query_rows)
        messages_by_query_id = self.list_latest_query_messages_by_dataquery_ids(
            dataquery_ids=query_ids,
            limit_per_query=limit_per_query,
        )

        grouped: dict[tuple[int, str], list[dict[str, object]]] = {}
        for row in query_rows:
            dataquery_id = int(row["id"])
            key = (int(row["field_template_id"]), self._normalize_field_path(row["field_path"]))
            grouped.setdefault(key, []).append(
                {
                    "dataquery_id": dataquery_id,
                    "status": row["status"],
                    "question_text": row["question_text"],
                    "opened_at": row["opened_at"],
                    "closed_at": row["closed_at"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "messages": messages_by_query_id.get(dataquery_id, []),
                },
            )
        return grouped

    def list_latest_active_query_ids_by_page_state_and_field_templates(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> dict[int, int]:
        if not field_template_ids:
            return {}
        rows = (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                status__in=self._active_query_statuses(),
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .annotate(sort_at=Coalesce("opened_at", "created_at"))
            .order_by("field_template_id", "-sort_at", "-id")
            .values("field_template_id", "id")
        )
        out: dict[int, int] = {}
        for row in rows:
            field_template_id = int(row["field_template_id"])
            out.setdefault(field_template_id, int(row["id"]))
        return out

    def list_latest_active_query_participants_by_page_state_and_field_templates(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> dict[int, dict[str, int | None]]:
        if not field_template_ids:
            return {}
        rows = (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                status__in=self._active_query_statuses(),
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .annotate(sort_at=Coalesce("opened_at", "created_at"))
            .order_by("field_template_id", "-sort_at", "-id")
            .values("field_template_id", "opened_by_id", "assigned_to_id", "status")
        )
        out: dict[int, dict[str, int | None]] = {}
        for row in rows:
            field_template_id = int(row["field_template_id"])
            out.setdefault(
                field_template_id,
                {
                    "opened_by_id": row["opened_by_id"],
                    "assigned_to_id": row["assigned_to_id"],
                    "active_query_status": row["status"],
                },
            )
        return out

    def list_latest_active_query_answered_flags_by_page_state_and_field_templates(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> dict[int, bool]:
        if not field_template_ids:
            return {}
        rows = (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                deleted=False,
                status__in=self._active_query_statuses(),
                field_template_id__isnull=False,
                field_template_id__in=field_template_ids,
            )
            .annotate(sort_at=Coalesce("opened_at", "created_at"))
            .order_by("field_template_id", "-sort_at", "-id")
            .values("field_template_id", "answered_at", "answered_by_id")
        )
        out: dict[int, bool] = {}
        for row in rows:
            field_template_id = int(row["field_template_id"])
            if field_template_id in out:
                continue
            out[field_template_id] = row["answered_at"] is not None or row["answered_by_id"] is not None
        return out

    def count_query_threads_since_current_user_last_comment(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
        current_user_id: int | None,
    ) -> dict[int, int]:
        if not field_template_ids or current_user_id is None:
            return {}
        open_query_ids_by_field = self.list_latest_active_query_ids_by_page_state_and_field_templates(
            page_state_id=page_state_id,
            field_template_ids=field_template_ids,
        )
        field_id_by_query_id = {query_id: field_id for field_id, query_id in open_query_ids_by_field.items()}
        if not field_id_by_query_id:
            return {}
        rows = (
            ReconcileQueryThread.objects.filter(
                dataquery_id__in=tuple(field_id_by_query_id),
                deleted=False,
            )
            .order_by("dataquery_id", "created_at", "id")
            .values("dataquery_id", "created_at", "author_id", "created_by_id")
        )
        latest_current_user_comment_by_query: dict[int, object] = {}
        buffered_rows = list(rows)
        for row in buffered_rows:
            if row["author_id"] == current_user_id or row["created_by_id"] == current_user_id:
                latest_current_user_comment_by_query[int(row["dataquery_id"])] = row["created_at"]

        counts: dict[int, int] = {}
        for row in buffered_rows:
            dataquery_id = int(row["dataquery_id"])
            last_current_user_comment_at = latest_current_user_comment_by_query.get(dataquery_id)
            is_current_user_comment = row["author_id"] == current_user_id or row["created_by_id"] == current_user_id
            if is_current_user_comment:
                continue
            if last_current_user_comment_at is not None and row["created_at"] <= last_current_user_comment_at:
                continue
            field_template_id = field_id_by_query_id[dataquery_id]
            counts[field_template_id] = counts.get(field_template_id, 0) + 1
        return counts

    def summarize_workbench(self, *, page_state_ids: tuple[int, ...]) -> dict[str, int]:
        if not page_state_ids:
            return {
                "total": 0,
                "open": 0,
                "awaiting_site_response": 0,
                "awaiting_review": 0,
                "blocking_open": 0,
                "resolved": 0,
                "closed": 0,
                "validation_issues_open": 0,
                "hard_validation_issues_open": 0,
                "actionable_for_current_user": 0,
            }
        inactive_statuses = ("cancelled", "closed", "resolved", "void")
        queryset = ReconcileDataQuery.objects.filter(page_state_id__in=page_state_ids, deleted=False)
        validation_issue_count = ReconcileValidationIssue.objects.filter(
            form_instance_id__in=page_state_ids,
            status__in=self.WORKBENCH_OPEN_VALIDATION_ISSUE_STATUSES,
        ).count()
        hard_validation_issue_count = ReconcileValidationIssue.objects.filter(
            form_instance_id__in=page_state_ids,
            rule__mode="HARD",
            status__in=self.WORKBENCH_OPEN_VALIDATION_ISSUE_STATUSES,
        ).count()
        return {
            "total": queryset.count(),
            "open": queryset.filter(status=ReconcileDataQueryStatusChoices.OPEN).count(),
            "awaiting_site_response": queryset.filter(status="open").count(),
            "awaiting_review": queryset.filter(status=ReconcileDataQueryStatusChoices.ANSWERED).count(),
            "blocking_open": (
                queryset.filter(is_blocking=True)
                .exclude(status__in=inactive_statuses)
                .count()
            ),
            "resolved": queryset.filter(status=ReconcileDataQueryStatusChoices.RESOLVED).count(),
            "closed": queryset.filter(status=ReconcileDataQueryStatusChoices.CLOSED).count(),
            "validation_issues_open": validation_issue_count,
            "hard_validation_issues_open": hard_validation_issue_count,
            "actionable_for_current_user": queryset.filter(status__in=("open", "answered")).count(),
        }

    def list_page_state_ids_with_open_workbench_items(self, *, page_state_ids: tuple[int, ...]) -> set[int]:
        normalized_page_state_ids = tuple(
            dict.fromkeys(int(page_state_id) for page_state_id in page_state_ids or () if page_state_id)
        )
        if not normalized_page_state_ids:
            return set()
        query_page_state_ids = ReconcileDataQuery.objects.filter(
            page_state_id__in=normalized_page_state_ids,
            deleted=False,
            status=ReconcileDataQueryStatusChoices.OPEN,
        ).values_list("page_state_id", flat=True)
        validation_issue_page_state_ids = ReconcileValidationIssue.objects.filter(
            form_instance_id__in=normalized_page_state_ids,
            status__in=self.WORKBENCH_OPEN_VALIDATION_ISSUE_STATUSES,
        ).values_list("form_instance_id", flat=True)
        return {int(page_state_id) for page_state_id in query_page_state_ids} | {
            int(page_state_id) for page_state_id in validation_issue_page_state_ids
        }

    def count_open_queries_assigned_to_user(
        self,
        *,
        page_state_ids: tuple[int, ...],
        user_id: int | None,
    ) -> int:
        if not page_state_ids or user_id is None:
            return 0
        return ReconcileDataQuery.objects.filter(
            page_state_id__in=page_state_ids,
            assigned_to_id=user_id,
            deleted=False,
            status=ReconcileDataQueryStatusChoices.OPEN,
        ).count()

    def count_open_queries_assigned_to_user_for_study_site(
        self,
        *,
        study_id: int | None,
        site_id: int | None,
        user_id: int | None,
    ) -> int:
        if study_id is None or user_id is None:
            return 0
        queryset = ReconcileDataQuery.objects.filter(
            assigned_to_id=user_id,
            deleted=False,
            status=ReconcileDataQueryStatusChoices.OPEN,
            page_state__deleted=False,
            page_state__subject__study_id=study_id,
        )
        if site_id is not None:
            queryset = queryset.filter(page_state__subject__site_id=site_id)
        return queryset.count()

    def list_workbench_queries(
        self,
        *,
        page_state_ids: tuple[int, ...],
        bucket: str = "all",
        search: str = "",
        status: str = "",
        severity: str = "",
        source: str = "",
        blocking: str = "",
        assigned_to_id: int | None = None,
        opened_by_id: int | None = None,
        sort: str = "-last_activity_at",
        can_view_internal_thread: bool = False,
    ) -> list[dict[str, object]]:
        if not page_state_ids:
            return []
        queryset = (
            ReconcileDataQuery.objects.filter(page_state_id__in=page_state_ids, deleted=False)
            .select_related("field_template")
            .prefetch_related("field_template__translations")
            .annotate(
                reply_count=Count(
                    "query_threads",
                    filter=self._visible_thread_filter(can_view_internal_thread),
                    distinct=True,
                ),
                last_thread_at=Max(
                    "query_threads__created_at",
                    filter=self._visible_thread_filter(can_view_internal_thread),
                ),
                sort_last_activity_at=Coalesce("last_thread_at", "closed_at", "resolved_at", "answered_at", "opened_at", "updated_at"),
            )
        )
        queryset = self._apply_workbench_filters(
            queryset,
            bucket=bucket,
            search=search,
            status=status,
            severity=severity,
            source=source,
            blocking=blocking,
            assigned_to_id=assigned_to_id,
            opened_by_id=opened_by_id,
        )
        queryset = queryset.order_by(self._workbench_sort_expression(sort), "-id")
        rows = []
        for query in queryset:
            field_template = query.field_template
            field_label = ""
            if field_template is not None:
                field_label = (
                    field_template.safe_translation_getter("label", default="", any_language=True)
                    if hasattr(field_template, "safe_translation_getter")
                    else ""
                )
            rows.append(
                {
                    "query_id": int(query.pk),
                    "page_state_id": int(query.page_state_id),
                    "status": query.status,
                    "source": query.source,
                    "query_type": query.query_type,
                    "severity": query.severity,
                    "is_blocking": bool(query.is_blocking),
                    "question_text": query.question_text,
                    "resolution_note": query.resolution_note,
                    "field_path": query.field_path,
                    "value_snapshot": query.value_snapshot,
                    "opened_at": query.opened_at,
                    "answered_at": query.answered_at,
                    "resolved_at": query.resolved_at,
                    "closed_at": query.closed_at,
                    "last_activity_at": query.sort_last_activity_at,
                    "reply_count": int(query.reply_count or 0),
                    "assigned_to_id": query.assigned_to_id,
                    "opened_by_id": query.opened_by_id,
                    "field_template_id": query.field_template_id,
                    "field_label": field_label,
                }
            )
        return rows

    def get_workbench_query_detail(
        self,
        *,
        query_id: int,
        can_view_internal_thread: bool = False,
    ) -> dict[str, object] | None:
        query = (
            ReconcileDataQuery.objects.filter(pk=query_id, deleted=False)
            .select_related("field_template")
            .prefetch_related("field_template__translations")
            .first()
        )
        if query is None:
            return None
        rows = self.list_workbench_queries(
            page_state_ids=(int(query.page_state_id),),
            search=str(query.pk),
            can_view_internal_thread=can_view_internal_thread,
        )
        detail = next((row for row in rows if row["query_id"] == int(query.pk)), None)
        if detail is None:
            return None
        detail["threads"] = self.list_query_threads(
            query_id=query_id,
            can_view_internal_thread=can_view_internal_thread,
        )
        return detail

    def list_query_threads(
        self,
        *,
        query_id: int,
        can_view_internal_thread: bool = False,
    ) -> list[dict[str, object]]:
        rows = (
            ReconcileQueryThread.objects.filter(dataquery_id=query_id, deleted=False)
            .order_by("-created_at", "-id")
            .values("id", "message_text", "message_type", "visibility", "source", "author_id", "created_at")
        )
        if not can_view_internal_thread:
            rows = rows.exclude(visibility="internal")
        return [
            {
                "id": int(row["id"]),
                "message_text": row["message_text"],
                "message_type": row["message_type"],
                "visibility": row["visibility"],
                "source": row["source"],
                "author_id": row["author_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_workbench_validation_issues(
        self,
        *,
        page_state_ids: tuple[int, ...],
        search: str = "",
    ) -> list[dict[str, object]]:
        if not page_state_ids:
            return []
        queryset = ReconcileValidationIssue.objects.filter(
            form_instance_id__in=page_state_ids,
            status__in=(
                ReconcileValidationIssueStatusChoices.OPEN,
                ReconcileValidationIssueStatusChoices.ACKNOWLEDGEMENT_REQUIRED,
                ReconcileValidationIssueStatusChoices.QUERY_CREATED,
            ),
        )
        if search:
            queryset = queryset.filter(Q(message__icontains=search) | Q(severity__icontains=search))
        return [
            {
                "issue_id": int(issue.pk),
                "page_state_id": int(issue.form_instance_id),
                "status": issue.status,
                "severity": issue.severity,
                "message": issue.message,
                "failed_value": issue.failed_value,
                "created_at": issue.created_at,
                "resolved_at": issue.resolved_at,
            }
            for issue in queryset.order_by("-created_at", "-id")
        ]

    @staticmethod
    def _visible_thread_filter(can_view_internal_thread: bool) -> Q:
        query = Q(query_threads__deleted=False)
        if not can_view_internal_thread:
            query &= ~Q(query_threads__visibility="internal")
        return query

    @staticmethod
    def _apply_workbench_filters(
        queryset,
        *,
        bucket: str,
        search: str,
        status: str,
        severity: str,
        source: str,
        blocking: str,
        assigned_to_id: int | None,
        opened_by_id: int | None,
    ):
        if bucket == "open":
            queryset = queryset.filter(status=ReconcileDataQueryStatusChoices.OPEN)
        elif bucket == "awaiting_site":
            queryset = queryset.filter(status="open")
        elif bucket == "awaiting_review":
            queryset = queryset.filter(status=ReconcileDataQueryStatusChoices.ANSWERED)
        elif bucket == "blocking":
            queryset = queryset.filter(is_blocking=True).exclude(status__in=("closed", "resolved", "cancelled", "void"))
        elif bucket == "resolved":
            queryset = queryset.filter(status=ReconcileDataQueryStatusChoices.RESOLVED)
        elif bucket == "closed":
            queryset = queryset.filter(status=ReconcileDataQueryStatusChoices.CLOSED)
        elif bucket == "validation_issues":
            queryset = queryset.none()

        if status:
            queryset = queryset.filter(status=status)
        if severity:
            queryset = queryset.filter(severity=severity)
        if source:
            queryset = queryset.filter(source=source)
        if blocking == "yes":
            queryset = queryset.filter(is_blocking=True)
        elif blocking == "no":
            queryset = queryset.filter(is_blocking=False)
        if assigned_to_id is not None and opened_by_id is not None:
            queryset = queryset.filter(
                Q(assigned_to_id=assigned_to_id) | Q(opened_by_id=opened_by_id)
            )
        elif assigned_to_id is not None:
            queryset = queryset.filter(assigned_to_id=assigned_to_id)
        elif opened_by_id is not None:
            queryset = queryset.filter(opened_by_id=opened_by_id)
        if search:
            query = Q(question_text__icontains=search) | Q(resolution_note__icontains=search)
            query |= Q(field_path__icontains=search) | Q(value_snapshot__icontains=search)
            if search.isdigit():
                query |= Q(pk=int(search))
            queryset = queryset.filter(query)
        return queryset

    @staticmethod
    def _workbench_sort_expression(sort: str) -> str:
        allowed = {
            "opened_at": "opened_at",
            "-opened_at": "-opened_at",
            "last_activity_at": "sort_last_activity_at",
            "-last_activity_at": "-sort_last_activity_at",
            "status": "status",
            "-status": "-status",
            "severity": "severity",
            "-severity": "-severity",
            "is_blocking": "is_blocking",
            "-is_blocking": "-is_blocking",
        }
        return allowed.get(sort, "-sort_last_activity_at")


__all__ = ["DjangoReconcileDataQueryReadRepository"]
