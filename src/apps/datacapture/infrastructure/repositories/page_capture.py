import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.db.models import CharField, Max, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Cast, Coalesce, Concat
from django.utils.translation import get_language

from apps.core.choices import (
    DataCaptureFieldReviewStatusChoices,
    DataCaptureFieldReviewTypeChoices,
    DataCapturePageEntryStatusChoices,
    DataCapturePageStateStatusChoices,
)
from apps.core.form_data_document import (
    FieldTemplateSnapshot,
    FormTemplateSnapshot,
    SectionTemplateSnapshot,
    flatten_form_data_for_export,
    is_canonical_form_data,
    iter_field_values,
    normalize_form_data,
    prune_empty_form_data_groups,
)
from apps.crf.models import (
    CrfFieldLookup,
    CrfFieldTemplate,
    CrfFieldValidationRule,
    CrfFieldValidationRuleModeChoices,
    CrfFieldValidationRuleTypeChoices,
    CrfSectionTemplate,
    CrfTemplate,
)
from apps.datacapture.infrastructure.models.capture import (
    DataCapturePageEntrySnapshot,
    DataCapturePageStateSnapshot,
    SubmitExecutionPlan,
)
from apps.datacapture.models import (
    DataCaptureFieldEntry,
    DataCaptureFieldReview,
    DataCapturePageEntry,
    DataCapturePageState,
    DataCapturePageStateTransitionLog,
    DataCaptureSectionInstance,
)
from apps.identity.models import User
from apps.study.models import EventFormBinding
from apps.subject.models import SubjectEventInstance


class DjangoDataCapturePageRepository:
    EMPTY_PAGE_STATE_FINAL_DATA = json.dumps(normalize_form_data(None), ensure_ascii=False, sort_keys=True)
    FINAL_DATA_STATUSES = frozenset(
        {
            DataCapturePageStateStatusChoices.VERIFIED,
            DataCapturePageStateStatusChoices.CERTIFIED,
            DataCapturePageStateStatusChoices.LOCKED,
            DataCapturePageStateStatusChoices.FINALIZED,
        }
    )
    LOOKUP_LABELS_PAYLOAD_KEY = "_field_lookup_labels"
    REPEAT_KEY_RE = re.compile(r"^(?P<base>.+)__repeat_(?P<repeat_index>\d+)$")
    DATE_PART_KEY_RE = re.compile(r"^(?P<base>.+)__(?P<part>day|month|year|time)$")

    def _resolve_event_form_binding_id_for_scope(
        self,
        *,
        visit_id: int,
        crf_template_id: int,
    ) -> int | None:
        visit = (
            SubjectEventInstance.objects.filter(
                pk=visit_id,
                deleted=False,
            )
            .only("id", "study_id", "study_version", "event_definition_id")
            .first()
        )
        if visit is None:
            return None
        binding = (
            EventFormBinding.objects.filter(
                study_id=visit.study_id,
                study_version=visit.study_version,
                event_definition_id=visit.event_definition_id,
                form_definition_id=crf_template_id,
                deleted=False,
                is_enabled=True,
            )
            .only("id")
            .order_by("display_order", "id")
            .first()
        )
        if binding is None:
            return None
        return int(binding.pk)

    @staticmethod
    def _visit_repeat_index(*, visit_id: int) -> int:
        repeat_index = (
            SubjectEventInstance.objects.filter(
                pk=visit_id,
                deleted=False,
            )
            .values_list("repeat_index", flat=True)
            .first()
        )
        return max(1, int(repeat_index or 1))

    def _resolve_page_state_repeat_index_for_scope(
        self,
        *,
        visit_id: int,
        event_form_binding_id: int | None,
    ) -> int:
        if event_form_binding_id is None:
            return self._visit_repeat_index(visit_id=visit_id)
        binding = (
            EventFormBinding.objects.filter(
                pk=event_form_binding_id,
                deleted=False,
            )
            .only("id", "is_repeatable_within_event")
            .first()
        )
        if binding is None or not bool(getattr(binding, "is_repeatable_within_event", False)):
            return self._visit_repeat_index(visit_id=visit_id)
        return 1

    def _resolve_default_page_state_repeat_index_for_scope(
        self,
        *,
        visit_id: int,
    ) -> int:
        return self._visit_repeat_index(visit_id=visit_id)

    def ensure_page_state_binding_context(self, page_state, *, actor_user_id: int | None = None):
        if page_state is None:
            return None
        update_fields: list[str] = []
        resolved_binding_id = getattr(page_state, "event_form_binding_id", None)
        if not resolved_binding_id:
            resolved_binding_id = self._resolve_event_form_binding_id_for_scope(
                visit_id=int(page_state.visit_id),
                crf_template_id=int(page_state.crf_template_id),
            )
            if resolved_binding_id is not None:
                page_state.event_form_binding_id = resolved_binding_id
                update_fields.append("event_form_binding_id")
        binding = getattr(page_state, "event_form_binding", None)
        visit = getattr(page_state, "visit", None)
        if resolved_binding_id and binding is not None and visit is not None:
            expected_repeat_index = (
                1
                if bool(getattr(binding, "is_repeatable_within_event", False))
                else max(1, int(getattr(visit, "repeat_index", 1) or 1))
            )
        else:
            expected_repeat_index = self._resolve_page_state_repeat_index_for_scope(
                visit_id=int(page_state.visit_id),
                event_form_binding_id=resolved_binding_id,
            )
        if int(getattr(page_state, "repeat_index", 0) or 0) != expected_repeat_index:
            page_state.repeat_index = expected_repeat_index
            update_fields.append("repeat_index")
        if not getattr(page_state, "instance_key", ""):
            page_state.instance_key = uuid4().hex
            update_fields.append("instance_key")
        if update_fields:
            page_state.updated_at = self._now()
            page_state.updated_by_id = actor_user_id
            update_fields.extend(["updated_at", "updated_by_id"])
            page_state.save(update_fields=update_fields)
        return page_state

    @staticmethod
    def _page_state_scope_filter_kwargs(
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        event_form_binding_id: int | None = None,
    ) -> dict:
        kwargs = {
            "subject_id": subject_id,
            "visit_id": visit_id,
        }
        if event_form_binding_id is not None:
            kwargs["event_form_binding_id"] = event_form_binding_id
        else:
            kwargs["crf_template_id"] = crf_template_id
        return kwargs

    @classmethod
    def _page_entry_scope_filter_kwargs(
        cls,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        event_form_binding_id: int | None = None,
    ) -> dict:
        if event_form_binding_id is None:
            return cls._page_state_scope_filter_kwargs(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                event_form_binding_id=None,
            )
        return {
            "subject_id": subject_id,
            "visit_id": visit_id,
            "page_state__event_form_binding_id": event_form_binding_id,
        }

    @staticmethod
    def _normalize_field_key_list(field_keys: list[str] | tuple[str, ...]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for field_key in field_keys or []:
            key = str(field_key or "").strip()
            if not key or key in seen:
                continue
            normalized.append(key)
            seen.add(key)
        return normalized

    @classmethod
    def _field_alias_base_key(cls, raw_key: str) -> str:
        key = str(raw_key or "").strip()
        date_part_match = cls.DATE_PART_KEY_RE.match(key)
        if date_part_match:
            key = date_part_match.group("base")
        repeat_match = cls.REPEAT_KEY_RE.match(key)
        if repeat_match:
            key = repeat_match.group("base")
        return key

    def _map_changed_field_keys_by_template_id(
        self,
        *,
        crf_template_id: int,
        changed_field_keys: list[str] | tuple[str, ...],
    ) -> dict[int, list[str]]:
        normalized_keys = self._normalize_field_key_list(changed_field_keys)
        if not normalized_keys:
            return {}
        changed_keys_by_base: dict[str, list[str]] = {}
        for key in normalized_keys:
            changed_keys_by_base.setdefault(self._field_alias_base_key(key), []).append(key)
        matched_keys_by_template_id: dict[int, list[str]] = {}
        fields = CrfFieldTemplate.objects.filter(
            crf_template_id=crf_template_id,
            deleted=False,
        ).only("id", "field_key")
        for field in fields:
            field_template_id = int(field.id)
            field_key = str(field.field_key or "").strip()
            aliases = [alias for alias in (field_key, f"field_{field_template_id}") if alias]
            matched_keys: list[str] = []
            for alias in aliases:
                matched_keys.extend(changed_keys_by_base.get(alias, []))
            if not matched_keys:
                continue
            matched_keys_by_template_id[field_template_id] = matched_keys
        return matched_keys_by_template_id

    def get_page_entry_by_id(
        self,
        *,
        page_entry_id: int,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        event_form_binding_id: int | None = None,
    ):
        return (
            DataCapturePageEntry.objects.filter(
                pk=page_entry_id,
                **self._page_entry_scope_filter_kwargs(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    event_form_binding_id=event_form_binding_id,
                ),
                deleted=False,
            )
            .only("id", "data", "entry_version", "status", "updated_by_id")
            .first()
        )

    def list_form_field_validation_rules(self, *, crf_template_id: int, include_hard: bool = False) -> dict[str, tuple[dict[str, object], ...]]:
        validation_rules_qs = CrfFieldValidationRule.objects.filter(
            deleted=False,
            mode__in=(
                CrfFieldValidationRuleModeChoices.SOFT,
                CrfFieldValidationRuleModeChoices.QUERY,
                *([CrfFieldValidationRuleModeChoices.HARD] if include_hard else []),
            ),
            rule_type__in=(
                CrfFieldValidationRuleTypeChoices.CUSTOM_EXPRESSION,
                CrfFieldValidationRuleTypeChoices.REQUIRED,
            ),
        ).only(
            "id",
            "field_template_id",
            "mode",
            "rule_type",
            "severity",
            "expression",
        ).prefetch_related("translations")
        fields = (
            CrfFieldTemplate.objects.filter(
                crf_template_id=crf_template_id,
                deleted=False,
                is_active=True,
            )
            .only("id", "field_key")
            .prefetch_related(Prefetch("validation_rules", queryset=validation_rules_qs))
            .order_by("id")
        )
        output: dict[str, tuple[dict[str, object], ...]] = {}
        for field in fields:
            field_key = str(field.field_key or "").strip()
            if not field_key:
                continue
            rules = tuple(
                {
                    "id": int(rule.pk),
                    "field_template_id": int(rule.field_template_id),
                    "mode": str(rule.mode or "").strip(),
                    "rule_type": str(rule.rule_type or "").strip(),
                    "severity": str(rule.severity or "").strip(),
                    "expression": str(rule.expression or "").strip(),
                    "message": (
                        rule.safe_translation_getter("message", default="", any_language=True)
                        if hasattr(rule, "safe_translation_getter")
                        else ""
                    ),
                }
                for rule in field.validation_rules.all()
                if str(rule.rule_type or "").strip()
            )
            output[field_key] = rules
        return output

    @classmethod
    def _build_validation_form_rows(cls, payload_map: dict[str, object]) -> list[dict[str, object]]:
        row_map: dict[int, dict[str, object]] = {}
        for raw_key, value in (payload_map or {}).items():
            normalized_key = str(raw_key or "").strip()
            if not normalized_key or normalized_key == "__form_verification__":
                continue
            base_key = cls._field_alias_base_key(normalized_key)
            repeat_match = cls.REPEAT_KEY_RE.match(base_key)
            repeat_index = int(repeat_match.group("repeat_index")) if repeat_match else 1
            field_key = repeat_match.group("base") if repeat_match else base_key
            row_map.setdefault(repeat_index, {})[field_key] = value
        return [row_map[idx] for idx in sorted(row_map)]

    def get_form_repeatability_by_codes(self, *, form_codes: tuple[str, ...]) -> dict[str, bool]:
        normalized_codes = tuple(str(code or "").strip() for code in form_codes if str(code or "").strip())
        if not normalized_codes:
            return {}
        rows = (
            CrfSectionTemplate.objects.filter(
                crf_template__code__in=normalized_codes,
                deleted=False,
                crf_template__deleted=False,
            )
            .values("crf_template__code")
            .annotate(has_repeatable=Max("is_repeatable"))
        )
        return {
            str(row["crf_template__code"]): bool(row["has_repeatable"])
            for row in rows
            if str(row.get("crf_template__code") or "").strip()
        }

    def get_subject_cross_form_validation_context(
        self,
        *,
        subject_id: int,
        form_codes: tuple[str, ...] = (),
        exclude_visit_id: int | None = None,
        exclude_crf_template_id: int | None = None,
    ) -> dict[str, dict[str, object]]:
        normalized_codes = tuple(str(code or "").strip() for code in form_codes if str(code or "").strip())
        repeatability_by_code = self.get_form_repeatability_by_codes(form_codes=normalized_codes)
        context: dict[str, dict[str, object]] = {
            form_code: {
                "is_repeatable": bool(repeatability_by_code.get(form_code)),
                "rows": [],
            }
            for form_code in normalized_codes
        }
        if not normalized_codes:
            return context
        page_states = (
            DataCapturePageState.objects.select_related("crf_template", "current_entry")
            .filter(
                subject_id=subject_id,
                deleted=False,
                crf_template__code__in=normalized_codes,
                crf_template__deleted=False,
            )
        )
        if exclude_visit_id is not None and exclude_crf_template_id is not None:
            page_states = page_states.exclude(
                visit_id=exclude_visit_id,
                crf_template_id=exclude_crf_template_id,
            )
        page_states = page_states.only(
            "id",
            "visit_id",
            "crf_template_id",
            "crf_template__code",
            "final_data",
            "current_entry_id",
            "current_entry__data",
        ).order_by("crf_template__code", "visit_id", "id")

        for page_state in page_states:
            form_code = str(getattr(getattr(page_state, "crf_template", None), "code", "") or "").strip()
            if not form_code:
                continue
            raw_payload = ""
            current_entry = getattr(page_state, "current_entry", None)
            if current_entry is not None and str(getattr(current_entry, "data", "") or "").strip():
                raw_payload = current_entry.data
            elif str(page_state.final_data or "").strip():
                raw_payload = page_state.final_data
            if not raw_payload:
                continue
            parsed_payload = self._load_json_map(raw_payload)
            if not parsed_payload:
                continue
            flattened_payload = flatten_form_data_for_export(
                normalize_form_data(parsed_payload, strict=False),
                repeat_strategy="legacy_repeat_suffix",
            )
            context.setdefault(
                form_code,
                {
                    "is_repeatable": bool(repeatability_by_code.get(form_code)),
                    "rows": [],
                },
            )
            context[form_code]["rows"].extend(self._build_validation_form_rows(flattened_payload))
        return context

    @staticmethod
    def _load_json_map(raw_payload: str | None) -> dict:
        try:
            payload = json.loads(raw_payload or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def get_form_template_snapshot(self, *, crf_template_id: int) -> FormTemplateSnapshot | None:
        crf_template = (
            CrfTemplate.objects.filter(pk=crf_template_id, deleted=False)
            .only("id", "code", "version")
            .first()
        )
        if crf_template is None:
            return None
        fields_by_section_id: dict[int, list[FieldTemplateSnapshot]] = {}
        fields = (
            CrfFieldTemplate.objects.filter(
                crf_template_id=crf_template_id,
                deleted=False,
                is_active=True,
                section_template_id__isnull=False,
            )
            .select_related("section_template")
            .only(
                "id",
                "field_key",
                "data_type",
                "display_order",
                "section_template_id",
                "section_template__section_code",
            )
            .order_by("section_template__display_order", "display_order", "id")
        )
        for field in fields:
            section_code = str(getattr(field.section_template, "section_code", "") or "").strip()
            field_key = str(field.field_key or "").strip()
            if not section_code or not field_key:
                continue
            fields_by_section_id.setdefault(int(field.section_template_id), []).append(
                FieldTemplateSnapshot(
                    field_key=field_key,
                    section_code=section_code,
                    data_type=str(field.data_type or "").strip() or None,
                    display_order=field.display_order,
                )
            )
        sections = (
            CrfSectionTemplate.objects.filter(
                crf_template_id=crf_template_id,
                deleted=False,
                is_enabled=True,
            )
            .only("id", "section_code", "is_repeatable", "display_order")
            .order_by("display_order", "id")
        )
        return FormTemplateSnapshot(
            form_code=str(crf_template.code or "").strip(),
            form_version=str(crf_template.version or "").strip(),
            sections=[
                SectionTemplateSnapshot(
                    section_code=str(section.section_code or "").strip(),
                    is_repeatable=bool(section.is_repeatable),
                    display_order=section.display_order,
                    fields=fields_by_section_id.get(int(section.pk), []),
                )
                for section in sections
                if str(section.section_code or "").strip()
            ],
        )

    def list_page_state_contexts(
        self,
        *,
        page_state_ids: tuple[int, ...] = (),
        study_id: int | None = None,
        site_id: int | None = None,
    ) -> list[dict[str, object]]:
        queryset = (
            DataCapturePageState.objects.filter(deleted=False)
            .select_related(
                "subject",
                "subject__site",
                "visit",
                "visit__event_definition",
                "crf_template",
            )
            .only(
                "id",
                "crf_template_id",
                "subject_id",
                "visit_id",
                "subject__study_id",
                "subject__site_id",
                "subject__subject_code",
                "subject__screening_code",
                "visit__event_code_snapshot",
                "visit__event_name_snapshot",
                "visit__event_definition__code",
                "visit__event_definition__name",
                "crf_template__code",
            )
        )
        if page_state_ids:
            queryset = queryset.filter(pk__in=page_state_ids)
        if study_id is not None:
            queryset = queryset.filter(subject__study_id=study_id)
        if site_id is not None:
            queryset = queryset.filter(subject__site_id=site_id)

        contexts: list[dict[str, object]] = []
        for page_state in queryset.order_by("id"):
            subject = page_state.subject
            visit = page_state.visit
            event_definition = getattr(visit, "event_definition", None)
            crf_template = page_state.crf_template
            crf_label = (
                crf_template.safe_translation_getter("name", default="", any_language=True)
                if hasattr(crf_template, "safe_translation_getter")
                else ""
            )
            contexts.append(
                {
                    "page_state_id": int(page_state.pk),
                    "study_id": int(subject.study_id) if subject.study_id else None,
                    "site_id": int(subject.site_id) if subject.site_id else None,
                    "subject_id": int(subject.pk) if subject.pk else None,
                    "subject_code": str(subject.subject_code or "").strip(),
                    "screening_code": str(subject.screening_code or "").strip(),
                    "event_instance_id": int(visit.pk) if visit.pk else None,
                    "event_code": str(visit.event_code_snapshot or getattr(event_definition, "code", "") or "").strip(),
                    "event_label": str(visit.event_name_snapshot or getattr(event_definition, "name", "") or "").strip(),
                    "crf_page_label": str(crf_label or crf_template.code or "").strip(),
                    "page_template_id": int(page_state.crf_template_id) if page_state.crf_template_id else None,
                }
            )
        return contexts

    def list_page_state_transition_history_for_subject(
        self,
        *,
        subject_id: int,
        limit: int = 200,
        search: str = "",
        field_name: str = "",
    ) -> list[dict[str, object]]:
        queryset = (
            DataCapturePageStateTransitionLog.objects.filter(
                page_state__subject_id=subject_id,
                page_state__deleted=False,
            )
            .select_related(
                "page_state",
                "page_state__visit",
                "page_state__visit__event_definition",
                "page_state__crf_template",
            )
            .annotate(
                audit_field_name=Value("page_state_status", output_field=CharField()),
                audit_field_description=Concat(
                    Coalesce("page_state__visit__event_name_snapshot", "page_state__visit__event_definition__name", Value("")),
                    Value(" / "),
                    Coalesce("page_state__crf_template__code", Value("")),
                    output_field=CharField(),
                ),
                audit_value=Concat(
                    Coalesce("from_status", Value("")),
                    Value(" "),
                    Coalesce("to_status", Value("")),
                    Value(" "),
                    Coalesce(Cast("data_version", output_field=CharField()), Value("")),
                    Value(" "),
                    Coalesce("reason_code", Value("")),
                    Value(" "),
                    Coalesce("reason_text", Value("")),
                    Value(" "),
                    Coalesce("trigger_source", Value("")),
                    Value(" "),
                    Coalesce("facts_json", Value("")),
                    output_field=CharField(),
                ),
                audit_user_display=self._audit_user_display_expression("actor_id"),
            )
        )
        queryset = self._apply_audit_history_filters(
            queryset,
            search=search,
            field_name=field_name,
        )
        queryset = queryset.order_by("-created_at", "-id")
        if limit:
            queryset = queryset[:limit]

        rows: list[dict[str, object]] = []
        for log in queryset:
            page_state = log.page_state
            visit = page_state.visit
            event_definition = getattr(visit, "event_definition", None)
            crf_template = page_state.crf_template
            rows.append(
                {
                    "occurred_at": log.created_at,
                    "field_name": str(log.audit_field_name or "").strip(),
                    "field_description": str(log.audit_field_description or "").strip(),
                    "value": str(log.audit_value or "").strip(),
                    "user_display": str(log.audit_user_display or "").strip(),
                    "page_state_id": int(page_state.pk),
                    "from_status": str(log.from_status or "").strip(),
                    "to_status": str(log.to_status or "").strip(),
                    "data_version": log.data_version,
                    "reason_code": str(log.reason_code or "").strip(),
                    "reason_text": str(log.reason_text or "").strip(),
                    "trigger_source": str(log.trigger_source or "").strip(),
                    "actor_id": log.actor_id,
                    "event_code": str(
                        visit.event_code_snapshot or getattr(event_definition, "code", "") or ""
                    ).strip(),
                    "event_label": str(
                        visit.event_name_snapshot
                        or getattr(event_definition, "name", "")
                        or visit.event_code_snapshot
                        or ""
                    ).strip(),
                    "form_code": str(getattr(crf_template, "code", "") or "").strip(),
                    "form_label": str(getattr(crf_template, "code", "") or "").strip(),
                    "repeat_index": page_state.repeat_index,
                }
            )
        return rows

    @classmethod
    def _apply_audit_history_filters(cls, queryset, *, search: str = "", field_name: str = ""):
        normalized_field_name = str(field_name or "").strip()
        if normalized_field_name:
            queryset = queryset.filter(audit_field_name__icontains=normalized_field_name)

        for term in cls._audit_history_search_terms(search):
            queryset = queryset.filter(
                Q(audit_value__icontains=term)
                | Q(audit_field_description__icontains=term)
                | Q(audit_user_display__icontains=term)
            )
        return queryset

    @staticmethod
    def _audit_history_search_terms(search: str) -> tuple[str, ...]:
        normalized_search = str(search or "").strip()
        if not normalized_search:
            return ()
        return tuple(term for term in normalized_search.split() if term)

    @staticmethod
    def _audit_user_display_expression(actor_field: str):
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

    def normalize_form_data_json_for_storage(
        self,
        *,
        crf_template_id: int,
        data: str,
        entry_version: str | int | None = None,
        strict: bool = True,
    ) -> str:
        payload = self._load_json_map(data)
        template_snapshot = self.get_form_template_snapshot(crf_template_id=crf_template_id)
        doc = normalize_form_data(
            payload,
            template_snapshot=template_snapshot,
            entry_version=entry_version,
            strict=strict,
        )
        doc = prune_empty_form_data_groups(doc)
        return json.dumps(doc, ensure_ascii=False, sort_keys=True)

    def flatten_form_data_json_for_read(self, *, data: str, crf_template_id: int | None = None) -> dict:
        payload = self._load_json_map(data)
        template_snapshot = (
            self.get_form_template_snapshot(crf_template_id=crf_template_id)
            if crf_template_id is not None and not is_canonical_form_data(payload)
            else None
        )
        doc = normalize_form_data(payload, template_snapshot=template_snapshot, strict=False)
        return flatten_form_data_for_export(doc, repeat_strategy="legacy_repeat_suffix")

    @classmethod
    def _split_payload_key(cls, raw_key: str) -> tuple[str, int, str | None]:
        key = str(raw_key or "").strip()
        date_part = None
        date_part_match = cls.DATE_PART_KEY_RE.match(key)
        if date_part_match:
            key = date_part_match.group("base")
            date_part = date_part_match.group("part")
        repeat_index = 1
        repeat_match = cls.REPEAT_KEY_RE.match(key)
        if repeat_match:
            key = repeat_match.group("base")
            repeat_index = int(repeat_match.group("repeat_index"))
        return key, repeat_index, date_part

    @staticmethod
    def _section_instance_key(*, section_template_id: int, repeat_index: int) -> str:
        return f"section_{section_template_id}_{repeat_index}"

    @staticmethod
    def _normalize_payload_scalar(raw_value):
        if raw_value is None:
            return None
        if isinstance(raw_value, str):
            return raw_value
        if isinstance(raw_value, bool):
            return "true" if raw_value else "false"
        if isinstance(raw_value, int | float | Decimal):
            return str(raw_value)
        return None

    @staticmethod
    def _parse_decimal(raw_value):
        if raw_value in (None, ""):
            return None
        try:
            return Decimal(str(raw_value).strip().replace(",", "."))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _parse_bool(raw_value):
        if raw_value in (None, ""):
            return None
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, int | float):
            return raw_value != 0
        normalized = str(raw_value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return None

    @staticmethod
    def _parse_date(raw_value):
        if raw_value in (None, ""):
            return None
        normalized = str(raw_value).strip()
        matched = re.match(r"^(\d{4})-(\d{2})-(\d{2})", normalized)
        if not matched:
            return None
        try:
            return date(
                int(matched.group(1)),
                int(matched.group(2)),
                int(matched.group(3)),
            )
        except ValueError:
            return None

    @classmethod
    def _field_entry_values(cls, *, raw_value, data_type: str) -> dict:
        normalized_type = str(data_type or "").strip().upper()
        values = {
            "value_text": cls._normalize_payload_scalar(raw_value),
            "value_json": None,
            "value_date": None,
            "value_number": None,
            "value_bool": None,
        }
        if isinstance(raw_value, list | dict):
            values["value_json"] = json.dumps(raw_value, ensure_ascii=False, sort_keys=True)
            return values
        if normalized_type in {"INTEGER", "DECIMAL", "NUMBER"}:
            values["value_number"] = cls._parse_decimal(raw_value)
            return values
        if normalized_type in {"DATE", "DATETIME"}:
            values["value_date"] = cls._parse_date(raw_value)
            return values
        if normalized_type == "BOOLEAN":
            values["value_bool"] = cls._parse_bool(raw_value)
            return values
        return values

    def _payload_values_by_field_repeat(self, *, crf_template_id: int, payload: dict) -> dict[tuple[int, int], object]:
        fields = CrfFieldTemplate.objects.filter(
            crf_template_id=crf_template_id,
            deleted=False,
            is_active=True,
        ).only("id", "field_key", "data_type", "section_template_id")
        field_by_alias: dict[str, CrfFieldTemplate] = {}
        for field in fields:
            field_key = str(field.field_key or "").strip()
            if field_key:
                field_by_alias[field_key] = field
            field_by_alias[f"field_{field.pk}"] = field

        values_by_field_repeat: dict[tuple[int, int], object] = {}
        if is_canonical_form_data(payload):
            for ref in iter_field_values(payload):
                field = field_by_alias.get(ref.field_key)
                if field is None:
                    continue
                values_by_field_repeat[(int(field.pk), int(ref.row_no or 1))] = ref.value
            return values_by_field_repeat

        date_parts_by_field_repeat: dict[tuple[int, int], dict[str, str]] = {}
        for raw_key, raw_value in payload.items():
            base_key, repeat_index, date_part = self._split_payload_key(raw_key)
            field = field_by_alias.get(base_key)
            if field is None:
                continue
            key = (int(field.pk), repeat_index)
            if date_part:
                date_parts_by_field_repeat.setdefault(key, {})[date_part] = str(raw_value or "").strip()
                continue
            values_by_field_repeat[key] = raw_value

        for key, parts in date_parts_by_field_repeat.items():
            if key in values_by_field_repeat:
                continue
            year = parts.get("year")
            month = parts.get("month")
            day = parts.get("day")
            if year and month and day:
                values_by_field_repeat[key] = f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}"
        return values_by_field_repeat

    def persist_entry_values_from_payload(
        self,
        *,
        page_entry_id: int,
        page_state_id: int,
        crf_template_id: int,
        data: str,
        actor_user_id: int | None = None,
    ) -> None:
        payload = self._load_json_map(data)
        if not payload:
            return
        payload.pop(self.LOOKUP_LABELS_PAYLOAD_KEY, None)
        values_by_field_repeat = self._payload_values_by_field_repeat(
            crf_template_id=crf_template_id,
            payload=payload,
        )
        now = self._now()
        touched_section_ids: set[int] = set()
        touched_field_ids: set[int] = set()
        fields = {
            int(field.pk): field
            for field in CrfFieldTemplate.objects.filter(
                id__in=[field_id for field_id, _repeat_index in values_by_field_repeat],
                deleted=False,
            ).only("id", "data_type", "section_template_id")
        }
        section_instance_by_key: dict[tuple[int, int], DataCaptureSectionInstance] = {}
        for (field_template_id, repeat_index), raw_value in values_by_field_repeat.items():
            field = fields.get(field_template_id)
            if field is None:
                continue
            section_instance = None
            if field.section_template_id:
                section_key = (int(field.section_template_id), repeat_index)
                section_instance = section_instance_by_key.get(section_key)
                if section_instance is None:
                    section_instance, _created = DataCaptureSectionInstance.objects.update_or_create(
                        page_entry_id=page_entry_id,
                        section_template_id=field.section_template_id,
                        repeat_index=repeat_index,
                        defaults={
                            "updated_at": now,
                            "deleted": False,
                            "page_state_id": page_state_id,
                            "instance_key": self._section_instance_key(
                                section_template_id=int(field.section_template_id),
                                repeat_index=repeat_index,
                            ),
                            "status": "active",
                            "updated_by_id": actor_user_id,
                        },
                        create_defaults={
                            "created_at": now,
                            "updated_at": now,
                            "deleted": False,
                            "page_state_id": page_state_id,
                            "instance_key": self._section_instance_key(
                                section_template_id=int(field.section_template_id),
                                repeat_index=repeat_index,
                            ),
                            "status": "active",
                            "created_by_id": actor_user_id,
                            "updated_by_id": actor_user_id,
                        },
                    )
                    section_instance_by_key[section_key] = section_instance
                touched_section_ids.add(int(section_instance.pk))

            field_values = self._field_entry_values(raw_value=raw_value, data_type=field.data_type)
            field_entry, _created = DataCaptureFieldEntry.objects.update_or_create(
                page_entry_id=page_entry_id,
                section_instance_id=section_instance.pk if section_instance is not None else None,
                field_template_id=field_template_id,
                defaults={
                    "updated_at": now,
                    "deleted": False,
                    "page_state_id": page_state_id,
                    "status": "active",
                    "updated_by_id": actor_user_id,
                    **field_values,
                },
                create_defaults={
                    "created_at": now,
                    "updated_at": now,
                    "deleted": False,
                    "page_state_id": page_state_id,
                    "status": "active",
                    "created_by_id": actor_user_id,
                    "updated_by_id": actor_user_id,
                    **field_values,
                },
            )
            touched_field_ids.add(int(field_entry.pk))

        stale_sections = DataCaptureSectionInstance.objects.filter(
            page_entry_id=page_entry_id,
            deleted=False,
        )
        if touched_section_ids:
            stale_sections = stale_sections.exclude(pk__in=touched_section_ids)
        stale_sections.update(deleted=True, updated_at=now, updated_by_id=actor_user_id)

        stale_fields = DataCaptureFieldEntry.objects.filter(
            page_entry_id=page_entry_id,
            deleted=False,
        )
        if touched_field_ids:
            stale_fields = stale_fields.exclude(pk__in=touched_field_ids)
        stale_fields.update(deleted=True, updated_at=now, updated_by_id=actor_user_id)

    @staticmethod
    def _normalize_ui_options(raw_options) -> dict:
        if isinstance(raw_options, dict):
            return raw_options
        if isinstance(raw_options, str):
            normalized = raw_options.strip()
            if normalized.startswith("{") and normalized.endswith("}"):
                try:
                    parsed = json.loads(normalized)
                except json.JSONDecodeError:
                    return {}
                return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _is_select2_control(raw_control_type) -> bool:
        return str(raw_control_type or "").strip().lower().replace(" ", "") == "select2"

    @staticmethod
    def _normalize_language_code(language_code) -> str:
        return str(language_code or "en").split("-")[0].strip().lower() or "en"

    @classmethod
    def _translated_ui_config_options(cls, ui_config):
        if ui_config is None:
            return None
        translations = list(getattr(getattr(ui_config, "translations", None), "all", lambda: [])())
        if not translations:
            return None
        translation_by_language = {
            cls._normalize_language_code(translation.language_code): translation
            for translation in translations
        }
        current_language = cls._normalize_language_code(get_language())
        for language_code in (current_language, "en"):
            translation = translation_by_language.get(language_code)
            if translation is None:
                continue
            if translation.options not in (None, ""):
                return translation.options
        for translation in translations:
            if translation.options not in (None, ""):
                return translation.options
        return None

    @staticmethod
    def _normalize_lookup_label(raw_value) -> str:
        return str(raw_value or "").strip()

    @classmethod
    def _normalize_lookup_value(cls, raw_label) -> str:
        return cls._normalize_lookup_label(raw_label).upper()

    def _select2_lookup_key_for_field(self, field) -> str:
        ui_config = getattr(field, "ui_config", None)
        if ui_config is None or not self._is_select2_control(ui_config.control_type):
            return ""
        options = self._normalize_ui_options(self._translated_ui_config_options(ui_config))
        if str(options.get("source") or "").strip().lower() != "lookup":
            return ""
        return str(options.get("lookup") or "").strip()

    @staticmethod
    def _first_matching_alias(aliases: list[str], payload: dict) -> str:
        return next((alias for alias in aliases if alias in payload), "")

    def _upsert_lookup_value(
        self,
        *,
        lookup_key: str,
        label: str,
        actor_user_id: int | None,
        now,
    ) -> str:
        value = self._normalize_lookup_value(label)
        lookup_row, created = CrfFieldLookup.objects.get_or_create(
            key=lookup_key,
            value=value,
            defaults={
                "created_at": now,
                "updated_at": now,
                "deleted": False,
                "label": label,
                "created_by_id": actor_user_id,
                "updated_by_id": actor_user_id,
            },
        )
        if not created and (lookup_row.label != label or lookup_row.deleted):
            lookup_row.label = label
            lookup_row.deleted = False
            lookup_row.updated_at = now
            lookup_row.updated_by_id = actor_user_id
            lookup_row.save(update_fields=["label", "deleted", "updated_at", "updated_by_id"])
        return value

    def persist_lookup_values_from_payload(
        self,
        *,
        crf_template_id: int,
        data: str,
        actor_user_id: int | None = None,
    ) -> str:
        payload = self._load_json_map(data)
        if not payload:
            return data

        raw_lookup_labels = payload.pop(self.LOOKUP_LABELS_PAYLOAD_KEY, {})
        lookup_labels = raw_lookup_labels if isinstance(raw_lookup_labels, dict) else {}
        fields = (
            CrfFieldTemplate.objects.filter(
                crf_template_id=crf_template_id,
                deleted=False,
                is_active=True,
            )
            .select_related("ui_config")
            .prefetch_related("ui_config__translations")
            .only(
                "id",
                "field_key",
                "ui_config__control_type",
            )
        )
        now = self._now()
        for field in fields:
            lookup_key = self._select2_lookup_key_for_field(field)
            if not lookup_key:
                continue

            aliases = [str(field.field_key or "").strip(), f"field_{field.pk}"]
            aliases = [alias for alias in aliases if alias]
            payload_alias = self._first_matching_alias(aliases, payload)
            label_alias = self._first_matching_alias(aliases, lookup_labels)
            if not payload_alias and not label_alias:
                continue

            label = self._normalize_lookup_label(lookup_labels.get(label_alias) if label_alias else payload.get(payload_alias))
            if not label:
                if payload_alias:
                    payload[payload_alias] = ""
                continue
            value = self._upsert_lookup_value(
                lookup_key=lookup_key,
                label=label,
                actor_user_id=actor_user_id,
                now=now,
            )
            if payload_alias:
                payload[payload_alias] = value

        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def get_page_state(self, *, subject_id: int, visit_id: int, crf_template_id: int, event_form_binding_id: int | None = None):
        page_state = (
            DataCapturePageState.objects.filter(
                **self._page_state_scope_filter_kwargs(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    event_form_binding_id=event_form_binding_id,
                ),
                deleted=False,
            )
            .only(
                "id",
                "status",
                "final_data",
                "data_version",
                "current_entry_id",
                "crf_template_id",
                "subject_id",
                "visit_id",
                "visit__study_id",
                "visit__study_version",
                "visit__event_definition_id",
                "subject__site_id",
            )
            .select_related("subject", "visit")
            .first()
        )
        if page_state is None:
            return None
        visit = page_state.visit
        return DataCapturePageStateSnapshot(
            id=page_state.pk,
            status=page_state.status,
            final_data=page_state.final_data,
            data_version=page_state.data_version,
            current_entry_id=page_state.current_entry_id,
            crf_template_id=page_state.crf_template_id,
            subject_id=page_state.subject_id,
            visit_id=page_state.visit_id,
            study_id=visit.study_id,
            study_version=visit.study_version,
            event_definition_id=visit.event_definition_id,
            site_id=page_state.subject.site_id,
        )

    def get_latest_entry(self, *, subject_id: int, visit_id: int, crf_template_id: int, event_form_binding_id: int | None = None):
        page_entry = (
            DataCapturePageEntry.objects.filter(
                **self._page_entry_scope_filter_kwargs(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    event_form_binding_id=event_form_binding_id,
                ),
                deleted=False,
            )
            .exclude(status__in=[DataCapturePageEntryStatusChoices.CANCELLED, "canceled"])
            .only(
                "id",
                "page_state_id",
                "parent_entry_id",
                "entry_no",
                "entry_kind",
                "entry_version",
                "status",
                "data",
                "crf_template_id",
                "subject_id",
                "visit_id",
                "updated_by_id",
                "updated_at",
            )
            .order_by("-entry_no", "-id")
            .first()
        )
        if page_entry is None:
            return None
        return DataCapturePageEntrySnapshot(
            id=page_entry.pk,
            page_state_id=page_entry.page_state_id,
            parent_entry_id=page_entry.parent_entry_id,
            entry_no=page_entry.entry_no,
            entry_kind=page_entry.entry_kind,
            entry_version=page_entry.entry_version,
            status=page_entry.status,
            data=page_entry.data,
            crf_template_id=page_entry.crf_template_id,
            subject_id=page_entry.subject_id,
            visit_id=page_entry.visit_id,
            updated_by_id=page_entry.updated_by_id,
            updated_at=page_entry.updated_at,
        )

    def get_latest_submitted_entry(self, *, subject_id: int, visit_id: int, crf_template_id: int, event_form_binding_id: int | None = None):
        page_entry = (
            DataCapturePageEntry.objects.filter(
                **self._page_entry_scope_filter_kwargs(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    event_form_binding_id=event_form_binding_id,
                ),
                deleted=False,
                status=DataCapturePageEntryStatusChoices.SUBMITTED,
            )
            .only(
                "id",
                "page_state_id",
                "parent_entry_id",
                "entry_no",
                "entry_kind",
                "entry_version",
                "status",
                "data",
                "crf_template_id",
                "subject_id",
                "visit_id",
                "updated_by_id",
                "updated_at",
            )
            .order_by("-entry_no", "-id")
            .first()
        )
        if page_entry is None:
            return None
        return DataCapturePageEntrySnapshot(
            id=page_entry.pk,
            page_state_id=page_entry.page_state_id,
            parent_entry_id=page_entry.parent_entry_id,
            entry_no=page_entry.entry_no,
            entry_kind=page_entry.entry_kind,
            entry_version=page_entry.entry_version,
            status=page_entry.status,
            data=page_entry.data,
            crf_template_id=page_entry.crf_template_id,
            subject_id=page_entry.subject_id,
            visit_id=page_entry.visit_id,
            updated_by_id=page_entry.updated_by_id,
            updated_at=page_entry.updated_at,
        )

    @staticmethod
    def _entry_version_for_no(entry_no: int) -> str:
        return f"v{entry_no}"

    def create_initial_entry(
        self,
        *,
        page_state_id: int,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        data: str,
        status: str,
        actor_user_id: int | None = None,
        event_form_binding_id: int | None = None,
    ):
        next_entry_no = self._next_entry_no(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            event_form_binding_id=event_form_binding_id,
        )
        entry_version = self._entry_version_for_no(next_entry_no)
        data = self.normalize_form_data_json_for_storage(
            crf_template_id=crf_template_id,
            data=data,
            entry_version=entry_version,
            strict=True,
        )
        return DataCapturePageEntry.objects.create(
            created_at=self._now(),
            updated_at=self._now(),
            deleted=False,
            entry_no=next_entry_no,
            entry_kind="initial",
            entry_version=entry_version,
            data=data,
            status=status,
            page_state_id=page_state_id,
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            created_by_id=actor_user_id,
            updated_by_id=actor_user_id,
        )

    def create_correction_draft_from_submitted_entry(
        self,
        *,
        page_state_id: int,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        data: str,
        status: str,
        actor_user_id: int | None = None,
        event_form_binding_id: int | None = None,
    ):
        """Save-draft after a submitted row: new correction draft; prior submitted row unchanged (6.2)."""
        latest = self.get_latest_entry(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            event_form_binding_id=event_form_binding_id,
        )
        assert latest is not None and latest.status == DataCapturePageEntryStatusChoices.SUBMITTED
        next_entry_no = self._next_entry_no(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            event_form_binding_id=event_form_binding_id,
        )
        entry_version = self._entry_version_for_no(next_entry_no)
        data = self.normalize_form_data_json_for_storage(
            crf_template_id=crf_template_id,
            data=data,
            entry_version=entry_version,
            strict=True,
        )
        return DataCapturePageEntry.objects.create(
            created_at=self._now(),
            updated_at=self._now(),
            deleted=False,
            entry_no=next_entry_no,
            entry_kind="correction",
            entry_version=entry_version,
            data=data,
            status=status,
            page_state_id=page_state_id,
            parent_entry_id=latest.id,
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            created_by_id=actor_user_id,
            updated_by_id=actor_user_id,
        )

    def supersede_submitted_entries_except(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        keep_entry_id: int,
        target_status: str,
        actor_user_id: int | None = None,
        event_form_binding_id: int | None = None,
    ) -> int:
        """Mark all submitted rows except ``keep_entry_id`` as superseded (submit flow 6.3)."""
        return DataCapturePageEntry.objects.filter(
            **self._page_entry_scope_filter_kwargs(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                event_form_binding_id=event_form_binding_id,
            ),
            deleted=False,
            status=DataCapturePageEntryStatusChoices.SUBMITTED,
        ).exclude(pk=keep_entry_id).update(
            status=target_status,
            updated_at=self._now(),
            updated_by_id=actor_user_id,
        )

    def has_other_submitted_entry(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        exclude_entry_id: int,
        event_form_binding_id: int | None = None,
    ) -> bool:
        return DataCapturePageEntry.objects.filter(
            **self._page_entry_scope_filter_kwargs(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                event_form_binding_id=event_form_binding_id,
            ),
            deleted=False,
            status=DataCapturePageEntryStatusChoices.SUBMITTED,
        ).exclude(pk=exclude_entry_id).exists()

    def are_all_visit_forms_submitted(self, *, subject_id: int, visit_id: int) -> bool:
        return self._are_all_visit_forms_in_status(
            subject_id=subject_id,
            visit_id=visit_id,
            status=DataCapturePageStateStatusChoices.SUBMITTED,
        )

    def are_all_visit_forms_verified(self, *, subject_id: int, visit_id: int) -> bool:
        return self._are_all_visit_forms_in_status(
            subject_id=subject_id,
            visit_id=visit_id,
            status=(
                DataCapturePageStateStatusChoices.VERIFIED,
                DataCapturePageStateStatusChoices.CERTIFIED,
                DataCapturePageStateStatusChoices.FINALIZED,
                DataCapturePageStateStatusChoices.LOCKED,
            ),
        )

    def _are_all_visit_forms_in_status(self, *, subject_id: int, visit_id: int, status) -> bool:
        visit = (
            SubjectEventInstance.objects.filter(
                pk=visit_id,
                subject_id=subject_id,
                deleted=False,
            )
            .only("id", "event_definition_id")
            .first()
        )
        if visit is None:
            return False

        form_definition_ids = list(
            EventFormBinding.objects.filter(
                event_definition_id=visit.event_definition_id,
                deleted=False,
                is_enabled=True,
            )
            .values_list("form_definition_id", flat=True)
            .distinct()
        )
        if not form_definition_ids:
            return False

        status_filter = (
            {"status__in": status}
            if isinstance(status, (tuple, list, set, frozenset))
            else {"status": status}
        )
        submitted_form_count = (
            DataCapturePageState.objects.filter(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id__in=form_definition_ids,
                deleted=False,
                **status_filter,
            )
            .values("crf_template_id")
            .distinct()
            .count()
        )
        return submitted_form_count == len(form_definition_ids)

    def list_submitted_entry_ids_except(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        exclude_entry_id: int | None,
        event_form_binding_id: int | None = None,
    ) -> list[int]:
        return list(
            DataCapturePageEntry.objects.filter(
                **self._page_entry_scope_filter_kwargs(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    event_form_binding_id=event_form_binding_id,
                ),
                deleted=False,
                status=DataCapturePageEntryStatusChoices.SUBMITTED,
            )
            .exclude(pk=exclude_entry_id)
            .values_list("id", flat=True)
        )

    def get_page_state_by_scope(self, *, subject_id: int, visit_id: int, crf_template_id: int, event_form_binding_id: int | None = None):
        return self.get_page_state(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            event_form_binding_id=event_form_binding_id,
        )

    def get_latest_stable_page_state_id_for_event_instance(self, *, event_instance_id: int) -> int | None:
        return (
            DataCapturePageState.objects.filter(
                visit_id=event_instance_id,
                deleted=False,
                status__in=self.FINAL_DATA_STATUSES,
            )
            .order_by("-updated_at", "-id")
            .values_list("id", flat=True)
            .first()
        )

    def get_latest_submitted_or_stable_page_state_id_for_event_instance(self, *, event_instance_id: int) -> int | None:
        return (
            DataCapturePageState.objects.filter(
                visit_id=event_instance_id,
                deleted=False,
                status__in=(
                    DataCapturePageStateStatusChoices.SUBMITTED,
                    *self.FINAL_DATA_STATUSES,
                ),
                current_entry__status=DataCapturePageEntryStatusChoices.SUBMITTED,
            )
            .order_by("-updated_at", "-id")
            .values_list("id", flat=True)
            .first()
        )

    def event_instance_has_data(self, *, event_instance_id: int) -> bool:
        return DataCapturePageEntry.objects.filter(
            visit_id=event_instance_id,
            deleted=False,
        ).exists()

    def get_current_entry(self, *, subject_id: int, visit_id: int, crf_template_id: int, event_form_binding_id: int | None = None):
        return self.get_latest_entry(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            event_form_binding_id=event_form_binding_id,
        )

    def update_latest_draft_entry_data(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        data: str,
        actor_user_id: int | None = None,
        event_form_binding_id: int | None = None,
    ):
        latest = self.get_latest_entry(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            event_form_binding_id=event_form_binding_id,
        )
        if latest is None or latest.status != DataCapturePageEntryStatusChoices.DRAFT:
            return latest
        data = self.normalize_form_data_json_for_storage(
            crf_template_id=crf_template_id,
            data=data,
            entry_version=latest.entry_version,
            strict=True,
        )
        DataCapturePageEntry.objects.filter(pk=latest.id).update(
            data=data,
            updated_at=self._now(),
            updated_by_id=actor_user_id,
        )
        return self.get_latest_entry(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            event_form_binding_id=event_form_binding_id,
        )

    def cancel_latest_draft_entry(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        target_status: str,
        actor_user_id: int | None = None,
        event_form_binding_id: int | None = None,
    ):
        latest = self.get_latest_entry(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            event_form_binding_id=event_form_binding_id,
        )
        if latest is None or latest.status != DataCapturePageEntryStatusChoices.DRAFT:
            return None
        DataCapturePageEntry.objects.filter(pk=latest.id).update(
            status=target_status,
            updated_at=self._now(),
            updated_by_id=actor_user_id,
        )
        return latest

    def upsert_page_state(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        status: str,
        actor_user_id: int | None = None,
        trigger_source: str = "manual",
        event_form_binding_id: int | None = None,
    ):
        """Create or update page state lifecycle fields only."""
        now = self._now()
        resolved_binding_id = event_form_binding_id or self._resolve_event_form_binding_id_for_scope(
            visit_id=visit_id,
            crf_template_id=crf_template_id,
        )
        repeat_index = self._resolve_default_page_state_repeat_index_for_scope(visit_id=visit_id)
        existing = (
            DataCapturePageState.objects.filter(
                **self._page_state_scope_filter_kwargs(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    event_form_binding_id=resolved_binding_id,
                ),
            )
            .order_by("id")
            .first()
        )
        if existing is not None:
            from_status = existing.status
            DataCapturePageState.objects.filter(pk=existing.pk).update(
                updated_at=now,
                deleted=False,
                status=status,
                event_form_binding_id=resolved_binding_id or existing.event_form_binding_id,
                repeat_index=repeat_index,
                final_data=self._page_state_final_data_for_lifecycle_status(
                    status=status,
                    current_final_data=existing.final_data,
                    page_state_id=existing.pk,
                    data_version=existing.data_version,
                ),
                updated_by_id=actor_user_id,
            )
            existing.refresh_from_db()
            self._record_page_state_transition(
                page_state=existing,
                from_status=from_status,
                to_status=status,
                actor_user_id=actor_user_id,
                trigger_source=trigger_source,
            )
            return existing
        page_state = DataCapturePageState.objects.create(
            created_at=now,
            updated_at=now,
            deleted=False,
            status=status,
            final_data=self.EMPTY_PAGE_STATE_FINAL_DATA,
            data_version=1,
            crf_template_id=crf_template_id,
            event_form_binding_id=resolved_binding_id,
            repeat_index=repeat_index,
            instance_key=uuid4().hex,
            subject_id=subject_id,
            visit_id=visit_id,
            created_by_id=actor_user_id,
            updated_by_id=actor_user_id,
        )
        if status in self.FINAL_DATA_STATUSES:
            page_state.final_data = self._page_state_final_data_for_status(
                status=status,
                page_state_id=page_state.pk,
                data_version=page_state.data_version,
            )
            DataCapturePageState.objects.filter(pk=page_state.pk).update(final_data=page_state.final_data)
        self._record_page_state_transition(
            page_state=page_state,
            from_status=None,
            to_status=status,
            actor_user_id=actor_user_id,
            trigger_source=trigger_source,
        )
        return page_state

    def upsert_page_state_for_data_entry(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        actor_user_id: int | None = None,
        event_form_binding_id: int | None = None,
    ):
        resolved_binding_id = event_form_binding_id or self._resolve_event_form_binding_id_for_scope(
            visit_id=visit_id,
            crf_template_id=crf_template_id,
        )
        page_state = DataCapturePageState.objects.filter(
            **self._page_state_scope_filter_kwargs(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                event_form_binding_id=resolved_binding_id,
            ),
            deleted=False,
        ).first()
        if page_state is None:
            page_state = self.upsert_page_state(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                status=DataCapturePageStateStatusChoices.NOT_STARTED,
                actor_user_id=actor_user_id,
                trigger_source="system",
                event_form_binding_id=resolved_binding_id,
            )
        else:
            page_state = self.ensure_page_state_binding_context(
                page_state,
                actor_user_id=actor_user_id,
            )
        if page_state.status == DataCapturePageStateStatusChoices.NOT_STARTED:
            return self.upsert_page_state(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                status=DataCapturePageStateStatusChoices.IN_PROGRESS,
                actor_user_id=actor_user_id,
                trigger_source="manual",
                event_form_binding_id=resolved_binding_id,
            )
        return page_state

    def ensure_open_page_state_if_not_exists(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        actor_user_id: int | None = None,
        event_form_binding_id: int | None = None,
    ) -> bool:
        """Create a not-started ``PageState`` when none exists for the scope."""
        resolved_binding_id = event_form_binding_id or self._resolve_event_form_binding_id_for_scope(
            visit_id=visit_id,
            crf_template_id=crf_template_id,
        )
        exists = DataCapturePageState.objects.filter(
            **self._page_state_scope_filter_kwargs(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                event_form_binding_id=resolved_binding_id,
            ),
            deleted=False,
        ).exists()
        if exists:
            return False
        self.upsert_page_state(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            status=DataCapturePageStateStatusChoices.NOT_STARTED,
            actor_user_id=actor_user_id,
            trigger_source="system",
            event_form_binding_id=resolved_binding_id,
        )
        return True

    def ensure_draft_page_state_if_not_exists(self, **kwargs) -> bool:
        return self.ensure_open_page_state_if_not_exists(**kwargs)

    def execute_submit_plan(
        self,
        *,
        page_state_id: int,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        plan: SubmitExecutionPlan,
        data: str,
        actor_user_id: int | None = None,
        event_form_binding_id: int | None = None,
    ):
        """Persist submit outcome described by a domain ``SubmitExecutionPlan`` (no branching here)."""
        if plan.action == "initial_submitted":
            next_entry_no = self._next_entry_no(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                event_form_binding_id=event_form_binding_id,
            )
            assert plan.entry_state_change is not None
            entry_version = self._entry_version_for_no(next_entry_no)
            data = self.normalize_form_data_json_for_storage(
                crf_template_id=crf_template_id,
                data=data,
                entry_version=entry_version,
                strict=True,
            )
            return DataCapturePageEntry.objects.create(
                created_at=self._now(),
                updated_at=self._now(),
                deleted=False,
                entry_no=next_entry_no,
                entry_kind="initial",
                entry_version=entry_version,
                data=data,
                status=plan.entry_state_change.to_status,
                submitted_at=self._now(),
                submitted_by_id=actor_user_id,
                page_state_id=page_state_id,
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                created_by_id=actor_user_id,
                updated_by_id=actor_user_id,
            )

        if plan.action == "promote_draft":
            assert plan.draft_entry_id is not None
            if plan.supersede_other_submitted_before_promote:
                assert plan.superseded_entry_state_change is not None
                self.supersede_submitted_entries_except(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    keep_entry_id=plan.draft_entry_id,
                    target_status=plan.superseded_entry_state_change.to_status,
                    actor_user_id=actor_user_id,
                    event_form_binding_id=event_form_binding_id,
                )
            assert plan.entry_state_change is not None
            draft_entry = DataCapturePageEntry.objects.only("entry_version").get(pk=plan.draft_entry_id)
            data = self.normalize_form_data_json_for_storage(
                crf_template_id=crf_template_id,
                data=data,
                entry_version=draft_entry.entry_version,
                strict=True,
            )
            DataCapturePageEntry.objects.filter(pk=plan.draft_entry_id).update(
                updated_at=self._now(),
                updated_by_id=actor_user_id,
                data=data,
                status=plan.entry_state_change.to_status,
                submitted_at=self._now(),
                submitted_by_id=actor_user_id,
            )
            return DataCapturePageEntry.objects.get(pk=plan.draft_entry_id)

        if plan.action == "replace_submitted":
            snap = plan.superseded_entry_snapshot
            assert snap is not None
            assert plan.superseded_entry_state_change is not None
            DataCapturePageEntry.objects.filter(pk=snap.id).update(
                updated_at=self._now(),
                updated_by_id=actor_user_id,
                status=plan.superseded_entry_state_change.to_status,
            )
            next_entry_no = self._next_entry_no(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                event_form_binding_id=event_form_binding_id,
            )
            entry_version = self._entry_version_for_no(next_entry_no)
            assert plan.entry_state_change is not None
            data = self.normalize_form_data_json_for_storage(
                crf_template_id=crf_template_id,
                data=data,
                entry_version=entry_version,
                strict=True,
            )
            return DataCapturePageEntry.objects.create(
                created_at=self._now(),
                updated_at=self._now(),
                deleted=False,
                entry_no=next_entry_no,
                entry_kind="correction",
                entry_version=entry_version,
                data=data,
                status=plan.entry_state_change.to_status,
                submitted_at=self._now(),
                submitted_by_id=actor_user_id,
                page_state_id=page_state_id,
                parent_entry_id=snap.id,
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                created_by_id=actor_user_id,
                updated_by_id=actor_user_id,
            )

        raise RuntimeError(f"execute_submit_plan: unknown action {plan.action!r}")

    def submit_page_state_with_entry(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        entry_id: int,
        actor_user_id: int | None = None,
        trigger_source: str = "manual",
        target_status: str = DataCapturePageStateStatusChoices.SUBMITTED,
        event_form_binding_id: int | None = None,
    ):
        resolved_binding_id = event_form_binding_id or self._resolve_event_form_binding_id_for_scope(
            visit_id=visit_id,
            crf_template_id=crf_template_id,
        )
        page_state = (
            DataCapturePageState.objects.filter(
                **self._page_state_scope_filter_kwargs(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    event_form_binding_id=resolved_binding_id,
                ),
                deleted=False,
            )
            .order_by("id")
            .first()
        )
        if page_state is None:
            page_state = self.upsert_page_state(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                status=DataCapturePageStateStatusChoices.NOT_STARTED,
                actor_user_id=actor_user_id,
                trigger_source="system",
                event_form_binding_id=resolved_binding_id,
            )
        now = self._now()
        from_status = page_state.status
        next_version = int(page_state.data_version or 1) + 1
        DataCapturePageState.objects.filter(pk=page_state.pk).update(
            updated_at=now,
            updated_by_id=actor_user_id,
            data_version=next_version,
            current_entry_id=entry_id,
            status=target_status,
            final_data=self._page_state_final_data_for_lifecycle_status(
                status=target_status,
                current_final_data=page_state.final_data,
                page_state_id=page_state.pk,
                data_version=page_state.data_version,
            ),
            submitted_at=now,
            submitted_by_id=actor_user_id,
        )
        page_state.refresh_from_db()
        self._record_page_state_transition(
            page_state=page_state,
            from_status=from_status,
            to_status=target_status,
            actor_user_id=actor_user_id,
            trigger_source=trigger_source,
        )
        return page_state

    def mark_field_reviews_stale_for_changed_field_keys(
        self,
        *,
        page_state_id: int,
        crf_template_id: int,
        changed_field_keys: list[str],
        actor_user_id: int | None = None,
    ) -> int:
        if not changed_field_keys:
            return 0
        matched_keys_by_template_id = self._map_changed_field_keys_by_template_id(
            crf_template_id=crf_template_id,
            changed_field_keys=changed_field_keys,
        )
        if not matched_keys_by_template_id:
            return 0
        now = self._now()
        affected = 0
        for field_template_id in matched_keys_by_template_id:
            latest_verified = (
                DataCaptureFieldReview.objects.filter(
                    page_state_id=page_state_id,
                    field_template_id=field_template_id,
                    deleted=False,
                    status=DataCaptureFieldReviewStatusChoices.VERIFIED,
                )
                .order_by("-updated_at", "-id")
                .first()
            )
            if latest_verified is None:
                continue
            affected += DataCaptureFieldReview.objects.filter(pk=latest_verified.pk).update(
                status=DataCaptureFieldReviewStatusChoices.STALE,
                updated_at=now,
                updated_by_id=actor_user_id,
            )
        return affected

    def list_changed_verified_field_keys(
        self,
        *,
        page_state_id: int,
        crf_template_id: int,
        data_version: int,
        changed_field_keys: list[str],
        review_type: str = DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
    ) -> list[str]:
        if not changed_field_keys:
            return []
        matched_keys_by_template_id = self._map_changed_field_keys_by_template_id(
            crf_template_id=crf_template_id,
            changed_field_keys=changed_field_keys,
        )
        if not matched_keys_by_template_id:
            return []
        verified_changed_keys: list[str] = []
        for field_template_id, matched_changed_keys in matched_keys_by_template_id.items():
            latest_review = (
                DataCaptureFieldReview.objects.filter(
                    page_state_id=page_state_id,
                    field_template_id=field_template_id,
                    review_type=review_type,
                    deleted=False,
                )
                .order_by("-updated_at", "-id")
                .first()
            )
            if latest_review is None:
                continue
            if latest_review.status == DataCaptureFieldReviewStatusChoices.VERIFIED:
                verified_changed_keys.extend(matched_changed_keys)
        return sorted(set(verified_changed_keys))

    def mark_verified_field_reviews_stale_with_reasons(
        self,
        *,
        page_state_id: int,
        crf_template_id: int,
        data_version: int,
        reason_by_field_key: dict[str, str],
        actor_user_id: int | None = None,
        review_type: str = DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
    ) -> int:
        normalized_reason_map = {
            str(field_key or "").strip(): str(reason or "").strip()
            for field_key, reason in (reason_by_field_key or {}).items()
            if str(field_key or "").strip() and str(reason or "").strip()
        }
        if not normalized_reason_map:
            return 0
        matched_keys_by_template_id = self._map_changed_field_keys_by_template_id(
            crf_template_id=crf_template_id,
            changed_field_keys=list(normalized_reason_map.keys()),
        )
        affected = 0
        now = self._now()
        for field_template_id, matched_reason_keys in matched_keys_by_template_id.items():
            reason_text = ""
            for field_key in matched_reason_keys:
                reason_text = normalized_reason_map.get(field_key, "")
                if reason_text:
                    break
            if not reason_text:
                continue
            latest_verified = (
                DataCaptureFieldReview.objects.filter(
                    page_state_id=page_state_id,
                    field_template_id=field_template_id,
                    review_type=review_type,
                    deleted=False,
                    status=DataCaptureFieldReviewStatusChoices.VERIFIED,
                )
                .order_by("-updated_at", "-id")
                .first()
            )
            if latest_verified is None:
                continue
            affected += DataCaptureFieldReview.objects.filter(pk=latest_verified.pk).update(
                status=DataCaptureFieldReviewStatusChoices.STALE,
                reason_text=reason_text,
                updated_at=now,
                updated_by_id=actor_user_id,
            )
        return affected

    def start_page_review(
        self,
        *,
        page_state_id: int,
        actor_user_id: int | None = None,
    ):
        page_state = DataCapturePageState.objects.filter(pk=page_state_id, deleted=False).first()
        if page_state is None:
            raise LookupError("No page state exists for this subject visit and form.")
        if page_state.status == DataCapturePageStateStatusChoices.UNDER_REVIEW:
            return page_state
        now = self._now()
        from_status = page_state.status
        DataCapturePageState.objects.filter(pk=page_state.pk).update(
            updated_at=now,
            updated_by_id=actor_user_id,
            status=DataCapturePageStateStatusChoices.UNDER_REVIEW,
            final_data=self.EMPTY_PAGE_STATE_FINAL_DATA,
            review_started_at=now,
            review_started_by_id=actor_user_id,
        )
        page_state.refresh_from_db()
        self._record_page_state_transition(
            page_state=page_state,
            from_status=from_status,
            to_status=DataCapturePageStateStatusChoices.UNDER_REVIEW,
            actor_user_id=actor_user_id,
            trigger_source="review",
        )
        return page_state

    def ensure_field_reviews_for_page(
        self,
        *,
        page_state_id: int,
        field_template_ids: tuple[int, ...],
        data_version: int,
        actor_user_id: int | None = None,
        review_type: str = DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
    ) -> int:
        if not field_template_ids:
            return 0
        now = self._now()
        stale_rows = list(
            DataCaptureFieldReview.objects.filter(
                page_state_id=page_state_id,
                field_template_id__in=field_template_ids,
                review_type=review_type,
                deleted=False,
                status=DataCaptureFieldReviewStatusChoices.STALE,
            ).only("id", "field_template_id")
        )
        stale_field_ids = {int(row.field_template_id) for row in stale_rows}
        if stale_rows:
            DataCaptureFieldReview.objects.filter(
                id__in=[int(row.id) for row in stale_rows],
            ).update(
                deleted=True,
                updated_at=now,
                updated_by_id=actor_user_id,
            )
        existing_ids = set(
            DataCaptureFieldReview.objects.filter(
                page_state_id=page_state_id,
                field_template_id__in=field_template_ids,
                review_type=review_type,
                deleted=False,
            ).values_list("field_template_id", flat=True)
        )
        records = [
            DataCaptureFieldReview(
                created_at=now,
                updated_at=now,
                deleted=False,
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                review_type=review_type,
                status=DataCaptureFieldReviewStatusChoices.NOT_REVIEWED,
                data_version=data_version,
                created_by_id=actor_user_id,
                updated_by_id=actor_user_id,
            )
            for field_template_id in field_template_ids
            if field_template_id not in existing_ids or field_template_id in stale_field_ids
        ]
        if not records:
            return 0
        DataCaptureFieldReview.objects.bulk_create(records, ignore_conflicts=True)
        return len(records)

    def verify_field_review(
        self,
        *,
        page_state_id: int,
        field_template_id: int,
        data_version: int,
        value_snapshot: str,
        actor_user_id: int | None = None,
        review_type: str = DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
    ) -> None:
        now = self._now()
        review = DataCaptureFieldReview.objects.filter(
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            review_type=review_type,
            deleted=False,
        ).first()
        if review is not None and review.status == DataCaptureFieldReviewStatusChoices.STALE:
            DataCaptureFieldReview.objects.filter(pk=review.pk).update(
                deleted=True,
                updated_at=now,
                updated_by_id=actor_user_id,
            )
            review = None
        if review is None:
            review = DataCaptureFieldReview.objects.create(
                created_at=now,
                updated_at=now,
                deleted=False,
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                review_type=review_type,
                status=DataCaptureFieldReviewStatusChoices.NOT_REVIEWED,
                data_version=data_version,
                created_by_id=actor_user_id,
                updated_by_id=actor_user_id,
            )
        if review.status == DataCaptureFieldReviewStatusChoices.VERIFIED:
            return
        update_fields = {
            "updated_at": now,
            "updated_by_id": actor_user_id,
            "deleted": False,
            "status": DataCaptureFieldReviewStatusChoices.VERIFIED,
            "data_version": data_version,
            "value_snapshot": value_snapshot,
            "verified_at": now,
            "verified_by_id": actor_user_id,
        }
        if review.reviewed_at is None:
            update_fields["reviewed_at"] = now
            update_fields["reviewed_by_id"] = actor_user_id
        DataCaptureFieldReview.objects.filter(pk=review.pk).update(**update_fields)

    def unverify_field_review(
        self,
        *,
        page_state_id: int,
        field_template_id: int,
        status: str,
        data_version: int,
        reason_text: str | None = None,
        actor_user_id: int | None = None,
        review_type: str = DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
    ) -> bool:
        now = self._now()
        review = (
            DataCaptureFieldReview.objects.filter(
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                review_type=review_type,
                deleted=False,
                status=DataCaptureFieldReviewStatusChoices.VERIFIED,
            )
            .order_by("-updated_at", "-id")
            .first()
        )
        if review is None:
            return False
        DataCaptureFieldReview.objects.filter(pk=review.pk).update(
            updated_at=now,
            updated_by_id=actor_user_id,
            status=status,
            data_version=data_version,
            reason_text=reason_text,
            verified_at=None,
            verified_by_id=None,
        )
        return True

    def map_valid_field_review_status_by_field_template_id(
        self,
        *,
        page_state_id: int,
        data_version: int,
        review_type: str = DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
    ) -> dict[int, str]:
        rows = DataCaptureFieldReview.objects.filter(
            page_state_id=page_state_id,
            review_type=review_type,
            deleted=False,
        ).values("field_template_id", "status", "data_version", "updated_at", "id").order_by(
            "field_template_id", "-data_version", "-updated_at", "-id"
        )
        status_by_field: dict[int, str] = {}
        for row in rows:
            field_template_id = int(row["field_template_id"])
            if field_template_id in status_by_field:
                continue
            status_by_field[field_template_id] = str(row["status"] or "")
        return status_by_field

    def list_verified_or_waived_field_template_ids(
        self,
        *,
        page_state_id: int,
        data_version: int,
        review_type: str = DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
    ) -> set[int]:
        return set(
            DataCaptureFieldReview.objects.filter(
                page_state_id=page_state_id,
                review_type=review_type,
                data_version=data_version,
                deleted=False,
                status__in=[
                    DataCaptureFieldReviewStatusChoices.VERIFIED,
                    DataCaptureFieldReviewStatusChoices.WAIVED,
                ],
            ).values_list("field_template_id", flat=True)
        )

    def list_verified_field_template_ids(
        self,
        *,
        page_state_id: int,
        data_version: int | None = None,
        review_type: str = DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
    ) -> set[int]:
        queryset = DataCaptureFieldReview.objects.filter(
            page_state_id=page_state_id,
            review_type=review_type,
            deleted=False,
            status=DataCaptureFieldReviewStatusChoices.VERIFIED,
        )
        return set(queryset.values_list("field_template_id", flat=True))

    def is_field_review_verified(
        self,
        *,
        page_state_id: int,
        field_template_id: int,
        review_type: str = DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
    ) -> bool:
        return DataCaptureFieldReview.objects.filter(
            page_state_id=page_state_id,
            field_template_id=field_template_id,
            review_type=review_type,
            deleted=False,
            status=DataCaptureFieldReviewStatusChoices.VERIFIED,
        ).exists()

    def find_page_verification_field_review_blockers(
        self,
        *,
        page_state_id: int,
        data_version: int,
        required_field_template_ids: tuple[int, ...],
        review_type: str = DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
    ) -> list[str]:
        if not required_field_template_ids:
            return []
        status_by_field = self.map_valid_field_review_status_by_field_template_id(
            page_state_id=page_state_id,
            data_version=data_version,
            review_type=review_type,
        )
        valid_statuses = {
            DataCaptureFieldReviewStatusChoices.VERIFIED,
            DataCaptureFieldReviewStatusChoices.WAIVED,
        }
        blockers: list[str] = []
        for field_template_id in required_field_template_ids:
            status = status_by_field.get(int(field_template_id))
            if status not in valid_statuses:
                blockers.append(f"field_review_not_ready:{field_template_id}")
        return blockers

    def verify_page_state_if_ready(
        self,
        *,
        page_state_id: int,
        actor_user_id: int | None = None,
    ) -> str:
        page_state = DataCapturePageState.objects.filter(pk=page_state_id, deleted=False).first()
        if page_state is None:
            raise LookupError("No page state exists for this subject visit and form.")
        now = self._now()
        from_status = page_state.status
        DataCapturePageState.objects.filter(pk=page_state.pk).update(
            updated_at=now,
            updated_by_id=actor_user_id,
            status=DataCapturePageStateStatusChoices.VERIFIED,
            final_data=self._page_state_final_data_for_status(
                status=DataCapturePageStateStatusChoices.VERIFIED,
                page_state_id=page_state.pk,
                data_version=page_state.data_version,
            ),
            verified_at=now,
            verified_by_id=actor_user_id,
            verified_data_version=page_state.data_version,
        )
        page_state.refresh_from_db()
        self._record_page_state_transition(
            page_state=page_state,
            from_status=from_status,
            to_status=DataCapturePageStateStatusChoices.VERIFIED,
            actor_user_id=actor_user_id,
            trigger_source="PageVerified",
        )
        return DataCapturePageStateStatusChoices.VERIFIED.value

    def reopen_verified_page_state(
        self,
        *,
        page_state_id: int,
        reason_text: str | None,
        actor_user_id: int | None = None,
    ) -> str:
        page_state = DataCapturePageState.objects.filter(pk=page_state_id, deleted=False).first()
        if page_state is None:
            raise LookupError("No page state exists for this subject visit and form.")
        now = self._now()
        from_status = page_state.status
        DataCapturePageState.objects.filter(pk=page_state.pk).update(
            updated_at=now,
            updated_by_id=actor_user_id,
            status=DataCapturePageStateStatusChoices.CORRECTION_REQUIRED,
            final_data=self.EMPTY_PAGE_STATE_FINAL_DATA,
        )
        DataCaptureFieldReview.objects.filter(
            page_state_id=page_state.pk,
            review_type=DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
            deleted=False,
            status=DataCaptureFieldReviewStatusChoices.VERIFIED,
        ).update(
            status=DataCaptureFieldReviewStatusChoices.STALE,
            reason_code="reopen_form",
            reason_text=reason_text,
            updated_at=now,
            updated_by_id=actor_user_id,
        )
        page_state.refresh_from_db()
        self._record_page_state_transition(
            page_state=page_state,
            from_status=from_status,
            to_status=DataCapturePageStateStatusChoices.CORRECTION_REQUIRED,
            actor_user_id=actor_user_id,
            trigger_source="review",
            reason_code="reopen_form",
            reason_text=reason_text,
        )
        return DataCapturePageStateStatusChoices.CORRECTION_REQUIRED.value

    def map_latest_submitted_entry_updated_by_id_by_subject_ids(
        self, *, subject_ids: tuple[int, ...]
    ) -> dict[int, int | None]:
        if not subject_ids:
            return {}
        result: dict[int, int | None] = {}
        entries = (
            DataCapturePageEntry.objects.filter(
                subject_id__in=subject_ids,
                deleted=False,
                status=DataCapturePageEntryStatusChoices.SUBMITTED,
            )
            .only("subject_id", "updated_by_id", "updated_at", "id")
            .order_by("subject_id", "-updated_at", "-id")
        )
        for entry in entries:
            if entry.subject_id not in result:
                result[entry.subject_id] = entry.updated_by_id
        return result

    def update_page_state_final_data_and_status(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        final_data: str | None,
        status: str,
        actor_user_id: int | None = None,
        trigger_source: str = "review",
        event_form_binding_id: int | None = None,
    ) -> None:
        """Persist stable ``final_data`` when status is verified, locked, or finalized."""
        now = self._now()
        page_state = DataCapturePageState.objects.filter(
            **self._page_state_scope_filter_kwargs(
                subject_id=subject_id,
                visit_id=visit_id,
                crf_template_id=crf_template_id,
                event_form_binding_id=event_form_binding_id,
            ),
            deleted=False,
        ).first()
        if page_state is None:
            raise LookupError("Could not persist verification: page state update affected 0 rows.")
        from_status = page_state.status
        rows_updated = DataCapturePageState.objects.filter(pk=page_state.pk).update(
            final_data=self._page_state_final_data_for_status(
                status=status,
                page_state_id=page_state.pk,
                data_version=page_state.data_version,
            ),
            status=status,
            updated_at=now,
            updated_by_id=actor_user_id,
            finalized_at=(
                now if status == DataCapturePageStateStatusChoices.FINALIZED else page_state.finalized_at
            ),
            finalized_by_id=(
                actor_user_id if status == DataCapturePageStateStatusChoices.FINALIZED else page_state.finalized_by_id
            ),
            finalized_data_version=(
                page_state.data_version
                if status == DataCapturePageStateStatusChoices.FINALIZED
                else page_state.finalized_data_version
            ),
            certified_at=(
                now if status == DataCapturePageStateStatusChoices.CERTIFIED else page_state.certified_at
            ),
            certified_by_id=(
                actor_user_id
                if status == DataCapturePageStateStatusChoices.CERTIFIED
                else page_state.certified_by_id
            ),
            certified_data_version=(
                page_state.data_version
                if status == DataCapturePageStateStatusChoices.CERTIFIED
                else page_state.certified_data_version
            ),
            locked_at=(now if status == DataCapturePageStateStatusChoices.LOCKED else page_state.locked_at),
            locked_by_id=(
                actor_user_id if status == DataCapturePageStateStatusChoices.LOCKED else page_state.locked_by_id
            ),
            locked_data_version=(
                page_state.data_version
                if status == DataCapturePageStateStatusChoices.LOCKED
                else page_state.locked_data_version
            ),
            verified_at=(now if status == DataCapturePageStateStatusChoices.VERIFIED else page_state.verified_at),
            verified_by_id=(
                actor_user_id if status == DataCapturePageStateStatusChoices.VERIFIED else page_state.verified_by_id
            ),
            verified_data_version=(
                page_state.data_version
                if status == DataCapturePageStateStatusChoices.VERIFIED
                else page_state.verified_data_version
            ),
        )
        if rows_updated == 0:
            raise LookupError("Could not persist verification: page state update affected 0 rows.")
        page_state.refresh_from_db()
        self._record_page_state_transition(
            page_state=page_state,
            from_status=from_status,
            to_status=status,
            actor_user_id=actor_user_id,
            trigger_source=trigger_source,
        )

    def _page_state_final_data_for_lifecycle_status(
        self,
        *,
        status: str,
        current_final_data: str | None,
        page_state_id: int | None = None,
        data_version: int | None = None,
    ) -> str:
        if status in self.FINAL_DATA_STATUSES:
            if page_state_id is not None and data_version is not None:
                return self._page_state_final_data_for_status(
                    status=status,
                    page_state_id=page_state_id,
                    data_version=data_version,
                )
            return current_final_data or self.EMPTY_PAGE_STATE_FINAL_DATA
        return self.EMPTY_PAGE_STATE_FINAL_DATA

    def _page_state_final_data_for_status(
        self,
        *,
        status: str,
        page_state_id: int,
        data_version: int,
    ) -> str:
        if status not in self.FINAL_DATA_STATUSES:
            return self.EMPTY_PAGE_STATE_FINAL_DATA
        return self._build_page_state_final_data_from_field_reviews(
            status=status,
            page_state_id=page_state_id,
            data_version=data_version,
            review_type=DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
        )

    def _build_page_state_final_data_from_field_reviews(
        self,
        *,
        status: str,
        page_state_id: int,
        data_version: int,
        review_type: str,
    ) -> str:
        payload: dict = {}
        rows = (
            DataCaptureFieldReview.objects.filter(
                page_state_id=page_state_id,
                data_version=data_version,
                review_type=review_type,
                deleted=False,
                status__in=[
                    DataCaptureFieldReviewStatusChoices.VERIFIED,
                    DataCaptureFieldReviewStatusChoices.WAIVED,
                ],
            )
            .only("value_snapshot", "updated_at", "id")
            .order_by("updated_at", "id")
        )
        for row in rows:
            try:
                value_map = json.loads(row.value_snapshot or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(value_map, dict):
                continue
            payload.update(value_map)
        page_state = (
            DataCapturePageState.objects.filter(pk=page_state_id, deleted=False)
            .only("crf_template_id", "current_entry_id")
            .first()
        )
        crf_template_id = int(page_state.crf_template_id) if page_state is not None else 0
        entry_version = ""
        if page_state is not None and page_state.current_entry_id:
            current_entry = (
                DataCapturePageEntry.objects.filter(pk=page_state.current_entry_id)
                .only("entry_version", "data")
                .first()
            )
            entry_version = str(getattr(current_entry, "entry_version", "") or "")
            if not payload and current_entry is not None:
                payload = flatten_form_data_for_export(
                    normalize_form_data(current_entry.data, strict=False),
                    repeat_strategy="legacy_repeat_suffix",
                )
        template_snapshot = (
            self.get_form_template_snapshot(crf_template_id=crf_template_id)
            if crf_template_id
            else None
        )
        doc = normalize_form_data(
            payload,
            template_snapshot=template_snapshot,
            entry_version=entry_version,
            strict=False,
        )
        doc["page_state"] = {
            "id": int(page_state_id),
            "status": self._status_value(status),
            "data_version": int(data_version),
            "current_entry_id": int(page_state.current_entry_id) if page_state and page_state.current_entry_id else None,
            "crf_template_id": crf_template_id or None,
        }
        doc["fields"] = {
            str(field_key): {"value": value}
            for field_key, value in payload.items()
            if not str(field_key).startswith("_")
        }
        doc["verification"] = {
            "required_eligibility_fields_verified": True,
            "required_fields_verified": True,
        }
        doc["query_summary"] = {
            "blocking_eligibility_query_count": 0,
            "blocking_query_count": 0,
        }
        return json.dumps(doc, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _status_value(status: str) -> str:
        return str(getattr(status, "value", status))

    def _record_page_state_transition(
        self,
        *,
        page_state,
        from_status: str | None,
        to_status: str,
        actor_user_id: int | None,
        trigger_source: str,
        reason_code: str | None = None,
        reason_text: str | None = None,
    ) -> None:
        if from_status == to_status:
            return
        DataCapturePageStateTransitionLog.objects.create(
            created_at=self._now(),
            page_state_id=page_state.pk,
            from_status=from_status,
            to_status=to_status,
            data_version=getattr(page_state, "data_version", None),
            reason_code=reason_code,
            reason_text=reason_text,
            trigger_source=trigger_source,
            actor_id=actor_user_id,
        )

    def _next_entry_no(
        self,
        *,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        event_form_binding_id: int | None = None,
    ) -> int:
        max_entry_no = (
            DataCapturePageEntry.objects.filter(
                **self._page_entry_scope_filter_kwargs(
                    subject_id=subject_id,
                    visit_id=visit_id,
                    crf_template_id=crf_template_id,
                    event_form_binding_id=event_form_binding_id,
                ),
                deleted=False,
            ).aggregate(max_entry_no=Max("entry_no")).get("max_entry_no")
        )
        return 1 if max_entry_no is None else int(max_entry_no) + 1

    @staticmethod
    def _now():
        from django.utils import timezone

        return timezone.now()
