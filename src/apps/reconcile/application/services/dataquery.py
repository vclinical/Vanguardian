import json
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from apps.core.choices.reconcile import ReconcileValidationRunSourceChoices
from apps.reconcile.infrastructure.repositories import DjangoReconcileDataQueryWriteRepository

_DATE_PART_SUFFIXES = ("__day", "__month", "__year", "__time")
_QUERY_THREAD_COMMENT = "comment"
_QUERY_THREAD_RESOLUTION = "resolution"
_QUERY_THREAD_STATUS_CHANGE = "status_change"
_QUERY_THREAD_SOURCE_SYSTEM = "system"
_QUERY_RESPONSE_PERMISSION_ERROR = "Only the query opener or assignee can respond to this query."


@dataclass(frozen=True)
class ReconcileChangeReasonItem:
    field_key: str
    field_label: str
    reason: str


@dataclass(frozen=True)
class ReconcileValidationFailureItem:
    rule_id: int | None
    field_template_id: int | None
    field_key: str
    mode: str
    severity: str
    message: str
    failed_value: object
    evaluation_context: str | None = None


class ReconcileDataQueryWriteService:
    def __init__(self, repository=None):
        self.repository = repository or DjangoReconcileDataQueryWriteRepository()

    @staticmethod
    def _canonical_field_key(field_key: str) -> str:
        normalized = str(field_key or "").strip()
        for suffix in _DATE_PART_SUFFIXES:
            if normalized.endswith(suffix):
                return normalized[: -len(suffix)]
        return normalized

    @staticmethod
    def _resolve_field_template_id(*, canonical_field_key: str, field_key_to_id: dict[str, int]) -> int | None:
        if not canonical_field_key:
            return None
        if canonical_field_key.startswith("field_"):
            raw_id = canonical_field_key.removeprefix("field_").strip()
            if raw_id.isdigit():
                return int(raw_id)
        return field_key_to_id.get(canonical_field_key)

    @staticmethod
    def _to_int_or_none(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_query_severity(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"critical", "fatal"}:
            return "critical"
        if normalized in {"major", "error", "hard"}:
            return "major"
        return "minor"

    @classmethod
    def _normalize_validation_failure(cls, item) -> ReconcileValidationFailureItem:
        if isinstance(item, dict):
            getter = item.get
        else:
            def getter(key, default=None):
                return getattr(item, key, default)

        return ReconcileValidationFailureItem(
            rule_id=cls._to_int_or_none(getter("rule_id")),
            field_template_id=cls._to_int_or_none(getter("field_template_id")),
            field_key=str(getter("field_key") or "").strip(),
            mode=str(getter("mode") or "").strip().upper(),
            severity=str(getter("severity") or "").strip(),
            message=str(getter("message") or "").strip(),
            failed_value=getter("failed_value"),
            evaluation_context=getter("evaluation_context"),
        )

    def create_validation_failure_records(
        self,
        *,
        page_state_id: int,
        crf_template_id: int | None = None,
        failures: list[object],
        actor_user_id: int | None,
        evaluated_values_json: dict[str, object] | None = None,
        data_version: int | None = None,
        related_audit_event_id: int | None = None,
        source: str = ReconcileValidationRunSourceChoices.SUBMIT_FOR_REVIEW,
        page_entry_id: int | None = None,
        passed_rule_ids: set[int] | None = None,
    ) -> dict[str, int]:
        normalized_failures = [self._normalize_validation_failure(item) for item in failures]
        validation_issue_failures = [
            item for item in normalized_failures if item.mode in {"HARD", "SOFT"}
        ]
        query_failures = [item for item in normalized_failures if item.mode == "QUERY"]
        now: datetime = timezone.now()
        normalized_evaluated_values_json = (
            dict(evaluated_values_json) if isinstance(evaluated_values_json, dict) else {}
        )
        field_key_to_id = (
            self.repository.list_field_key_to_id(crf_template_id=crf_template_id)
            if crf_template_id is not None
            else {}
        )
        evaluated_values_by_field_template_id: dict[int, object] = {}
        for raw_field_key, value in normalized_evaluated_values_json.items():
            canonical_field_key = self._canonical_field_key(str(raw_field_key or ""))
            field_template_id = self._resolve_field_template_id(
                canonical_field_key=canonical_field_key,
                field_key_to_id=field_key_to_id,
            )
            if field_template_id is None:
                continue
            evaluated_values_by_field_template_id[int(field_template_id)] = value
        normalized_data_version = int(data_version) if data_version is not None else 0
        validation_run = self.repository.create_validation_run(
            page_state_id=page_state_id,
            source=source,
            data_version=normalized_data_version,
            actor_user_id=actor_user_id,
            now=now,
            related_audit_event_id=related_audit_event_id,
        )

        soft_issue_count = self.repository.bulk_create_soft_validation_issues(
            page_state_id=page_state_id,
            items=[
                {
                    "rule_id": item.rule_id,
                    "field_template_id": item.field_template_id,
                    "mode": item.mode,
                    "severity": item.severity,
                    "message": item.message,
                    "failed_value": item.failed_value,
                    **({"evaluation_context": item.evaluation_context} if item.evaluation_context else {}),
                }
                for item in validation_issue_failures
            ],
            actor_user_id=actor_user_id,
            now=now,
            validation_run_id=int(validation_run.pk),
            evaluated_values_by_field_template_id=evaluated_values_by_field_template_id,
            data_version=normalized_data_version,
            related_audit_event_id=related_audit_event_id,
            **({"page_entry_id": page_entry_id, "passed_rule_ids": passed_rule_ids} if page_entry_id is not None else {}),
        )

        query_count = 0
        for item in query_failures:
            if item.field_template_id is None:
                continue
            if self.repository.has_open_query_for_page_field(
                page_state_id=page_state_id,
                field_template_id=item.field_template_id,
                field_key=item.field_key,
            ):
                continue
            message_text = item.message or "Validation rule failed."
            dataquery = self.repository.create_validation_open_query(
                page_state_id=page_state_id,
                field_template_id=item.field_template_id,
                validation_rule_id=item.rule_id,
                field_key=item.field_key,
                question_text=message_text,
                severity=self._normalize_query_severity(item.severity),
                actor_user_id=actor_user_id,
                now=now,
            )
            self.repository.create_query_thread_message(
                dataquery_id=dataquery.pk,
                message_text=message_text,
                message_type=_QUERY_THREAD_COMMENT,
                actor_user_id=actor_user_id,
                now=now,
                source=_QUERY_THREAD_SOURCE_SYSTEM,
            )
            query_count += 1
        return {
            "soft_issue_count": soft_issue_count,
            "query_count": query_count,
        }

    def reconcile_import_validation(
        self, *, page_state_id, crf_template_id, entry_id, data_version,
        failures, passed_rules, evaluated_values_json, actor_user_id,
    ):
        from apps.audit.public import AuditContextAdapter

        audit = AuditContextAdapter()
        normalized = [self._normalize_validation_failure(item) for item in failures]
        audit_event = audit.record_event(
            action="file_import_validation", object_type="page_state", object_id=str(page_state_id),
            actor_user_id=actor_user_id,
            after_data={"entry_id": entry_id, "values": evaluated_values_json,
                        "failures": [item.__dict__ for item in normalized], "passed_rules": passed_rules},
        )
        result = self.create_validation_failure_records(
            page_state_id=page_state_id, crf_template_id=crf_template_id,
            failures=[item for item in normalized if item.mode != "QUERY"],
            actor_user_id=actor_user_id, evaluated_values_json=evaluated_values_json,
            data_version=data_version, related_audit_event_id=audit_event.pk,
            source=ReconcileValidationRunSourceChoices.FILE_IMPORT, page_entry_id=entry_id,
            passed_rule_ids={item["rule_id"] for item in passed_rules},
        )
        active_queries = self.repository.list_active_system_validation_queries(page_state_id=page_state_id)
        active_signatures = {(item["field_template_id"], item["validation_rule_id"]) for item in active_queries}
        now = timezone.now()
        for failure in normalized:
            signature = (failure.field_template_id, failure.rule_id)
            if failure.mode != "QUERY" or failure.field_template_id is None or signature in active_signatures:
                continue
            query = self.repository.create_validation_open_query(
                page_state_id=page_state_id, field_template_id=failure.field_template_id,
                validation_rule_id=failure.rule_id, field_key=failure.field_key,
                question_text=failure.message or "Validation rule failed.",
                severity=self._normalize_query_severity(failure.severity),
                actor_user_id=actor_user_id, now=now, page_entry_id=entry_id,
            )
            self.repository.create_query_thread_message(
                dataquery_id=query.pk, message_text=failure.message or "Validation rule failed.",
                message_type=_QUERY_THREAD_COMMENT, actor_user_id=actor_user_id, now=now,
                source=_QUERY_THREAD_SOURCE_SYSTEM,
            )
            audit.record_event(action="query_open", object_type="dataquery", object_id=str(query.pk),
                actor_user_id=actor_user_id, after_data={"status": "open", "source": "file_import",
                    "entry_id": entry_id, "rule_id": failure.rule_id, "value": failure.failed_value})
            active_signatures.add(signature)
            result["query_count"] += 1
        passed_signatures = {(item["field_template_id"], item["rule_id"]) for item in passed_rules}
        result["closed_query_count"] = 0
        for query in active_queries:
            if (query["field_template_id"], query["validation_rule_id"]) not in passed_signatures:
                continue
            if not self.repository.close_passing_system_validation_query(
                dataquery_id=query["id"], page_state_id=page_state_id, actor_user_id=actor_user_id, now=now,
            ):
                continue
            message = "Imported value passed the validation rule; system query closed."
            self.repository.create_query_thread_message(
                dataquery_id=query["id"], message_text=message, message_type=_QUERY_THREAD_STATUS_CHANGE,
                actor_user_id=actor_user_id, now=now, source=_QUERY_THREAD_SOURCE_SYSTEM,
            )
            audit.record_event(action="query_close", object_type="dataquery", object_id=str(query["id"]),
                actor_user_id=actor_user_id, before_data={"status": query["status"]},
                after_data={"status": "closed", "source": "file_import", "entry_id": entry_id,
                    "rule_id": query["validation_rule_id"], "values": evaluated_values_json})
            result["closed_query_count"] += 1
        return result

    def acknowledge_validation_issues(
        self,
        *,
        page_state_id: int,
        issues: list[dict[str, object]],
        actor_user_id: int | None,
        current_failures: list[object] | None = None,
    ) -> dict[str, object]:
        normalized_issues: list[dict[str, object]] = []
        for item in issues:
            if not isinstance(item, dict):
                continue
            issue_id = self._to_int_or_none(item.get("issue_id") or item.get("id"))
            comment = str(item.get("comment") or item.get("acknowledgement_comment") or "").strip()
            if issue_id is None:
                continue
            if not comment:
                raise ValueError("Acknowledgement comment is required.")
            normalized_issues.append({"issue_id": issue_id, "comment": comment})
        if not normalized_issues:
            return {"acknowledged_issue_ids": [], "acknowledged_count": 0}
        now = timezone.now()
        page_state_data_version = self.repository.get_page_state_data_version(page_state_id=page_state_id)
        validation_run = self.repository.create_validation_run(
            page_state_id=page_state_id,
            source=ReconcileValidationRunSourceChoices.VALIDATION_ISSUE_ACKNOWLEDGEMENT,
            data_version=page_state_data_version,
            actor_user_id=actor_user_id,
            now=now,
            related_audit_event_id=None,
        )
        acknowledged_issue_ids = self.repository.acknowledge_validation_issues(
            page_state_id=page_state_id,
            items=normalized_issues,
            actor_user_id=actor_user_id,
            now=now,
            validation_run_id=int(validation_run.pk),
            **({"current_failures": [self._normalize_validation_failure(item).__dict__ for item in current_failures]}
               if current_failures is not None else {}),
        )
        return {
            "acknowledged_issue_ids": acknowledged_issue_ids,
            "acknowledged_count": len(acknowledged_issue_ids),
        }

    def correct_resolved_validation_issues(
        self,
        *,
        page_state_id: int,
        crf_template_id: int,
        changed_field_keys: list[str],
        values_by_field_key: dict[str, object],
        failures: list[object],
        actor_user_id: int | None,
    ) -> dict[str, object]:
        if not changed_field_keys or not values_by_field_key:
            return {"corrected_issue_ids": [], "corrected_count": 0}

        field_key_to_id = self.repository.list_field_key_to_id(crf_template_id=crf_template_id)
        field_id_to_value: dict[int, object] = {}
        for raw_field_key in changed_field_keys:
            field_key = self._canonical_field_key(raw_field_key)
            field_template_id = self._resolve_field_template_id(
                canonical_field_key=field_key,
                field_key_to_id=field_key_to_id,
            )
            if field_template_id is None or field_key not in values_by_field_key:
                continue
            field_id_to_value[field_template_id] = values_by_field_key[field_key]

        if not field_id_to_value:
            return {"corrected_issue_ids": [], "corrected_count": 0}

        active_issues = self.repository.list_active_validation_issues_by_page_state_and_field_templates(
            page_state_id=page_state_id,
            field_template_ids=tuple(field_id_to_value),
        )
        if not active_issues:
            return {"corrected_issue_ids": [], "corrected_count": 0}

        active_failure_signatures = {
            (item.field_template_id, item.rule_id)
            for item in (self._normalize_validation_failure(failure) for failure in failures)
            if item.mode == "SOFT" and item.field_template_id is not None and item.rule_id is not None
        }
        field_contexts = self.repository.list_field_thread_value_contexts(
            crf_template_id=crf_template_id,
            field_template_ids=tuple(field_id_to_value),
        )
        now: datetime = timezone.now()
        corrected_issue_ids: list[int] = []
        for issue in active_issues:
            field_template_id = self._to_int_or_none(issue.get("field_template_id"))
            rule_id = self._to_int_or_none(issue.get("rule_id"))
            issue_id = self._to_int_or_none(issue.get("id"))
            if field_template_id is None or issue_id is None:
                continue
            if rule_id is not None and (field_template_id, rule_id) in active_failure_signatures:
                continue
            old_value = self._format_thread_value(
                issue.get("failed_value"),
                field_contexts.get(field_template_id),
            ) or "—"
            new_value = self._format_thread_value(
                field_id_to_value.get(field_template_id),
                field_contexts.get(field_template_id),
            ) or "—"
            correction_comment = f"Cập nhật dữ liệu từ {old_value} thành {new_value}"
            corrected = self.repository.mark_validation_issue_corrected(
                issue_id=issue_id,
                page_state_id=page_state_id,
                actor_user_id=actor_user_id,
                correction_comment=correction_comment,
                now=now,
            )
            if corrected:
                corrected_issue_ids.append(issue_id)
        return {
            "corrected_issue_ids": corrected_issue_ids,
            "corrected_count": len(corrected_issue_ids),
        }

    def create_change_reason_data_queries(
        self,
        *,
        page_state_id: int,
        crf_template_id: int,
        reasons: list[ReconcileChangeReasonItem],
        actor_user_id: int | None,
    ) -> int:
        normalized_reasons: list[ReconcileChangeReasonItem] = []
        for item in reasons:
            field_key = self._canonical_field_key(item.field_key)
            reason = str(item.reason or "").strip()
            if not field_key or not reason:
                continue
            normalized_reasons.append(
                ReconcileChangeReasonItem(
                    field_key=field_key,
                    field_label=str(item.field_label or "").strip(),
                    reason=reason,
                )
            )

        if not normalized_reasons:
            return 0

        field_key_to_id = self.repository.list_field_key_to_id(crf_template_id=crf_template_id)
        now: datetime = timezone.now()
        create_items: list[dict[str, object]] = []
        for item in normalized_reasons:
            field_template_id = self._resolve_field_template_id(
                canonical_field_key=item.field_key,
                field_key_to_id=field_key_to_id,
            )
            create_items.append(
                {
                    "field_template_id": field_template_id,
                    "reason": item.reason,
                    "resolution_note": (item.field_label or item.field_key),
                },
            )
        return self.repository.bulk_create_manual_open_queries(
            page_state_id=page_state_id,
            items=create_items,
            actor_user_id=actor_user_id,
            now=now,
        )

    def add_update_value_threads_for_changed_fields(
        self,
        *,
        page_state_id: int,
        crf_template_id: int,
        values_by_field_key: dict[str, object],
        actor_user_id: int | None,
    ) -> int:
        if not values_by_field_key:
            return 0

        field_key_to_id = self.repository.list_field_key_to_id(crf_template_id=crf_template_id)
        field_id_to_value: dict[int, object] = {}
        for raw_field_key, value in values_by_field_key.items():
            field_key = self._canonical_field_key(raw_field_key)
            field_template_id = self._resolve_field_template_id(
                canonical_field_key=field_key,
                field_key_to_id=field_key_to_id,
            )
            if field_template_id is None:
                continue
            field_id_to_value[field_template_id] = value

        query_ids_by_field_template = self.repository.list_latest_open_query_ids_by_page_state_and_field_templates(
            page_state_id=page_state_id,
            field_template_ids=tuple(field_id_to_value),
        )
        if not query_ids_by_field_template:
            return 0

        field_contexts = self.repository.list_field_thread_value_contexts(
            crf_template_id=crf_template_id,
            field_template_ids=tuple(query_ids_by_field_template),
        )
        now: datetime = timezone.now()
        created_count = 0
        for field_template_id, dataquery_id in query_ids_by_field_template.items():
            formatted_value = self._format_thread_value(
                field_id_to_value.get(field_template_id),
                field_contexts.get(field_template_id),
            )
            answered = self.repository.mark_query_answered(
                dataquery_id=dataquery_id,
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                actor_user_id=actor_user_id,
                now=now,
            )
            if not answered:
                continue
            self.repository.create_query_thread_message(
                dataquery_id=dataquery_id,
                message_text=self._format_system_update_value_thread_message(formatted_value),
                message_type=_QUERY_THREAD_COMMENT,
                actor_user_id=actor_user_id,
                now=now,
                source=_QUERY_THREAD_SOURCE_SYSTEM,
            )
            created_count += 1
        return created_count

    def open_query(
        self,
        *,
        page_state_id: int,
        field_template_id: int,
        message_text: str,
        actor_user_id: int | None,
        field_key: str = "",
    ) -> dict[str, object]:
        normalized_text = str(message_text or "").strip()
        if not normalized_text:
            raise ValueError("Message text is required.")
        if self.repository.has_open_query_for_page_field(
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            field_key=field_key,
        ):
            raise ValueError("An open query already exists for this field.")
        now: datetime = timezone.now()
        dataquery = self.repository.create_manual_open_query(
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            field_key=field_key,
            question_text=normalized_text,
            actor_user_id=actor_user_id,
            now=now,
        )
        thread = self.repository.create_query_thread_message(
            dataquery_id=dataquery.pk,
            message_text=normalized_text,
            message_type=_QUERY_THREAD_COMMENT,
            actor_user_id=actor_user_id,
            now=now,
        )
        return {
            "dataquery_id": dataquery.pk,
            "field_template_id": field_template_id,
            "message_text": thread.message_text,
            "message_type": thread.message_type,
            "created_at": (
                timezone.localtime(thread.created_at)
                if timezone.is_aware(thread.created_at)
                else thread.created_at
            ),
        }

    def reply_to_query(
        self,
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        message_text: str,
        actor_user_id: int | None,
    ) -> dict[str, object]:
        normalized_text = str(message_text or "").strip()
        if not normalized_text:
            raise ValueError("Message text is required.")
        if not self.repository.query_belongs_to_scope(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
        ):
            raise ValueError("Query does not belong to the current field.")
        now: datetime = timezone.now()
        answered = self.repository.mark_query_answered(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            actor_user_id=actor_user_id,
            now=now,
        )
        if not answered:
            return {
                "dataquery_id": dataquery_id,
                "message_text": "",
                "message_type": "",
                "status": "answered",
                "changed": False,
            }
        thread = self.repository.create_query_thread_message(
            dataquery_id=dataquery_id,
            message_text=normalized_text,
            message_type=_QUERY_THREAD_COMMENT,
            actor_user_id=actor_user_id,
            now=now,
        )
        return {
            "dataquery_id": dataquery_id,
            "message_text": thread.message_text,
            "message_type": thread.message_type,
            "created_at": (
                timezone.localtime(thread.created_at)
                if timezone.is_aware(thread.created_at)
                else thread.created_at
            ),
            "status": "answered",
            "changed": True,
            "closed": False,
        }

    def query_action_scope(self, *, dataquery_id: int) -> dict[str, object] | None:
        return self.repository.get_query_action_scope(dataquery_id=dataquery_id)

    def resolve_query(
        self,
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        message_text: str,
        actor_user_id: int | None,
    ) -> dict[str, object]:
        normalized_text = str(message_text or "").strip()
        if not normalized_text:
            raise ValueError("Resolution note is required.")
        if not self.repository.query_belongs_to_scope(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
        ):
            raise ValueError("Query does not belong to the current field.")
        now: datetime = timezone.now()
        resolved = self.repository.resolve_query(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            resolution_note=normalized_text,
            actor_user_id=actor_user_id,
            now=now,
        )
        if not resolved:
            return {
                "dataquery_id": dataquery_id,
                "message_text": "",
                "message_type": "",
                "status": "resolved",
                "changed": False,
            }
        thread = self.repository.create_query_thread_message(
            dataquery_id=dataquery_id,
            message_text=normalized_text,
            message_type=_QUERY_THREAD_RESOLUTION,
            actor_user_id=actor_user_id,
            now=now,
        )
        return self._action_result(
            dataquery_id=dataquery_id,
            thread=thread,
            status="resolved",
            changed=resolved,
        )

    def close_resolved_query(
        self,
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        message_text: str,
        actor_user_id: int | None,
    ) -> dict[str, object]:
        if not self.repository.query_belongs_to_scope(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
        ):
            raise ValueError("Query does not belong to the current field.")
        now: datetime = timezone.now()
        normalized_text = str(message_text or "").strip() or "Query closed."
        closed = self.repository.close_resolved_query(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            actor_user_id=actor_user_id,
            now=now,
        )
        if not closed:
            return {
                "dataquery_id": dataquery_id,
                "message_text": "",
                "message_type": "",
                "status": "closed",
                "changed": False,
            }
        thread = self.repository.create_query_thread_message(
            dataquery_id=dataquery_id,
            message_text=normalized_text,
            message_type=_QUERY_THREAD_STATUS_CHANGE,
            actor_user_id=actor_user_id,
            now=now,
        )
        return self._action_result(
            dataquery_id=dataquery_id,
            thread=thread,
            status="closed",
            changed=closed,
        )

    def reopen_query(
        self,
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        message_text: str,
        actor_user_id: int | None,
    ) -> dict[str, object]:
        normalized_text = str(message_text or "").strip()
        if not normalized_text:
            raise ValueError("Reopen reason is required.")
        if not self.repository.query_belongs_to_scope(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
        ):
            raise ValueError("Query does not belong to the current field.")
        now: datetime = timezone.now()
        opened = self.repository.reopen_query(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            actor_user_id=actor_user_id,
            now=now,
        )
        if not opened:
            return {
                "dataquery_id": dataquery_id,
                "message_text": "",
                "message_type": "",
                "status": "open",
                "changed": False,
            }
        thread = self.repository.create_query_thread_message(
            dataquery_id=dataquery_id,
            message_text=normalized_text,
            message_type=_QUERY_THREAD_STATUS_CHANGE,
            actor_user_id=actor_user_id,
            now=now,
        )
        return self._action_result(
            dataquery_id=dataquery_id,
            thread=thread,
            status="open",
            changed=opened,
        )

    def request_clarification(
        self,
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        message_text: str,
        actor_user_id: int | None,
    ) -> dict[str, object]:
        normalized_text = str(message_text or "").strip()
        if not normalized_text:
            raise ValueError("Clarification reason is required.")
        if not self.repository.query_belongs_to_scope(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
        ):
            raise ValueError("Query does not belong to the current field.")
        now: datetime = timezone.now()
        opened = self.repository.request_clarification(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            actor_user_id=actor_user_id,
            now=now,
        )
        if not opened:
            return {
                "dataquery_id": dataquery_id,
                "message_text": "",
                "message_type": "",
                "status": "open",
                "changed": False,
            }
        thread = self.repository.create_query_thread_message(
            dataquery_id=dataquery_id,
            message_text=normalized_text,
            message_type=_QUERY_THREAD_STATUS_CHANGE,
            actor_user_id=actor_user_id,
            now=now,
        )
        return self._action_result(
            dataquery_id=dataquery_id,
            thread=thread,
            status="open",
            changed=opened,
        )

    def cancel_dataquery(
        self,
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int | None,
        message_text: str,
        actor_user_id: int | None,
    ) -> dict[str, object]:
        normalized_text = str(message_text or "").strip()
        if not normalized_text:
            raise ValueError("Cancellation reason is required.")
        if not self.repository.query_belongs_to_scope(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
        ):
            raise ValueError("Query does not belong to the current field.")
        now: datetime = timezone.now()
        cancelled = self.repository.cancel_query(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            actor_user_id=actor_user_id,
            now=now,
        )
        if not cancelled:
            return {
                "dataquery_id": dataquery_id,
                "message_text": "",
                "message_type": "",
                "status": "cancelled",
                "changed": False,
            }
        thread = self.repository.create_query_thread_message(
            dataquery_id=dataquery_id,
            message_text=normalized_text,
            message_type=_QUERY_THREAD_STATUS_CHANGE,
            actor_user_id=actor_user_id,
            now=now,
        )
        return self._action_result(
            dataquery_id=dataquery_id,
            thread=thread,
            status="cancelled",
            changed=cancelled,
        )

    @staticmethod
    def _action_result(*, dataquery_id: int, thread, status: str, changed: bool) -> dict[str, object]:
        return {
            "dataquery_id": dataquery_id,
            "message_text": thread.message_text,
            "message_type": thread.message_type,
            "created_at": (
                timezone.localtime(thread.created_at)
                if timezone.is_aware(thread.created_at)
                else thread.created_at
            ),
            "status": status,
            "changed": changed,
        }

    def cancel_query(
        self,
        *,
        dataquery_id: int,
        page_state_id: int,
        field_template_id: int,
        actor_user_id: int | None,
    ) -> dict[str, object]:
        if not self.repository.query_belongs_to_scope(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
        ):
            raise ValueError("Query does not belong to the current field.")
        if not self.repository.user_can_respond_to_query(
            dataquery_id=dataquery_id,
            actor_user_id=actor_user_id,
        ):
            raise ValueError(_QUERY_RESPONSE_PERMISSION_ERROR)
        now: datetime = timezone.now()
        cancelled = self.repository.cancel_query(
            dataquery_id=dataquery_id,
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            actor_user_id=actor_user_id,
            now=now,
        )
        return {
            "dataquery_id": dataquery_id,
            "message_text": "",
            "message_type": "cancelled",
            "created_at": timezone.localtime(now) if timezone.is_aware(now) else now,
            "closed": False,
            "cancelled": cancelled,
        }

    @classmethod
    def _format_thread_value(cls, value, field_context: dict[str, object] | None = None) -> str:
        if field_context and field_context.get("options"):
            label = cls._format_option_label_value(value, field_context.get("options"))
            if label:
                return label
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _format_system_update_value_thread_message(formatted_value: str) -> str:
        if not formatted_value:
            return "Update value to"
        return f"Update value to **{formatted_value}**"

    @classmethod
    def _format_option_label_value(cls, value, raw_options) -> str:
        label_by_value = cls._option_label_by_value(raw_options)
        if not label_by_value:
            return ""
        tokens = cls._value_tokens(value)
        labels = [label_by_value.get(token, token) for token in tokens]
        return ", ".join(label for label in labels if label)

    @classmethod
    def _option_label_by_value(cls, raw_options) -> dict[str, str]:
        static_options = cls._static_options(raw_options)
        label_by_value: dict[str, str] = {}
        for option in static_options:
            if not isinstance(option, dict):
                continue
            label = str(option.get("label") or "").strip()
            option_value = str(option.get("value") or label).strip()
            if option_value and label:
                label_by_value[option_value] = label
        return label_by_value

    @classmethod
    def _static_options(cls, raw_options):
        if not raw_options:
            return []
        parsed_options = raw_options
        if isinstance(raw_options, str):
            stripped_options = raw_options.strip()
            try:
                parsed_options = json.loads(stripped_options)
            except (TypeError, json.JSONDecodeError):
                parsed_options = stripped_options
        if isinstance(parsed_options, dict) and parsed_options.get("source") == "static":
            return parsed_options.get("static") or []
        return []

    @staticmethod
    def _value_tokens(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if not text:
            return []
        return [part.strip() for part in text.split(",") if part.strip()]
