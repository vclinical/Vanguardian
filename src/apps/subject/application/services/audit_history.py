import json
from dataclasses import dataclass
from datetime import datetime

from apps.subject.infrastructure.repositories.audit_history import (
    DjangoSubjectAuditHistoryRepository,
)


@dataclass(frozen=True)
class SubjectAuditHistorySubjectDTO:
    subject_id: int
    study_id: int
    study_code: str
    study_name: str
    site_code: str
    screening_code: str
    subject_code: str


@dataclass(frozen=True)
class SubjectStatusAuditHistoryRecordDTO:
    occurred_at: datetime | None
    field_name: str
    field_description: str
    value: str
    user_display: str
    from_status: str
    to_status: str
    reason_code: str
    reason_text: str
    source: str
    actor_id: int | None


@dataclass(frozen=True)
class SubjectIdentifierAuditHistoryRecordDTO:
    occurred_at: datetime | None
    field_name: str
    field_description: str
    value: str
    user_display: str
    identifier_type: str
    from_value: str
    to_value: str
    assignment_source: str
    related_randomization_event_id: int | None
    actor_id: int | None


@dataclass(frozen=True)
class SubjectEventTransitionAuditHistoryRecordDTO:
    occurred_at: datetime | None
    field_name: str
    field_description: str
    value: str
    user_display: str
    from_event_label: str
    to_event_label: str
    from_status: str
    to_status: str
    trigger_source: str
    result: str
    reason: str
    actor_id: int | None
    transition_rule_id: int | None


@dataclass(frozen=True)
class SubjectPeriodTransitionAuditHistoryRecordDTO:
    occurred_at: datetime | None
    field_name: str
    field_description: str
    value: str
    user_display: str
    period_no: int
    treatment_code: str
    from_status: str
    to_status: str
    trigger_source: str
    reason: str
    actor_id: int | None
    source_event_label: str
    facts_json: str
    override_reason_code: str
    override_reason_text: str


class SubjectAuditHistoryQueryService:
    repository_class = DjangoSubjectAuditHistoryRepository
    source_order = (
        "subject_identifier",
        "subject_status",
        "period_transition",
        "event_transition",
        "page_state",
        "event_gate",
        "file_import",
    )

    def __init__(
        self,
        repository=None,
        datacapture_history_reader=None,
        study_gate_history_reader=None,
    ):
        self.repository = repository or self.repository_class()
        if datacapture_history_reader is None:
            from apps.datacapture.public import list_page_state_transition_history_for_subject

            datacapture_history_reader = list_page_state_transition_history_for_subject
        if study_gate_history_reader is None:
            from apps.study.public import list_event_gate_evaluation_history_for_subject

            study_gate_history_reader = list_event_gate_evaluation_history_for_subject
        self.datacapture_history_reader = datacapture_history_reader
        self.study_gate_history_reader = study_gate_history_reader

    def get_subject_audit_history(
        self,
        *,
        study_id: int,
        subject_id: int,
        limit_per_source: int = 0,
        search: str = "",
        field_name: str = "",
        user: str = "",
    ) -> dict | None:
        subject = self.repository.get_subject_context(
            study_id=study_id,
            subject_id=subject_id,
            snapshot_class=SubjectAuditHistorySubjectDTO,
        )
        if subject is None:
            return None

        records = self.repository.list_import_field_history(study_id=study_id, subject_id=subject_id)
        records.extend(
            self._build_subject_identifier_records(
                subject_id=subject_id,
                limit=limit_per_source,
            )
        )
        records.extend(
            self._build_subject_status_records(
                subject_id=subject_id,
                limit=limit_per_source,
            )
        )
        records.extend(
            self._build_event_transition_records(
                study_id=study_id,
                subject_id=subject_id,
                limit=limit_per_source,
            )
        )
        records.extend(
            self._build_period_transition_records(
                subject_id=subject_id,
                limit=limit_per_source,
            )
        )
        records.extend(
            self.datacapture_history_reader(
                subject_id=subject_id,
                limit=limit_per_source,
            )
        )
        records.extend(
            self.study_gate_history_reader(
                study_id=study_id,
                subject_id=subject_id,
                limit=limit_per_source,
            )
        )
        normalized_records = [self._normalize_record(record) for record in records]
        user_options = self._build_user_options(normalized_records)
        normalized_records = self._filter_records(
            normalized_records,
            search=search,
            field_name=field_name,
            user=user,
        )
        normalized_records.sort(
            key=lambda record: (
                record["occurred_at"] is not None,
                record["occurred_at"],
            ),
            reverse=True,
        )

        return {
            "subject_id": subject.subject_id,
            "study_id": subject.study_id,
            "study_code": subject.study_code,
            "study_name": subject.study_name,
            "site_code": subject.site_code,
            "screening_code": subject.screening_code,
            "subject_code": subject.subject_code,
            "title": subject.subject_code or subject.screening_code or "Subject Audit History",
            "subtitle": "Audit History",
            "total_count": len(normalized_records),
            "source_counts": self._build_source_counts(normalized_records),
            "search": str(search or "").strip(),
            "field_name": str(field_name or "").strip(),
            "user": str(user or "").strip(),
            "user_options": user_options,
            "records": normalized_records,
        }

    def _build_subject_identifier_records(
        self,
        *,
        subject_id: int,
        limit: int,
    ) -> list[dict]:
        rows = self.repository.list_subject_identifier_history(
            subject_id=subject_id,
            record_class=SubjectIdentifierAuditHistoryRecordDTO,
            limit=limit,
        )
        return [
            {
                "occurred_at": row.occurred_at,
                "category": "subject_identifier",
                "source": "Subject Identifier",
                "field_name": row.field_name,
                "field_description": row.field_description,
                "value": row.value,
                "user_display": row.user_display or self._format_actor(row.actor_id),
                "scope": self._humanize_value(row.identifier_type),
                "action": "Identifier assigned",
                "from_value": row.from_value,
                "to_value": row.to_value,
                "actor": self._format_actor(row.actor_id),
                "reason": "",
                "details": [
                    {
                        "label": "Assignment Source",
                        "value": self._humanize_value(row.assignment_source),
                    },
                    {
                        "label": "Related Randomization Event",
                        "value": str(row.related_randomization_event_id or ""),
                    },
                ],
            }
            for row in rows
        ]

    def _build_subject_status_records(
        self,
        *,
        subject_id: int,
        limit: int,
        search: str = "",
        field_name: str = "",
    ) -> list[dict]:
        rows = self.repository.list_subject_status_history(
            subject_id=subject_id,
            record_class=SubjectStatusAuditHistoryRecordDTO,
            limit=limit,
            search=search,
            field_name=field_name,
        )
        return [
            {
                "occurred_at": row.occurred_at,
                "category": "subject_status",
                "source": "Subject Status",
                "field_name": row.field_name,
                "field_description": row.field_description,
                "value": row.value or self._status_value(row),
                "user_display": row.user_display or self._format_actor(row.actor_id),
                "scope": "Subject",
                "action": "Subject status transition",
                "from_value": self._humanize_value(row.from_status),
                "to_value": self._humanize_value(row.to_status),
                "actor": self._format_actor(row.actor_id),
                "reason": self._format_reason(row.reason_code, row.reason_text),
                "details": [
                    {"label": "Source", "value": self._humanize_value(row.source)},
                ]
                if row.source
                else [],
            }
            for row in rows
        ]

    def _build_event_transition_records(
        self,
        *,
        study_id: int,
        subject_id: int,
        limit: int,
        search: str = "",
        field_name: str = "",
    ) -> list[dict]:
        rows = self.repository.list_event_instance_transition_history(
            study_id=study_id,
            subject_id=subject_id,
            record_class=SubjectEventTransitionAuditHistoryRecordDTO,
            limit=limit,
            search=search,
            field_name=field_name,
        )
        return [
            {
                "occurred_at": row.occurred_at,
                "category": "event_transition",
                "source": "Event Transition",
                "field_name": row.field_name,
                "field_description": row.field_description,
                "value": row.value or self._event_transition_value(row),
                "user_display": row.user_display or self._format_actor(row.actor_id),
                "scope": self._join_parts(row.from_event_label, row.to_event_label, separator=" -> "),
                "action": self._humanize_value(row.result) or "Event transition",
                "from_value": self._join_parts(row.from_event_label, self._humanize_value(row.from_status)),
                "to_value": self._join_parts(row.to_event_label, self._humanize_value(row.to_status)),
                "actor": self._format_actor(row.actor_id),
                "reason": row.reason or "",
                "details": self._event_transition_details(row),
            }
            for row in rows
        ]

    def _build_period_transition_records(
        self,
        *,
        subject_id: int,
        limit: int,
        search: str = "",
        field_name: str = "",
    ) -> list[dict]:
        rows = self.repository.list_period_transition_history(
            subject_id=subject_id,
            record_class=SubjectPeriodTransitionAuditHistoryRecordDTO,
            limit=limit,
            search=search,
            field_name=field_name,
        )
        return [
            {
                "occurred_at": row.occurred_at,
                "category": "period_transition",
                "source": "Period Transition",
                "field_name": row.field_name,
                "field_description": row.field_description,
                "value": self._period_transition_value(row),
                "user_display": (
                    row.user_display or self._format_actor(row.actor_id)
                ),
                "scope": self._period_label(row),
                "action": "Period status transition",
                "from_value": self._join_parts(
                    f"Period {row.period_no}",
                    self._humanize_value(row.from_status),
                ),
                "to_value": self._join_parts(
                    f"Period {row.period_no}",
                    self._humanize_value(row.to_status),
                ),
                "actor": self._format_actor(row.actor_id),
                "reason": (
                    self._format_reason(
                        row.override_reason_code,
                        row.override_reason_text,
                    )
                    or self._humanize_value(row.reason)
                ),
                "details": self._period_transition_details(row),
            }
            for row in rows
        ]

    @classmethod
    def _period_transition_details(
        cls,
        row: SubjectPeriodTransitionAuditHistoryRecordDTO,
    ) -> list[dict[str, str]]:
        details = [
            ("Trigger Source", cls._humanize_value(row.trigger_source)),
            ("Source Event", row.source_event_label),
            ("Transition Reason", cls._humanize_value(row.reason)),
        ]
        facts = cls._parse_json_object(row.facts_json)
        fact_labels = (
            ("override_id", "Override ID"),
            ("period_end_at", "Period End At"),
            ("next_period_start_at", "Next Period Start At"),
            ("pending_form_count", "Pending Form Count"),
            ("pending_data_acknowledged", "Pending Data Acknowledged"),
            ("clinical_transition_confirmed", "Clinical Transition Confirmed"),
            ("end_event_status", "End Event Status"),
        )
        details.extend(
            (label, cls._format_fact_value(facts.get(key)))
            for key, label in fact_labels
        )
        return [
            {"label": label, "value": str(value)}
            for label, value in details
            if cls._has_value(value)
        ]

    @classmethod
    def _event_transition_details(
        cls,
        row: SubjectEventTransitionAuditHistoryRecordDTO,
    ) -> list[dict[str, str]]:
        details = [
            ("Trigger Source", cls._humanize_value(row.trigger_source)),
            ("Transition Rule ID", row.transition_rule_id),
        ]
        return [
            {"label": label, "value": str(value)}
            for label, value in details
            if cls._has_value(value)
        ]

    @classmethod
    def _status_value(cls, row: SubjectStatusAuditHistoryRecordDTO) -> str:
        return cls._join_parts(
            cls._humanize_value(row.from_status),
            cls._humanize_value(row.to_status),
            cls._format_reason(row.reason_code, row.reason_text),
            cls._humanize_value(row.source),
        )

    @classmethod
    def _event_transition_value(cls, row: SubjectEventTransitionAuditHistoryRecordDTO) -> str:
        return cls._join_parts(
            cls._humanize_value(row.from_status),
            cls._humanize_value(row.to_status),
            cls._humanize_value(row.result),
            row.reason,
            cls._humanize_value(row.trigger_source),
        )

    @classmethod
    def _period_transition_value(
        cls,
        row: SubjectPeriodTransitionAuditHistoryRecordDTO,
    ) -> str:
        return cls._join_parts(
            cls._humanize_value(row.from_status),
            cls._humanize_value(row.to_status),
            cls._humanize_value(row.reason),
            cls._humanize_value(row.trigger_source),
        )

    @classmethod
    def _period_label(
        cls,
        row: SubjectPeriodTransitionAuditHistoryRecordDTO,
    ) -> str:
        return cls._join_parts(
            f"Period {row.period_no}",
            row.treatment_code,
        )

    @classmethod
    def _build_source_counts(cls, records: list[dict]) -> list[dict[str, object]]:
        labels = {
            "subject_identifier": "Subject Identifier",
            "subject_status": "Subject Status",
            "period_transition": "Period Transition",
            "event_transition": "Event Transition",
            "page_state": "Page State",
            "event_gate": "Event Gate",
            "file_import": "File Import",
        }
        counts = {key: 0 for key in cls.source_order}
        for record in records:
            category = record.get("category") or ""
            if category not in counts:
                counts[category] = 0
            counts[category] += 1
        return [
            {
                "key": key,
                "label": labels.get(key, cls._humanize_value(key)),
                "count": counts.get(key, 0),
            }
            for key in cls.source_order
        ]

    @classmethod
    def _filter_records(
        cls,
        records: list[dict],
        *,
        search: str = "",
        field_name: str = "",
        user: str = "",
    ) -> list[dict]:
        normalized_field_name = str(field_name or "").strip().casefold()
        normalized_user = str(user or "").strip().casefold()
        search_terms = tuple(
            term.casefold()
            for term in str(search or "").strip().split()
            if term
        )

        filtered_records = []
        for record in records:
            if normalized_field_name and normalized_field_name not in record["field_name"].casefold():
                continue
            if normalized_user and normalized_user != record["user_display"].casefold():
                continue
            detail_text = cls._details_search_text(record)
            if search_terms and not all(term in detail_text for term in search_terms):
                continue
            filtered_records.append(record)
        return filtered_records

    @staticmethod
    def _build_user_options(records: list[dict]) -> list[str]:
        values = {
            str(record.get("user_display") or "").strip()
            for record in records
            if str(record.get("user_display") or "").strip()
        }
        return sorted(values, key=str.casefold)

    @staticmethod
    def _details_search_text(record: dict) -> str:
        parts = []
        reason = str(record.get("reason") or "").strip()
        if reason:
            parts.extend(("Reason", reason))
        for detail in record.get("details", []):
            parts.extend(
                (
                    str(detail.get("label") or "").strip(),
                    str(detail.get("value") or "").strip(),
                )
            )
        return " ".join(part for part in parts if part).casefold()

    @classmethod
    def _normalize_record(cls, record: dict) -> dict:
        return {
            "occurred_at": record.get("occurred_at"),
            "category": str(record.get("category") or "").strip(),
            "source": str(record.get("source") or "").strip(),
            "field_name": str(record.get("field_name") or "").strip(),
            "field_description": str(record.get("field_description") or "").strip(),
            "value": str(record.get("value") or "").strip(),
            "user_display": str(record.get("user_display") or record.get("actor") or "").strip(),
            "scope": str(record.get("scope") or "").strip(),
            "action": str(record.get("action") or "").strip(),
            "from_value": str(record.get("from_value") or "").strip(),
            "to_value": str(record.get("to_value") or "").strip(),
            "actor": str(record.get("actor") or "").strip(),
            "reason": str(record.get("reason") or "").strip(),
            "details": [
                {
                    "label": str(detail.get("label") or "").strip(),
                    "value": str(detail.get("value") or "").strip(),
                }
                for detail in record.get("details", [])
                if str(detail.get("label") or "").strip()
                and str(detail.get("value") or "").strip()
            ],
        }

    @staticmethod
    def _format_actor(actor_id) -> str:
        if actor_id is None:
            return "System"
        return f"User #{actor_id}"

    @classmethod
    def _format_reason(cls, reason_code: str | None, reason_text: str | None) -> str:
        code = cls._humanize_value(reason_code)
        text = str(reason_text or "").strip()
        if code and text:
            return f"{code}: {text}"
        return code or text

    @staticmethod
    def _parse_json_object(value: str) -> dict[str, object]:
        try:
            parsed = json.loads(value or "{}")
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _format_fact_value(value) -> str:
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _humanize_value(value) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return text.replace("_", " ").replace("-", " ").title()

    @staticmethod
    def _join_parts(*parts, separator: str = " / ") -> str:
        return separator.join(str(part).strip() for part in parts if str(part or "").strip())

    @staticmethod
    def _has_value(value) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return True


__all__ = [
    "SubjectAuditHistoryQueryService",
    "SubjectAuditHistorySubjectDTO",
    "SubjectEventTransitionAuditHistoryRecordDTO",
    "SubjectIdentifierAuditHistoryRecordDTO",
    "SubjectPeriodTransitionAuditHistoryRecordDTO",
    "SubjectStatusAuditHistoryRecordDTO",
]
