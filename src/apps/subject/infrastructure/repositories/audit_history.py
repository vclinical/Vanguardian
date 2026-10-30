import json

from django.db.models import CharField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Cast, Coalesce, Concat

from apps.audit.public import list_file_import_events
from apps.identity.models import User
from apps.subject.models import (
    Subject,
    SubjectEventInstance,
    SubjectEventInstanceTransitionLog,
    SubjectIdentifierHistory,
    SubjectPeriodTransitionLog,
    SubjectPeriodTransitionOverride,
    SubjectStatusHistory,
)


class DjangoSubjectAuditHistoryRepository:
    def list_import_field_history(self, *, study_id, subject_id):
        visits = dict(SubjectEventInstance.objects.filter(
            subject_id=subject_id, subject__study_id=study_id, deleted=False,
        ).values_list("id", "event_code_snapshot"))
        rows = []
        for event in list_file_import_events(visit_ids=visits):
            try:
                payload = json.loads(event["after_data"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict) or not isinstance(payload.get("changes"), list):
                continue
            for change in payload["changes"]:
                if not isinstance(change, dict) or change.get("status") not in {"new", "overwrite"}:
                    continue
                if payload.get("field_decisions", {}).get(change.get("key")) == "ecrf":
                    continue
                before = "" if change.get("old_value") is None else str(change["old_value"])
                after = "" if change.get("new_value") is None else str(change["new_value"])
                rows.append({
                    "occurred_at": event["created_at"], "category": "file_import", "source": "File Import",
                    "field_name": change.get("field_key", ""),
                    "field_description": " / ".join(filter(None, [visits.get(int(event["object_id"])),
                        change.get("form_code"), change.get("field_label")])),
                    "value": f"{before} → {after}", "from_value": before, "to_value": after,
                    "user_display": event["created_by__display_name"] or event["created_by__username"]
                        or f"User #{event['created_by_id']}",
                    "action": "Item data value imported (draft)", "reason": payload.get("reason", ""),
                    "details": [{"label": "File", "value": payload.get("filename", "")},
                        {"label": "Row", "value": str(payload.get("row_number", ""))},
                        {"label": "Column", "value": change.get("column", "")},
                        {"label": "Status", "value": "Imported into draft"}],
                })
        return rows

    def get_subject_context(self, *, study_id: int, subject_id: int, snapshot_class):
        subject = (
            Subject.objects.filter(study_id=study_id, pk=subject_id, deleted=False)
            .select_related("study", "site")
            .first()
        )
        if subject is None:
            return None
        return snapshot_class(
            subject_id=int(subject.pk),
            study_id=int(subject.study_id),
            study_code=getattr(subject.study, "code", "") or "",
            study_name=getattr(subject.study, "name", "") or "",
            site_code=getattr(subject.site, "code", "") or "",
            screening_code=subject.screening_code or "",
            subject_code=subject.subject_code or "",
        )

    def list_subject_status_history(
        self,
        *,
        subject_id: int,
        record_class,
        limit: int = 200,
        search: str = "",
        field_name: str = "",
    ):
        queryset = self._annotate_audit_fields(
            SubjectStatusHistory.objects.filter(subject_id=subject_id),
            actor_field="changed_by_id",
            field_name="subject_status",
            field_description=Value("Subject status transition", output_field=CharField()),
            value_expression=Concat(
                Coalesce("from_status", Value("")),
                Value(" "),
                Coalesce("to_status", Value("")),
                Value(" "),
                Coalesce("reason_code", Value("")),
                Value(" "),
                Coalesce("reason_text", Value("")),
                Value(" "),
                Coalesce("source", Value("")),
                output_field=CharField(),
            ),
        )
        queryset = self._apply_audit_filters(queryset, search=search, field_name=field_name)
        queryset = queryset.order_by("-transition_at", "-id")
        if limit:
            queryset = queryset[:limit]
        return [
            record_class(
                occurred_at=row.transition_at,
                field_name=row.audit_field_name,
                field_description=row.audit_field_description,
                value=row.audit_value,
                user_display=row.audit_user_display,
                from_status=row.from_status or "",
                to_status=row.to_status or "",
                reason_code=row.reason_code or "",
                reason_text=row.reason_text or "",
                source=row.source or "",
                actor_id=row.changed_by_id,
            )
            for row in queryset
        ]

    def list_subject_identifier_history(
        self,
        *,
        subject_id: int,
        record_class,
        limit: int = 200,
        search: str = "",
        field_name: str = "",
    ):
        queryset = self._annotate_audit_fields(
            SubjectIdentifierHistory.objects.filter(subject_id=subject_id),
            actor_field="actor_user_id",
            field_name="subject_identifier",
            field_description=Concat(
                Value("Identifier assignment: "),
                Coalesce("identifier_type", Value("")),
                output_field=CharField(),
            ),
            value_expression=Concat(
                Coalesce("from_value", Value("")),
                Value(" "),
                Coalesce("to_value", Value("")),
                Value(" "),
                Coalesce("assignment_source", Value("")),
                output_field=CharField(),
            ),
        )
        queryset = self._apply_audit_filters(
            queryset,
            search=search,
            field_name=field_name,
        ).order_by("-occurred_at", "-id")
        if limit:
            queryset = queryset[:limit]
        return [
            record_class(
                occurred_at=row.occurred_at,
                field_name=row.audit_field_name,
                field_description=row.audit_field_description,
                value=row.audit_value,
                user_display=row.audit_user_display,
                identifier_type=row.identifier_type,
                from_value=row.from_value or "",
                to_value=row.to_value,
                assignment_source=row.assignment_source,
                related_randomization_event_id=(
                    row.related_randomization_event_id
                ),
                actor_id=row.actor_user_id,
            )
            for row in queryset
        ]

    def list_event_instance_transition_history(
        self,
        *,
        study_id: int,
        subject_id: int,
        record_class,
        limit: int = 200,
        search: str = "",
        field_name: str = "",
    ):
        queryset = self._annotate_audit_fields(
            SubjectEventInstanceTransitionLog.objects.filter(
                study_id=study_id,
                subject_id=subject_id,
                deleted=False,
            )
            .select_related(
                "from_event_definition",
                "to_event_definition",
                "source_event_instance",
                "target_event_instance",
                "transition_rule",
            )
            ,
            actor_field="created_by_id",
            field_name="event_transition",
            field_description=Concat(
                Coalesce("source_event_instance__event_name_snapshot", "from_event_definition__name", Value("")),
                Value(" -> "),
                Coalesce("target_event_instance__event_name_snapshot", "to_event_definition__name", Value("")),
                output_field=CharField(),
            ),
            value_expression=Concat(
                Coalesce("from_status", Value("")),
                Value(" "),
                Coalesce("to_status", Value("")),
                Value(" "),
                Coalesce("result", Value("")),
                Value(" "),
                Coalesce("reason", Value("")),
                Value(" "),
                Coalesce("trigger_source", Value("")),
                Value(" "),
                Coalesce("facts_json", Value("")),
                output_field=CharField(),
            ),
        )
        queryset = self._apply_audit_filters(queryset, search=search, field_name=field_name)
        queryset = queryset.order_by("-created_at", "-id")
        if limit:
            queryset = queryset[:limit]

        return [
            record_class(
                occurred_at=row.created_at,
                field_name=row.audit_field_name,
                field_description=row.audit_field_description,
                value=row.audit_value,
                user_display=row.audit_user_display,
                from_event_label=self._event_label(row.source_event_instance, row.from_event_definition),
                to_event_label=self._event_label(row.target_event_instance, row.to_event_definition),
                from_status=row.from_status or "",
                to_status=row.to_status or "",
                trigger_source=row.trigger_source or "",
                result=row.result or "",
                reason=row.reason or "",
                actor_id=row.created_by_id,
                transition_rule_id=row.transition_rule_id,
            )
            for row in queryset
        ]

    def list_period_transition_history(
        self,
        *,
        subject_id: int,
        record_class,
        limit: int = 200,
        search: str = "",
        field_name: str = "",
    ):
        queryset = self._annotate_audit_fields(
            SubjectPeriodTransitionLog.objects.filter(
                subject_id=subject_id,
                deleted=False,
            ).select_related(
                "period",
                "source_event_instance__event_definition",
            ),
            actor_field="actor_user_id",
            field_name="period_status",
            field_description=Concat(
                Value("Period "),
                Cast("period__period_no", output_field=CharField()),
                Value(" / "),
                Coalesce("period__treatment_code", Value("")),
                output_field=CharField(),
            ),
            value_expression=Concat(
                Coalesce("from_status", Value("")),
                Value(" "),
                Coalesce("to_status", Value("")),
                Value(" "),
                Coalesce("reason", Value("")),
                Value(" "),
                Coalesce("trigger_source", Value("")),
                Value(" "),
                Coalesce("facts_json", Value("")),
                output_field=CharField(),
            ),
        )
        queryset = self._apply_audit_filters(
            queryset,
            search=search,
            field_name=field_name,
        )
        queryset = queryset.order_by("-created_at", "-id")
        if limit:
            queryset = queryset[:limit]

        rows = list(queryset)
        override_ids = {
            override_id
            for row in rows
            if (override_id := self._period_override_id(row.facts_json)) is not None
        }
        overrides_by_id = SubjectPeriodTransitionOverride.objects.filter(
            id__in=override_ids,
            subject_id=subject_id,
            deleted=False,
        ).in_bulk()

        return [
            record_class(
                occurred_at=row.created_at,
                field_name=row.audit_field_name,
                field_description=row.audit_field_description,
                value=row.audit_value,
                user_display=row.audit_user_display,
                period_no=row.period.period_no,
                treatment_code=row.period.treatment_code or "",
                from_status=row.from_status or "",
                to_status=row.to_status or "",
                trigger_source=row.trigger_source or "",
                reason=row.reason or "",
                actor_id=row.actor_user_id,
                source_event_label=self._event_label(
                    row.source_event_instance,
                    getattr(row.source_event_instance, "event_definition", None),
                ),
                facts_json=row.facts_json or "",
                override_reason_code=getattr(
                    overrides_by_id.get(
                        self._period_override_id(row.facts_json)
                    ),
                    "reason_code",
                    "",
                )
                or "",
                override_reason_text=getattr(
                    overrides_by_id.get(
                        self._period_override_id(row.facts_json)
                    ),
                    "reason_text",
                    "",
                )
                or "",
            )
            for row in rows
        ]

    @classmethod
    def _annotate_audit_fields(
        cls,
        queryset,
        *,
        actor_field: str,
        field_name: str,
        field_description,
        value_expression,
    ):
        return queryset.annotate(
            audit_field_name=Value(field_name, output_field=CharField()),
            audit_field_description=field_description,
            audit_value=value_expression,
            audit_user_display=cls._user_display_expression(actor_field),
        )

    @classmethod
    def _apply_audit_filters(cls, queryset, *, search: str = "", field_name: str = ""):
        normalized_field_name = str(field_name or "").strip()
        if normalized_field_name:
            queryset = queryset.filter(audit_field_name__icontains=normalized_field_name)

        for term in cls._search_terms(search):
            queryset = queryset.filter(
                Q(audit_value__icontains=term)
                | Q(audit_field_description__icontains=term)
                | Q(audit_user_display__icontains=term)
            )
        return queryset

    @staticmethod
    def _search_terms(search: str) -> tuple[str, ...]:
        normalized_search = str(search or "").strip()
        if not normalized_search:
            return ()
        return tuple(term for term in normalized_search.split() if term)

    @staticmethod
    def _period_override_id(facts_json: str | None) -> int | None:
        try:
            facts = json.loads(facts_json or "{}")
            override_id = facts.get("override_id")
            return int(override_id) if override_id is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

    @staticmethod
    def _user_display_expression(actor_field: str):
        user_display = (
            User.objects.filter(pk=OuterRef(actor_field), deleted=False)
            .annotate(
                audit_display=Concat(
                    Coalesce("display_name", Value("")),
                    Value(" "),
                    Coalesce("first_name", Value("")),
                    Value(" "),
                    Coalesce("last_name", Value("")),
                    Value(" "),
                    Coalesce("username", Value("")),
                    output_field=CharField(),
                )
            )
            .values("audit_display")[:1]
        )
        return Coalesce(
            Cast(Subquery(user_display), output_field=CharField()),
            Value("System"),
            output_field=CharField(),
        )

    @staticmethod
    def _event_label(event_instance, event_definition) -> str:
        if event_instance is not None:
            label = (
                event_instance.event_name_snapshot
                or getattr(event_definition, "name", "")
                or event_instance.event_code_snapshot
                or getattr(event_definition, "code", "")
            )
            if event_instance.repeat_index and int(event_instance.repeat_index) > 1:
                return f"{label} #{event_instance.repeat_index}" if label else f"Repeat #{event_instance.repeat_index}"
            return label or ""
        return getattr(event_definition, "name", "") or getattr(event_definition, "code", "") or ""


__all__ = ["DjangoSubjectAuditHistoryRepository"]
