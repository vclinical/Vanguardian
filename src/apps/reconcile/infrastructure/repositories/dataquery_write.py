import json
import re
from datetime import datetime

from django.db.models import F, Q
from django.utils.translation import get_language

from apps.core.choices import DataCapturePageEntryStatusChoices
from apps.core.form_data_document import (
    build_field_path,
    flatten_form_data_for_export,
    normalize_form_data,
)
from apps.crf.models import CrfFieldTemplate
from apps.datacapture.models import DataCaptureFieldEntry, DataCapturePageEntry, DataCapturePageState
from apps.reconcile.models import (
    ReconcileDataQuery,
    ReconcileDataQuerySeverityChoices,
    ReconcileDataQuerySourceChoices,
    ReconcileDataQueryStatusChoices,
    ReconcileDataQueryTypeChoices,
    ReconcileQueryThread,
    ReconcileQueryThreadSourceChoices,
    ReconcileQueryThreadVisibilityChoices,
    ReconcileValidationIssue,
    ReconcileValidationIssueSnapshot,
    ReconcileValidationIssueSnapshotResultChoices,
    ReconcileValidationIssueStatusChoices,
    ReconcileValidationRun,
)

_DATE_PART_SUFFIXES = ("__day", "__month", "__year", "__time")
_JSONPATH_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DjangoReconcileDataQueryWriteRepository:
    @staticmethod
    def _acknowledgement_matches(issue, evaluation_context):
        if not evaluation_context or issue.status != ReconcileValidationIssueStatusChoices.ACKNOWLEDGED:
            return False
        snapshot = ReconcileValidationIssueSnapshot.objects.filter(
            validation_issue_id=issue.pk,
        ).order_by("-created_at", "-id").values_list("evaluated_values_json", flat=True).first()
        return isinstance(snapshot, dict) and snapshot.get("_validation_context") == evaluation_context

    ACTIVE_VALIDATION_ISSUE_STATUSES = (
        ReconcileValidationIssueStatusChoices.OPEN,
        ReconcileValidationIssueStatusChoices.ACKNOWLEDGEMENT_REQUIRED,
    )
    TERMINAL_VALIDATION_ISSUE_STATUSES = (
        ReconcileValidationIssueStatusChoices.ACKNOWLEDGED,
        ReconcileValidationIssueStatusChoices.CORRECTED,
        ReconcileValidationIssueStatusChoices.QUERY_CREATED,
        ReconcileValidationIssueStatusChoices.CLOSED,
        ReconcileValidationIssueStatusChoices.WAIVED,
    )
    REUSABLE_VALIDATION_ISSUE_STATUSES = (
        ReconcileValidationIssueStatusChoices.OPEN,
        ReconcileValidationIssueStatusChoices.ACKNOWLEDGEMENT_REQUIRED,
        ReconcileValidationIssueStatusChoices.ACKNOWLEDGED,
        ReconcileValidationIssueStatusChoices.CORRECTED,
        ReconcileValidationIssueStatusChoices.QUERY_CREATED,
    )

    @staticmethod
    def list_active_system_validation_queries(*, page_state_id):
        return list(ReconcileDataQuery.objects.filter(
            page_state_id=page_state_id, deleted=False,
            source=ReconcileDataQuerySourceChoices.SYSTEM, query_type=ReconcileDataQueryTypeChoices.VALIDATION,
            status__in=(ReconcileDataQueryStatusChoices.OPEN, ReconcileDataQueryStatusChoices.ANSWERED,
                        ReconcileDataQueryStatusChoices.RESOLVED),
        ).values("id", "field_template_id", "validation_rule_id", "status"))

    @staticmethod
    def close_passing_system_validation_query(*, dataquery_id, page_state_id, actor_user_id, now):
        return bool(ReconcileDataQuery.objects.filter(
            pk=dataquery_id, page_state_id=page_state_id, deleted=False,
            source=ReconcileDataQuerySourceChoices.SYSTEM, query_type=ReconcileDataQueryTypeChoices.VALIDATION,
            status__in=(ReconcileDataQueryStatusChoices.OPEN, ReconcileDataQueryStatusChoices.ANSWERED,
                        ReconcileDataQueryStatusChoices.RESOLVED),
        ).update(status=ReconcileDataQueryStatusChoices.CLOSED, closed_at=now, closed_by_id=actor_user_id,
                 resolved_at=now, resolved_by_id=actor_user_id, updated_at=now, updated_by_id=actor_user_id,
                 resolution_note="Imported value passed the validation rule."))

    @staticmethod
    def list_field_key_to_id(*, crf_template_id: int) -> dict[str, int]:
        return dict(
            CrfFieldTemplate.objects.filter(
                crf_template_id=crf_template_id,
                deleted=False,
            ).values_list("field_key", "id"),
        )

    @staticmethod
    def list_field_thread_value_contexts(
        *,
        crf_template_id: int,
        field_template_ids: tuple[int, ...],
    ) -> dict[int, dict[str, object]]:
        if not field_template_ids:
            return {}
        current_language = str(get_language() or "").split("-")[0].lower()
        fields = (
            CrfFieldTemplate.objects.filter(
                crf_template_id=crf_template_id,
                id__in=field_template_ids,
                deleted=False,
            )
            .select_related("ui_config")
            .prefetch_related("ui_config__translations")
        )
        contexts: dict[int, dict[str, object]] = {}
        for field in fields:
            ui_config = getattr(field, "ui_config", None)
            if ui_config is None or ui_config.deleted:
                continue
            translations = list(getattr(getattr(ui_config, "translations", None), "all", lambda: [])())
            preferred_translation = next(
                (
                    translation
                    for translation in translations
                    if str(translation.language_code or "").split("-")[0].lower() == current_language
                    and translation.options
                ),
                None,
            )
            fallback_translation = next((translation for translation in translations if translation.options), None)
            translation = preferred_translation or fallback_translation
            contexts[field.pk] = {
                "control_type": ui_config.control_type,
                "options": getattr(translation, "options", "") if translation is not None else "",
            }
        return contexts

    @classmethod
    def bulk_create_manual_open_queries(
        cls,
        *,
        page_state_id: int,
        items: list[dict[str, object]],
        actor_user_id: int | None,
        now: datetime,
    ) -> int:
        records: list[ReconcileDataQuery] = []
        for item in items:
            field_template_id = item.get("field_template_id")
            entry_context = cls._current_page_entry_query_context(
                page_state_id=page_state_id,
                field_template_id=int(field_template_id) if field_template_id is not None else None,
            )
            records.append(
                ReconcileDataQuery(
                    created_at=now,
                    updated_at=now,
                    deleted=False,
                    status=ReconcileDataQueryStatusChoices.OPEN,
                    source=ReconcileDataQuerySourceChoices.MANUAL,
                    query_type=ReconcileDataQueryTypeChoices.MANUAL,
                    severity=ReconcileDataQuerySeverityChoices.MINOR,
                    is_blocking=True,
                    question_text=str(item.get("reason") or ""),
                    resolution_note=str(item.get("resolution_note") or "")[:1000],
                    opened_at=now,
                    closed_at=None,
                    page_state_id=page_state_id,
                    page_entry_id=entry_context.get("page_entry_id"),
                    field_template_id=item.get("field_template_id"),
                    validation_rule_id=None,
                    data_version=entry_context.get("data_version") or item.get("data_version"),
                    field_path=entry_context.get("field_path") or item.get("field_path"),
                    value_snapshot=entry_context.get("value_snapshot"),
                    assigned_to_id=entry_context.get("assigned_to_id"),
                    opened_by_id=actor_user_id,
                    created_by_id=actor_user_id,
                    updated_by_id=actor_user_id,
                ),
            )
        if not records:
            return 0
        ReconcileDataQuery.objects.bulk_create(records)
        return len(records)

    @staticmethod
    def get_current_field_entry_id(
        *,
        page_state_id: int,
        field_template_id: int | None,
        page_entry_id: int | None = None,
    ) -> int | None:
        if field_template_id is None:
            return None
        field_entry = (
            DataCaptureFieldEntry.objects.filter(
                page_state_id=page_state_id,
                page_entry_id=page_entry_id if page_entry_id is not None else F("page_state__current_entry_id"),
                field_template_id=field_template_id,
                deleted=False,
            )
            .only("id")
            .first()
        )
        return int(field_entry.pk) if field_entry is not None else None

    @classmethod
    def _validation_issue_value_key(cls, value: object) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return str(value)

    @staticmethod
    def _validation_issue_field_identity_filter(
        *,
        field_template_id: int | None,
        field_instance_id: int | None,
    ) -> Q:
        if field_template_id is not None:
            return Q(field_instance__field_template_id=field_template_id) | Q(
                field_instance_id__isnull=True,
                rule__field_template_id=field_template_id,
            )
        if field_instance_id is not None:
            return Q(field_instance_id=field_instance_id)
        return Q(field_instance_id__isnull=True)

    @staticmethod
    def _validation_issue_signature(
        *,
        rule_id: int | None,
        field_template_id: int | None,
        field_instance_id: int | None,
    ) -> tuple[int | None, str]:
        _ = field_instance_id
        return (rule_id, f"field_template:{int(field_template_id) if field_template_id is not None else 'none'}")

    @classmethod
    def _reusable_validation_issue_queryset(cls, *, form_instance_id: int):
        return (
            ReconcileValidationIssue.objects.filter(
                form_instance_id=form_instance_id,
                status__in=cls.REUSABLE_VALIDATION_ISSUE_STATUSES,
            )
            .select_related("rule", "field_instance")
            .order_by("created_at", "id")
        )

    @classmethod
    def _list_reusable_validation_issues_by_signature(
        cls,
        *,
        form_instance_id: int,
    ) -> dict[tuple[int | None, str], ReconcileValidationIssue]:
        issues_by_signature: dict[tuple[int | None, str], ReconcileValidationIssue] = {}
        for issue in cls._reusable_validation_issue_queryset(form_instance_id=form_instance_id):
            resolved_rule_id = int(issue.rule_id) if issue.rule_id is not None else None
            resolved_field_template_id = (
                int(getattr(issue.field_instance, "field_template_id", 0) or 0) or None
            )
            if resolved_field_template_id is None:
                resolved_field_template_id = int(getattr(issue.rule, "field_template_id", 0) or 0) or None
            signature = cls._validation_issue_signature(
                rule_id=resolved_rule_id,
                field_template_id=resolved_field_template_id,
                field_instance_id=int(issue.field_instance_id) if issue.field_instance_id is not None else None,
            )
            issues_by_signature.setdefault(signature, issue)
        return issues_by_signature

    @staticmethod
    def _create_validation_issue_snapshot(
        *,
        validation_issue_id: int,
        validation_run_id: int,
        result: str,
        evaluated_values_json: object,
        message: str,
        severity: str,
        data_version: int,
        created_at: datetime,
        related_audit_event_id: int | None,
    ) -> None:
        if evaluated_values_json is None:
            evaluated_values_json = {}
        ReconcileValidationIssueSnapshot.objects.create(
            validation_issue_id=validation_issue_id,
            validation_run_id=validation_run_id,
            result=result,
            evaluated_values_json=evaluated_values_json,
            message=message,
            severity=severity,
            data_version=data_version,
            created_at=created_at,
            related_audit_event_id=related_audit_event_id,
        )

    @staticmethod
    def create_validation_run(
        *,
        page_state_id: int,
        source: str,
        data_version: int,
        actor_user_id: int | None,
        now: datetime,
        related_audit_event_id: int | None = None,
    ) -> ReconcileValidationRun:
        return ReconcileValidationRun.objects.create(
            created_at=now,
            form_instance_id=page_state_id,
            source=source,
            data_version=int(data_version),
            triggered_by=actor_user_id,
            related_audit_event_id=related_audit_event_id,
        )

    @staticmethod
    def get_page_state_data_version(*, page_state_id: int) -> int:
        return int(
            DataCapturePageState.objects.filter(pk=page_state_id).values_list("data_version", flat=True).first() or 0
        )

    @staticmethod
    def _resolve_snapshot_value_for_field(
        *,
        field_template_id: int | None,
        evaluated_values_by_field_template_id: dict[int, object],
    ) -> object:
        if field_template_id is None:
            return None
        return evaluated_values_by_field_template_id.get(int(field_template_id))

    @classmethod
    def bulk_create_soft_validation_issues(
        cls,
        *,
        page_state_id: int,
        items: list[dict[str, object]],
        actor_user_id: int | None,
        now: datetime,
        validation_run_id: int,
        evaluated_values_by_field_template_id: dict[int, object] | None = None,
        data_version: int | None = None,
        related_audit_event_id: int | None = None,
        page_entry_id: int | None = None,
        passed_rule_ids: set[int] | None = None,
    ) -> int:
        _ = actor_user_id
        normalized_validation_run_id = int(validation_run_id)
        normalized_evaluated_values_by_field_template_id = (
            dict(evaluated_values_by_field_template_id)
            if isinstance(evaluated_values_by_field_template_id, dict)
            else {}
        )
        normalized_data_version = int(data_version) if data_version is not None else 0
        issues_by_signature = cls._list_reusable_validation_issues_by_signature(
            form_instance_id=page_state_id,
        )
        failing_signatures: set[tuple[int | None, str]] = set()
        for item in items:
            rule_id = item.get("rule_id")
            if rule_id is None:
                continue
            field_template_id = item.get("field_template_id")
            normalized_field_template_id = int(field_template_id) if field_template_id is not None else None
            field_instance_id = cls.get_current_field_entry_id(
                page_state_id=page_state_id,
                field_template_id=normalized_field_template_id,
                **({"page_entry_id": page_entry_id} if page_entry_id is not None else {}),
            )
            signature = cls._validation_issue_signature(
                rule_id=int(rule_id),
                field_template_id=normalized_field_template_id,
                field_instance_id=field_instance_id,
            )
            snapshot_value = cls._resolve_snapshot_value_for_field(
                field_template_id=normalized_field_template_id,
                evaluated_values_by_field_template_id=normalized_evaluated_values_by_field_template_id,
            )
            issue = issues_by_signature.get(signature)
            preserve_acknowledgement = issue is not None and cls._acknowledgement_matches(
                issue, item.get("evaluation_context"))
            failing_signatures.add(signature)
            if issue is None:
                issue = ReconcileValidationIssue.objects.create(
                    created_at=now,
                    rule_id=int(rule_id),
                    form_instance_id=page_state_id,
                    field_instance_id=field_instance_id,
                    mode=str(item.get("mode") or "SOFT").strip() or "SOFT",
                    severity=str(item.get("severity") or "").strip(),
                    status=ReconcileValidationIssueStatusChoices.ACKNOWLEDGEMENT_REQUIRED,
                    message=str(item.get("message") or "").strip() or "Validation warning requires acknowledgement.",
                    failed_value=item.get("failed_value"),
                    acknowledged_by=None,
                    acknowledged_at=None,
                    acknowledgement_comment=None,
                    resolved_at=None,
                )
                issues_by_signature[signature] = issue
            else:
                issue.mode = str(item.get("mode") or issue.mode or "SOFT").strip() or "SOFT"
                issue.severity = str(item.get("severity") or issue.severity or "").strip()
                if not preserve_acknowledgement:
                    issue.status = ReconcileValidationIssueStatusChoices.ACKNOWLEDGEMENT_REQUIRED
                issue.message = (
                    str(item.get("message") or "").strip()
                    or issue.message
                    or "Validation warning requires acknowledgement."
                )
                issue.failed_value = item.get("failed_value")
                issue.field_instance_id = field_instance_id
                if not preserve_acknowledgement:
                    issue.resolved_at = None
                issue.save(
                    update_fields=[
                        "mode",
                        "severity",
                        "status",
                        "message",
                        "failed_value",
                        "field_instance",
                        "resolved_at",
                    ]
                )
            cls._create_validation_issue_snapshot(
                validation_issue_id=int(issue.pk),
                validation_run_id=normalized_validation_run_id,
                result=ReconcileValidationIssueSnapshotResultChoices.FAIL,
                evaluated_values_json={"_validation_context": item["evaluation_context"], "value": snapshot_value,
                    "acknowledged": preserve_acknowledgement} if item.get("evaluation_context") else snapshot_value,
                message=str(item.get("message") or "").strip() or issue.message,
                severity=str(item.get("severity") or "").strip() or issue.severity,
                data_version=normalized_data_version,
                created_at=now,
                related_audit_event_id=related_audit_event_id,
            )

        for signature, issue in issues_by_signature.items():
            if passed_rule_ids is not None and issue.rule_id not in passed_rule_ids:
                continue
            if signature in failing_signatures:
                continue
            if issue.status not in cls.ACTIVE_VALIDATION_ISSUE_STATUSES:
                continue
            resolved_field_template_id = None
            if issue.field_instance_id is not None:
                resolved_field_template_id = int(getattr(issue.field_instance, "field_template_id", 0) or 0) or None
            if resolved_field_template_id is None:
                resolved_field_template_id = int(getattr(issue.rule, "field_template_id", 0) or 0) or None
            cls._create_validation_issue_snapshot(
                validation_issue_id=int(issue.pk),
                validation_run_id=normalized_validation_run_id,
                result=ReconcileValidationIssueSnapshotResultChoices.PASS,
                evaluated_values_json=cls._resolve_snapshot_value_for_field(
                    field_template_id=resolved_field_template_id,
                    evaluated_values_by_field_template_id=normalized_evaluated_values_by_field_template_id,
                ),
                message=str(issue.message or "").strip(),
                severity=str(issue.severity or "").strip(),
                data_version=normalized_data_version,
                created_at=now,
                related_audit_event_id=related_audit_event_id,
            )
            if issue.status != ReconcileValidationIssueStatusChoices.CORRECTED:
                issue.status = ReconcileValidationIssueStatusChoices.CORRECTED
                issue.resolved_at = now
                issue.save(update_fields=["status", "resolved_at"])
        return len(failing_signatures)

    @classmethod
    def acknowledge_validation_issues(
        cls,
        *,
        page_state_id: int,
        items: list[dict[str, object]],
        actor_user_id: int | None,
        now: datetime,
        validation_run_id: int,
        current_failures: list[dict] | None = None,
    ) -> list[int]:
        acknowledged_issue_ids: list[int] = []
        for item in items:
            issue_id = item.get("issue_id")
            try:
                normalized_issue_id = int(issue_id)
            except (TypeError, ValueError):
                continue
            issue = (
                ReconcileValidationIssue.objects.select_related("form_instance")
                .filter(
                    id=normalized_issue_id,
                    form_instance_id=page_state_id,
                    status__in=cls.ACTIVE_VALIDATION_ISSUE_STATUSES,
                )
                .first()
            )
            if issue is None:
                continue
            evaluation_context = None
            if current_failures is not None:
                current = next((failure for failure in current_failures if failure["rule_id"] == issue.rule_id), None)
                if current is None or current["failed_value"] != issue.failed_value or not current.get("evaluation_context"):
                    raise ValueError("The validation result changed. Refresh the form before acknowledging.")
                evaluation_context = current["evaluation_context"]
            acknowledgement_comment = str(item.get("comment") or "").strip()
            issue.status = ReconcileValidationIssueStatusChoices.ACKNOWLEDGED
            issue.acknowledged_by = actor_user_id
            issue.acknowledged_at = now
            issue.acknowledgement_comment = acknowledgement_comment
            issue.resolved_at = now
            issue.save(
                update_fields=[
                    "status",
                    "acknowledged_by",
                    "acknowledged_at",
                    "acknowledgement_comment",
                    "resolved_at",
                ]
            )
            cls._create_validation_issue_snapshot(
                validation_issue_id=int(issue.pk),
                validation_run_id=int(validation_run_id),
                result=ReconcileValidationIssueSnapshotResultChoices.PASS,
                evaluated_values_json={"_validation_context": evaluation_context, "value": issue.failed_value,
                    "acknowledged": True} if evaluation_context else (issue.failed_value if issue.failed_value is not None else {}),
                message=acknowledgement_comment or str(issue.message or "").strip(),
                severity=str(issue.severity or "").strip(),
                data_version=int(getattr(issue.form_instance, "data_version", 0) or 0),
                created_at=now,
                related_audit_event_id=None,
            )
            acknowledged_issue_ids.append(normalized_issue_id)
        return acknowledged_issue_ids

    @classmethod
    def list_active_validation_issues_by_page_state_and_field_templates(
        cls,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> list[dict[str, object]]:
        if not field_template_ids:
            return []
        rows = (
            ReconcileValidationIssue.objects.filter(
                form_instance_id=page_state_id,
                status__in=cls.ACTIVE_VALIDATION_ISSUE_STATUSES,
            )
            .filter(
                Q(field_instance__field_template_id__in=field_template_ids)
                | Q(field_instance_id__isnull=True, rule__field_template_id__in=field_template_ids)
            )
            .values(
                "id",
                "rule_id",
                "field_instance__field_template_id",
                "rule__field_template_id",
                "failed_value",
            )
        )
        out: list[dict[str, object]] = []
        seen_ids: set[int] = set()
        for row in rows:
            issue_id = int(row["id"])
            if issue_id in seen_ids:
                continue
            seen_ids.add(issue_id)
            field_template_id = row["field_instance__field_template_id"] or row["rule__field_template_id"]
            if field_template_id is None:
                continue
            out.append(
                {
                    "id": issue_id,
                    "rule_id": int(row["rule_id"]) if row["rule_id"] is not None else None,
                    "field_template_id": int(field_template_id),
                    "failed_value": row["failed_value"],
                }
            )
        return out

    @classmethod
    def mark_validation_issue_corrected(
        cls,
        *,
        issue_id: int,
        page_state_id: int,
        actor_user_id: int | None,
        correction_comment: str,
        now: datetime,
    ) -> bool:
        updated = ReconcileValidationIssue.objects.filter(
            id=issue_id,
            form_instance_id=page_state_id,
            status__in=cls.ACTIVE_VALIDATION_ISSUE_STATUSES,
        ).update(
            status=ReconcileValidationIssueStatusChoices.CORRECTED,
            acknowledged_by=actor_user_id,
            acknowledged_at=now,
            acknowledgement_comment=str(correction_comment or "").strip(),
            resolved_at=now,
        )
        return updated > 0

    @classmethod
    def has_open_query_for_page_field(
        cls,
        *,
        page_state_id: int,
        field_template_id: int,
        field_key: str = "",
    ) -> bool:
        entry_context = cls._current_page_entry_query_context(
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            storage_key_hint=field_key,
        )
        queryset = ReconcileDataQuery.objects.filter(
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            status=ReconcileDataQueryStatusChoices.OPEN,
            deleted=False,
        )
        field_path = str(entry_context.get("field_path") or "").strip()
        if field_path:
            field_paths = [
                str(path or "").strip()
                for path in entry_context.get("field_path_candidates", (field_path,))
                if str(path or "").strip()
            ]
            queryset = queryset.filter(field_path__in=field_paths or [field_path])
        return queryset.exists()

    @staticmethod
    def list_latest_open_query_ids_by_page_state_and_field_templates(
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
    ) -> dict[int, int]:
        if not field_template_ids:
            return {}
        rows = (
            ReconcileDataQuery.objects.filter(
                page_state_id=page_state_id,
                field_template_id__in=field_template_ids,
                field_template_id__isnull=False,
                status__in=(
                    ReconcileDataQueryStatusChoices.OPEN,
                    ReconcileDataQueryStatusChoices.ANSWERED,
                ),
                deleted=False,
            )
            .order_by("field_template_id", "-opened_at", "-created_at", "-id")
            .values("field_template_id", "id")
        )
        query_ids_by_field_template: dict[int, int] = {}
        for row in rows:
            query_ids_by_field_template.setdefault(int(row["field_template_id"]), int(row["id"]))
        return query_ids_by_field_template

    @classmethod
    def create_manual_open_query(
        cls,
        *,
        page_state_id: int,
        field_template_id: int,
        question_text: str,
        actor_user_id: int | None,
        now: datetime,
        field_key: str = "",
    ) -> ReconcileDataQuery:
        entry_context = cls._current_page_entry_query_context(
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            storage_key_hint=field_key,
        )
        return ReconcileDataQuery.objects.create(
            created_at=now,
            updated_at=now,
            deleted=False,
            status=ReconcileDataQueryStatusChoices.OPEN,
            source=ReconcileDataQuerySourceChoices.MANUAL,
            query_type=ReconcileDataQueryTypeChoices.MANUAL,
            severity=ReconcileDataQuerySeverityChoices.MINOR,
            is_blocking=True,
            question_text=question_text,
            resolution_note="",
            opened_at=now,
            closed_at=None,
            page_state_id=page_state_id,
            page_entry_id=entry_context.get("page_entry_id"),
            field_template_id=field_template_id,
            validation_rule_id=None,
            data_version=entry_context.get("data_version"),
            field_path=entry_context.get("field_path"),
            value_snapshot=entry_context.get("value_snapshot"),
            assigned_to_id=entry_context.get("assigned_to_id"),
            opened_by_id=actor_user_id,
            created_by_id=actor_user_id,
            updated_by_id=actor_user_id,
        )

    @classmethod
    def create_validation_open_query(
        cls,
        *,
        page_state_id: int,
        field_template_id: int,
        validation_rule_id: int | None,
        question_text: str,
        severity: str,
        actor_user_id: int | None,
        now: datetime,
        field_key: str = "",
        page_entry_id: int | None = None,
    ) -> ReconcileDataQuery:
        entry_context = cls._current_page_entry_query_context(
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            storage_key_hint=field_key,
            **({"page_entry_id": page_entry_id} if page_entry_id is not None else {}),
        )
        return ReconcileDataQuery.objects.create(
            created_at=now,
            updated_at=now,
            deleted=False,
            status=ReconcileDataQueryStatusChoices.OPEN,
            source=ReconcileDataQuerySourceChoices.SYSTEM,
            query_type=ReconcileDataQueryTypeChoices.VALIDATION,
            severity=severity,
            is_blocking=True,
            question_text=question_text,
            resolution_note="",
            opened_at=now,
            closed_at=None,
            page_state_id=page_state_id,
            page_entry_id=entry_context.get("page_entry_id"),
            field_template_id=field_template_id,
            validation_rule_id=validation_rule_id,
            data_version=entry_context.get("data_version"),
            field_path=entry_context.get("field_path"),
            value_snapshot=entry_context.get("value_snapshot"),
            assigned_to_id=entry_context.get("assigned_to_id"),
            opened_by_id=actor_user_id,
            created_by_id=actor_user_id,
            updated_by_id=actor_user_id,
            reason_code="validation_rule_failed",
        )

    @staticmethod
    def create_query_thread_message(
        *,
        dataquery_id: int,
        message_text: str,
        message_type: str,
        actor_user_id: int | None,
        now: datetime,
        source: str = ReconcileQueryThreadSourceChoices.MANUAL,
    ) -> ReconcileQueryThread:
        return ReconcileQueryThread.objects.create(
            created_at=now,
            updated_at=now,
            deleted=False,
            message_text=message_text,
            message_type=message_type,
            visibility=ReconcileQueryThreadVisibilityChoices.SITE,
            source=source,
            dataquery_id=dataquery_id,
            author_id=actor_user_id,
            created_by_id=actor_user_id,
            updated_by_id=actor_user_id,
        )

    @staticmethod
    def resolve_query(
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        resolution_note: str,
        actor_user_id: int | None,
        now: datetime,
    ) -> bool:
        updated = (
            ReconcileDataQuery.objects.filter(
                pk=dataquery_id,
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                deleted=False,
                status=ReconcileDataQueryStatusChoices.ANSWERED,
            )
            .update(
                status=ReconcileDataQueryStatusChoices.RESOLVED,
                resolution_note=resolution_note[:1000],
                resolved_at=now,
                resolved_by_id=actor_user_id,
                updated_at=now,
                updated_by_id=actor_user_id,
            )
        )
        return updated > 0

    @staticmethod
    def close_resolved_query(
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        actor_user_id: int | None,
        now: datetime,
    ) -> bool:
        updated = (
            ReconcileDataQuery.objects.filter(
                pk=dataquery_id,
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                deleted=False,
                status=ReconcileDataQueryStatusChoices.RESOLVED,
            ).update(
                status=ReconcileDataQueryStatusChoices.CLOSED,
                closed_at=now,
                closed_by_id=actor_user_id,
                updated_at=now,
                updated_by_id=actor_user_id,
            )
        )
        return updated > 0

    @staticmethod
    def reopen_query(
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        actor_user_id: int | None,
        now: datetime,
    ) -> bool:
        updated = (
            ReconcileDataQuery.objects.filter(
                pk=dataquery_id,
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                deleted=False,
                status__in=(
                    ReconcileDataQueryStatusChoices.RESOLVED,
                    ReconcileDataQueryStatusChoices.CLOSED,
                ),
            ).update(
                status=ReconcileDataQueryStatusChoices.OPEN,
                resolved_at=None,
                resolved_by_id=None,
                closed_at=None,
                closed_by_id=None,
                updated_at=now,
                updated_by_id=actor_user_id,
            )
        )
        return updated > 0

    @staticmethod
    def request_clarification(
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        actor_user_id: int | None,
        now: datetime,
    ) -> bool:
        updated = (
            ReconcileDataQuery.objects.filter(
                pk=dataquery_id,
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                deleted=False,
                status=ReconcileDataQueryStatusChoices.ANSWERED,
            ).update(
                status=ReconcileDataQueryStatusChoices.OPEN,
                answered_at=None,
                answered_by_id=None,
                updated_at=now,
                updated_by_id=actor_user_id,
            )
        )
        return updated > 0

    @staticmethod
    def mark_query_answered(
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        actor_user_id: int | None,
        now: datetime,
    ) -> bool:
        updated = (
            ReconcileDataQuery.objects.filter(
                pk=dataquery_id,
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                deleted=False,
                status__in=(
                    ReconcileDataQueryStatusChoices.OPEN,
                    ReconcileDataQueryStatusChoices.ANSWERED,
                ),
            )
            .update(
                status=ReconcileDataQueryStatusChoices.ANSWERED,
                answered_at=now,
                answered_by_id=actor_user_id,
                updated_at=now,
                updated_by_id=actor_user_id,
            )
        )
        return updated > 0

    @staticmethod
    def cancel_query(
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        actor_user_id: int | None,
        now: datetime,
    ) -> bool:
        updated = (
            ReconcileDataQuery.objects.filter(
                pk=dataquery_id,
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                deleted=False,
            )
            .exclude(status__in=(ReconcileDataQueryStatusChoices.CANCELLED, ReconcileDataQueryStatusChoices.CLOSED))
            .update(
                status=ReconcileDataQueryStatusChoices.CANCELLED,
                updated_at=now,
                updated_by_id=actor_user_id,
            )
        )
        return updated > 0

    @staticmethod
    def query_belongs_to_scope(*, dataquery_id: int, page_state_id: int, field_template_id: int | None) -> bool:
        return ReconcileDataQuery.objects.filter(
            pk=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            deleted=False,
        ).exists()

    @staticmethod
    def get_query_action_scope(*, dataquery_id: int) -> dict[str, object] | None:
        row = (
            ReconcileDataQuery.objects.filter(pk=dataquery_id, deleted=False)
            .values("id", "page_state_id", "field_template_id", "status")
            .first()
        )
        if row is None:
            return None
        return {
            "dataquery_id": int(row["id"]),
            "page_state_id": int(row["page_state_id"]),
            "field_template_id": row["field_template_id"],
            "status": str(row["status"] or ""),
        }

    @staticmethod
    def user_can_respond_to_query(*, dataquery_id: int, actor_user_id: int | None) -> bool:
        if actor_user_id is None:
            return False
        return ReconcileDataQuery.objects.filter(
            pk=dataquery_id,
            deleted=False,
        ).filter(
            Q(opened_by_id=actor_user_id) | Q(assigned_to_id=actor_user_id),
        ).exists()

    @classmethod
    def _current_page_entry_query_context(
        cls,
        *,
        page_state_id: int,
        field_template_id: int | None,
        storage_key_hint: str = "",
        page_entry_id: int | None = None,
    ) -> dict[str, object]:
        if field_template_id is None:
            return {}
        entry = (
            DataCapturePageEntry.objects.filter(
                page_state_id=page_state_id,
                **({"pk": page_entry_id} if page_entry_id is not None else {"page_state__current_entry_id": F("id")}),
                deleted=False,
            )
            .only("id", "entry_version", "data", "submitted_by_id")
            .first()
        )
        if entry is None and page_entry_id is None:
            entry = (
                DataCapturePageEntry.objects.filter(
                    page_state_id=page_state_id,
                    status=DataCapturePageEntryStatusChoices.SUBMITTED,
                    deleted=False,
                )
                .order_by("-submitted_at", "-updated_at", "-id")
                .only("id", "entry_version", "data", "submitted_by_id")
                .first()
            )
        if entry is None:
            return {}

        field = (
            CrfFieldTemplate.objects.filter(pk=field_template_id, deleted=False)
            .select_related("section_template")
            .only("field_key", "section_template__section_code", "section_template__is_repeatable")
            .first()
        )
        field_key = str(getattr(field, "field_key", "") or "").strip()
        section_code = str(getattr(getattr(field, "section_template", None), "section_code", "") or "").strip()
        is_repeatable = bool(getattr(getattr(field, "section_template", None), "is_repeatable", False))
        storage_key_hint = str(storage_key_hint or "").strip()
        payload = cls._parse_entry_payload(entry.data)
        value_snapshot, storage_key = cls._entry_field_value_snapshot(
            payload=payload,
            field_key=field_key,
            field_template_id=field_template_id,
            storage_key_hint=storage_key_hint,
        )
        field_path = cls._canonical_field_path(
            section_code=section_code,
            storage_key=storage_key,
            is_repeatable=is_repeatable,
        )
        return {
            "page_entry_id": entry.pk,
            "data_version": str(entry.entry_version or "").strip() or None,
            "field_path": field_path,
            "field_path_candidates": cls._field_path_candidates(
                section_code=section_code,
                storage_key=storage_key,
                is_repeatable=is_repeatable,
                field_path=field_path,
            ),
            "value_snapshot": value_snapshot,
            "assigned_to_id": entry.submitted_by_id,
        }

    @staticmethod
    def _parse_entry_payload(raw_data: object) -> dict[str, object]:
        if isinstance(raw_data, dict):
            return raw_data
        try:
            parsed = json.loads(str(raw_data or "{}"))
        except (TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        doc = normalize_form_data(parsed, strict=False)
        return flatten_form_data_for_export(doc, repeat_strategy="legacy_repeat_suffix")

    @classmethod
    def _entry_field_value_snapshot(
        cls,
        *,
        payload: dict[str, object],
        field_key: str,
        field_template_id: int,
        storage_key_hint: str = "",
    ) -> tuple[str, str]:
        storage_keys = cls._candidate_storage_keys(
            field_key=field_key,
            field_template_id=field_template_id,
            storage_key_hint=storage_key_hint,
        )
        for storage_key in storage_keys:
            if storage_key in payload:
                return cls._format_value_snapshot(payload.get(storage_key)), storage_key
        for storage_key in storage_keys:
            part_map = {
                f"{storage_key}{suffix}": payload.get(f"{storage_key}{suffix}")
                for suffix in _DATE_PART_SUFFIXES
                if f"{storage_key}{suffix}" in payload
            }
            if part_map:
                return json.dumps(part_map, ensure_ascii=False, sort_keys=True), storage_key
        fallback_key = storage_key_hint or field_key or f"field_{field_template_id}"
        return "", fallback_key

    @staticmethod
    def _candidate_storage_keys(
        *,
        field_key: str,
        field_template_id: int,
        storage_key_hint: str = "",
    ) -> list[str]:
        keys = [
            key
            for key in (
                storage_key_hint,
                field_key,
                f"field_{field_template_id}",
            )
            if key
        ]
        ordered: list[str] = []
        seen: set[str] = set()
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
        return ordered

    @staticmethod
    def _format_value_snapshot(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    @staticmethod
    def _jsonpath_for_field_key(field_key: str) -> str:
        normalized = str(field_key or "").strip()
        if not normalized:
            return "$"
        if _JSONPATH_IDENTIFIER_PATTERN.match(normalized):
            return f"$.{normalized}"
        return f"$[{json.dumps(normalized, ensure_ascii=False)}]"

    @classmethod
    def _canonical_field_path(cls, *, section_code: str, storage_key: str, is_repeatable: bool = False) -> str:
        if not section_code:
            return cls._jsonpath_for_field_key(storage_key)
        repeat_match = re.match(r"^(?P<base>.+)__repeat_(?P<repeat_index>\d+)$", str(storage_key or "").strip())
        if repeat_match:
            row_no = int(repeat_match.group("repeat_index"))
            return build_field_path(
                section_code,
                repeat_match.group("base"),
                row_key=f"row_{row_no:03d}",
            )
        if is_repeatable:
            return build_field_path(section_code, storage_key, row_key="row_001")
        return build_field_path(section_code, storage_key)

    @classmethod
    def _field_path_candidates(
        cls,
        *,
        section_code: str,
        storage_key: str,
        is_repeatable: bool,
        field_path: str,
    ) -> tuple[str, ...]:
        candidates = [str(field_path or "").strip()]
        if section_code and is_repeatable and not re.match(r"^.+__repeat_\d+$", str(storage_key or "").strip()):
            candidates.append(build_field_path(section_code, storage_key))
        return tuple(key for key in dict.fromkeys(candidates) if key)


__all__ = ["DjangoReconcileDataQueryWriteRepository"]
