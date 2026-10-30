import json
import re
from datetime import date, datetime
from typing import Any

from django.utils import formats, timezone

from apps.core.form_data_document import REPEAT_COUNTS_EXPORT_META_KEY, build_field_path
from apps.reconcile.application.services.dataquery_read import ReconcileDataQueryReadService
from apps.subject.infrastructure.repositories.event_instance_schedule_read import (
    DjangoSubjectEventInstanceScheduleReadRepository,
)
from apps.subject.infrastructure.repositories.user_display_lookup import get_username_display_for_user_id

_DATE_PART_SUFFIXES = ("__day", "__month", "__year", "__time")
_REPEAT_SUFFIX_RE = re.compile(r"__repeat_(?P<repeat_index>\d+)(?:__(?:day|month|year|time))?$")


def _normalize_storage_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return str(value).strip()


class FormFieldReviewTableService:

    def __init__(self, reconcile_read_service=None):
        self._reconcile_read_service = reconcile_read_service or ReconcileDataQueryReadService()

    def build_for_verification(
        self,
        *,
        subject_code: str,
        site_id: int,
        event_name: str,
        event_instance_id: int,
        form_name: str,
        form_status: str,
        entry_version: str,
        entry_updated_at: datetime | None,
        entry_updated_by_id: int | None,
        field_templates_payload: list[dict[str, Any]],
        entry_payload: dict[str, Any],
        page_state_id: int | None,
        current_user_id: int | None = None,
        verified_snapshot: dict[str, Any] | None = None,
        verified_field_template_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        event_start_at = DjangoSubjectEventInstanceScheduleReadRepository().get_event_start_datetime(
            event_instance_id=event_instance_id,
        )
        field_template_ids: list[int] = []
        seen_field_template_ids: set[int] = set()
        for field_row in field_templates_payload or []:
            raw_id = field_row.get("id")
            try:
                field_template_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if field_template_id in seen_field_template_ids:
                continue
            seen_field_template_ids.add(field_template_id)
            field_template_ids.append(field_template_id)
        counts: dict[int, int] = {}
        active_query_ids: dict[int, int] = {}
        active_query_participants: dict[int, dict[str, int | None]] = {}
        active_query_answered_flags: dict[int, bool] = {}
        verified_query_field_template_ids: set[int] = set()
        query_thread_badge_counts: dict[int, int] = {}
        query_messages_by_field: dict[int, list[dict[str, Any]]] = {}
        closed_query_histories_by_field: dict[int, list[dict[str, Any]]] = {}
        validation_issues_by_field: dict[int, list[dict[str, Any]]] = {}
        validation_issue_histories_by_field: dict[int, list[dict[str, Any]]] = {}
        counts_by_field_path: dict[tuple[int, str], int] = {}
        active_query_contexts_by_field_path: dict[tuple[int, str], dict[str, Any]] = {}
        verified_query_keys_by_field_path: set[tuple[int, str]] = set()
        query_thread_badge_counts_by_query: dict[int, int] = {}
        query_messages_by_query: dict[int, list[dict[str, Any]]] = {}
        closed_query_histories_by_field_path: dict[tuple[int, str], list[dict[str, Any]]] = {}
        if page_state_id is not None and field_template_ids:
            counts = self._reconcile_read_service.count_open_queries_by_page_state_and_field_templates(
                page_state_id=page_state_id,
                field_template_ids=tuple(field_template_ids),
            )
            verified_query_field_template_ids = (
                self._reconcile_read_service.list_field_template_ids_with_verified_queries(
                    page_state_id=page_state_id,
                    field_template_ids=tuple(field_template_ids),
                )
            )
            active_query_ids = (
                self._reconcile_read_service.list_latest_active_query_ids_by_page_state_and_field_templates(
                    page_state_id=page_state_id,
                    field_template_ids=tuple(field_template_ids),
                )
            )
            active_query_participants = (
                self._reconcile_read_service.list_latest_active_query_participants_by_page_state_and_field_templates(
                    page_state_id=page_state_id,
                    field_template_ids=tuple(field_template_ids),
                )
            )
            active_query_answered_flags = (
                self._reconcile_read_service.list_latest_active_query_answered_flags_by_page_state_and_field_templates(
                    page_state_id=page_state_id,
                    field_template_ids=tuple(field_template_ids),
                )
            )
            active_query_ids_tuple = tuple(active_query_ids.values())
            query_messages_by_query_for_field = self._reconcile_read_service.list_latest_query_messages_by_dataquery_ids(
                dataquery_ids=active_query_ids_tuple,
                limit_per_query=10,
            )
            query_thread_badge_counts_by_active_query = (
                self._reconcile_read_service.count_query_threads_since_current_user_last_comment_by_dataquery_ids(
                    dataquery_ids=active_query_ids_tuple,
                    current_user_id=current_user_id,
                )
            )
            query_messages_by_field = {
                field_template_id: query_messages_by_query_for_field.get(dataquery_id, [])
                for field_template_id, dataquery_id in active_query_ids.items()
            }
            query_thread_badge_counts = {
                field_template_id: query_thread_badge_counts_by_active_query.get(dataquery_id, 0)
                for field_template_id, dataquery_id in active_query_ids.items()
            }
            closed_query_histories_by_field = (
                self._reconcile_read_service.list_closed_query_histories_by_page_state_and_field_templates(
                    page_state_id=page_state_id,
                    field_template_ids=tuple(field_template_ids),
                    limit_per_query=10,
                )
            )
            validation_issues_by_field = (
                self._reconcile_read_service.list_open_validation_issues_by_page_state_and_field_templates(
                    page_state_id=page_state_id,
                    field_template_ids=tuple(field_template_ids),
                )
            )
            validation_issue_histories_by_field = (
                self._reconcile_read_service.list_validation_issue_histories_by_page_state_and_field_templates(
                    page_state_id=page_state_id,
                    field_template_ids=tuple(field_template_ids),
                )
            )
            counts_by_field_path = self._reconcile_read_service.count_open_queries_by_page_state_field_paths(
                page_state_id=page_state_id,
                field_template_ids=tuple(field_template_ids),
            )
            active_query_contexts_by_field_path = (
                self._reconcile_read_service.list_latest_active_query_contexts_by_page_state_and_field_templates(
                    page_state_id=page_state_id,
                    field_template_ids=tuple(field_template_ids),
                )
            )
            verified_query_keys_by_field_path = (
                self._reconcile_read_service.list_verified_query_keys_by_page_state_and_field_templates(
                    page_state_id=page_state_id,
                    field_template_ids=tuple(field_template_ids),
                )
            )
            active_query_ids_by_path = tuple(
                int(context["active_query_id"])
                for context in active_query_contexts_by_field_path.values()
                if context.get("active_query_id")
            )
            query_messages_by_query = self._reconcile_read_service.list_latest_query_messages_by_dataquery_ids(
                dataquery_ids=active_query_ids_by_path,
                limit_per_query=10,
            )
            query_thread_badge_counts_by_query = (
                self._reconcile_read_service.count_query_threads_since_current_user_last_comment_by_dataquery_ids(
                    dataquery_ids=active_query_ids_by_path,
                    current_user_id=current_user_id,
                )
            )
            closed_query_histories_by_field_path = (
                self._reconcile_read_service.list_closed_query_histories_by_page_state_field_paths(
                    page_state_id=page_state_id,
                    field_template_ids=tuple(field_template_ids),
                    limit_per_query=10,
                )
            )

        modified_by_display = get_username_display_for_user_id(entry_updated_by_id)

        rows: list[dict[str, Any]] = []
        for section in self._group_fields_by_section(field_templates_payload):
            repeat_count = (
                self._resolve_repeat_count_for_section(
                    section["fields"],
                    entry_payload,
                    section.get("max_repeats"),
                    section_code=section.get("code"),
                )
                if section.get("is_repeatable")
                else 1
            )
            for repeat_index in range(1, repeat_count + 1):
                for field_row in section["fields"]:
                    row = self._build_review_row(
                        field_row=field_row,
                        entry_payload=entry_payload,
                        repeat_index=repeat_index,
                        section_title=section.get("title") or "",
                        section_code=section.get("code") or "",
                        is_repeatable_group_item=bool(section.get("is_repeatable")),
                        counts=counts,
                        active_query_ids=active_query_ids,
                        active_query_participants=active_query_participants,
                        active_query_answered_flags=active_query_answered_flags,
                        verified_query_field_template_ids=verified_query_field_template_ids,
                        query_thread_badge_counts=query_thread_badge_counts,
                        query_messages_by_field=query_messages_by_field,
                        closed_query_histories_by_field=closed_query_histories_by_field,
                        validation_issues_by_field=validation_issues_by_field,
                        validation_issue_histories_by_field=validation_issue_histories_by_field,
                        counts_by_field_path=counts_by_field_path,
                        active_query_contexts_by_field_path=active_query_contexts_by_field_path,
                        verified_query_keys_by_field_path=verified_query_keys_by_field_path,
                        query_thread_badge_counts_by_query=query_thread_badge_counts_by_query,
                        query_messages_by_query=query_messages_by_query,
                        closed_query_histories_by_field_path=closed_query_histories_by_field_path,
                        current_user_id=current_user_id,
                        verified_snapshot=verified_snapshot,
                        verified_field_template_ids=verified_field_template_ids,
                        modified_by_display=modified_by_display,
                    )
                    if row is not None:
                        rows.append(row)

        return {
            "header": {
                "subject_code": subject_code or "—",
                "event_name": event_name or "—",
                "site_id": str(site_id) if site_id is not None else "—",
                "event_start_date": self._format_datetime(event_start_at),
                "form_name": form_name or "—",
                "form_status": (form_status or "").strip() or "—",
                "form_version": (entry_version or "").strip() or "—",
                "last_modified": self._format_datetime(entry_updated_at),
            },
            "rows": rows,
        }

    def _build_review_row(
        self,
        *,
        field_row: dict[str, Any],
        entry_payload: dict[str, Any],
        repeat_index: int,
        section_title: str,
        section_code: str,
        is_repeatable_group_item: bool,
        counts: dict[int, int],
        active_query_ids: dict[int, int],
        active_query_participants: dict[int, dict[str, int | None]],
        active_query_answered_flags: dict[int, bool],
        verified_query_field_template_ids: set[int],
        query_thread_badge_counts: dict[int, int],
        query_messages_by_field: dict[int, list[dict[str, Any]]],
        closed_query_histories_by_field: dict[int, list[dict[str, Any]]],
        validation_issues_by_field: dict[int, list[dict[str, Any]]],
        validation_issue_histories_by_field: dict[int, list[dict[str, Any]]],
        counts_by_field_path: dict[tuple[int, str], int],
        active_query_contexts_by_field_path: dict[tuple[int, str], dict[str, Any]],
        verified_query_keys_by_field_path: set[tuple[int, str]],
        query_thread_badge_counts_by_query: dict[int, int],
        query_messages_by_query: dict[int, list[dict[str, Any]]],
        closed_query_histories_by_field_path: dict[tuple[int, str], list[dict[str, Any]]],
        current_user_id: int | None,
        verified_snapshot: dict[str, Any] | None,
        verified_field_template_ids: set[int] | None,
        modified_by_display: str,
    ) -> dict[str, Any] | None:
        try:
            field_template_id = int(field_row.get("id"))
        except (TypeError, ValueError):
            return None
        field_key = str(field_row.get("field_key") or "").strip()
        brief = str(field_row.get("label") or field_key or "—")
        ui_config = field_row.get("ui_config") if isinstance(field_row.get("ui_config"), dict) else {}
        control_norm = self._normalize_control_type(ui_config.get("control_type"))
        options_raw = ui_config.get("options")
        label_by_value = self._options_value_label_map(options_raw)
        raw_value = self._resolve_entry_value_for_field(
            entry_payload,
            field_key=field_key,
            field_template_id=field_template_id,
            repeat_index=repeat_index,
        )
        display_value = self._resolve_display_value(
            raw_value=raw_value,
            control_norm=control_norm,
            label_by_value=label_by_value,
        )
        is_checked = False
        if verified_field_template_ids is not None:
            is_checked = field_template_id in verified_field_template_ids
        elif verified_snapshot is not None:
            is_checked = self.field_storage_matches_snapshot(
                verified_snapshot,
                entry_payload,
                field_key=field_key,
                field_template_id=field_template_id,
                repeat_index=repeat_index,
            )
        display_field_key = self._repeat_field_key(field_key, repeat_index)
        field_path_candidates = self._field_path_candidates(
            section_code=section_code,
            field_key=field_key,
            repeat_index=repeat_index,
            is_repeatable_group_item=is_repeatable_group_item,
        )
        query_key = self._first_matching_query_key(
            field_template_id=field_template_id,
            field_path_candidates=field_path_candidates,
            counts_by_field_path=counts_by_field_path,
            active_query_contexts_by_field_path=active_query_contexts_by_field_path,
            verified_query_keys_by_field_path=verified_query_keys_by_field_path,
            closed_query_histories_by_field_path=closed_query_histories_by_field_path,
        )
        active_query_context = (
            active_query_contexts_by_field_path.get(query_key)
            if query_key is not None
            else None
        )
        active_query_id = (
            active_query_context.get("active_query_id")
            if active_query_context is not None
            else (
                None
                if is_repeatable_group_item
                else active_query_ids.get(field_template_id)
            )
        )
        active_query_id_int = int(active_query_id) if active_query_id else None
        open_query_count = (
            counts_by_field_path.get(query_key, 0)
            if query_key is not None
            else (0 if is_repeatable_group_item else int(counts.get(field_template_id, 0)))
        )
        has_verified_query = (
            query_key in verified_query_keys_by_field_path
            if query_key is not None
            else (False if is_repeatable_group_item else field_template_id in verified_query_field_template_ids)
        )
        validation_issues = self._format_validation_issues(
            validation_issues_by_field.get(field_template_id, []),
            control_norm=control_norm,
            label_by_value=label_by_value,
        )
        validation_issue_count = len(validation_issues)
        closed_query_histories = self._format_closed_query_histories(
            closed_query_histories_by_field_path.get(query_key, [])
            if query_key is not None
            else ([] if is_repeatable_group_item else closed_query_histories_by_field.get(field_template_id, [])),
        )
        validation_issue_histories = self._format_validation_issue_histories(
            validation_issue_histories_by_field.get(field_template_id, []),
            control_norm=control_norm,
            label_by_value=label_by_value,
        )
        return {
            "field_template_id": field_template_id,
            "field_key": display_field_key,
            "repeat_base_field_key": field_key,
            "repeat_instance_index": repeat_index,
            "field_path": field_path_candidates[0] if field_path_candidates else "",
            "is_repeatable_group_item": is_repeatable_group_item,
            "section_title": section_title,
            "section_code": section_code,
            "group_item_label": self._group_item_label(repeat_index),
            "brief_description": brief,
            "display_order": self._sort_int(field_row.get("display_order"), default=0),
            "data_type": str(field_row.get("data_type") or "").strip(),
            "unit": str(field_row.get("unit") or "").strip(),
            "precision": field_row.get("precision"),
            "raw_value": raw_value,
            "display_value": display_value,
            "open_query_count": int(open_query_count),
            "validation_issue_count": int(validation_issue_count),
            "validation_issues": validation_issues,
            "active_query_id": active_query_id,
            "active_query_status": (
                str(active_query_context.get("active_query_status") or "").strip()
                if active_query_context is not None
                else str(active_query_participants.get(field_template_id, {}).get("active_query_status") or "").strip()
            ),
            "active_query_can_respond": self._user_can_respond_to_query(
                current_user_id=current_user_id,
                participants=(
                    active_query_context
                    if active_query_context is not None
                    else active_query_participants.get(field_template_id)
                ),
            ),
            "active_query_is_answered": (
                bool(active_query_context.get("active_query_is_answered"))
                if active_query_context is not None
                else bool(active_query_answered_flags.get(field_template_id, False))
            ),
            "has_verified_query": has_verified_query,
            "query_thread_badge_count": (
                int(query_thread_badge_counts_by_query.get(active_query_id_int, 0))
                if active_query_id_int is not None
                else (0 if is_repeatable_group_item else int(query_thread_badge_counts.get(field_template_id, 0)))
            ),
            "query_messages": self._format_query_messages(
                query_messages_by_query.get(active_query_id_int, [])
                if active_query_id_int is not None
                else ([] if is_repeatable_group_item else query_messages_by_field.get(field_template_id, [])),
            ),
            "closed_query_histories": closed_query_histories,
            "validation_issue_histories": validation_issue_histories,
            "modified_by": modified_by_display,
            "is_checked": is_checked,
        }

    @staticmethod
    def _user_can_respond_to_query(
        *,
        current_user_id: int | None,
        participants: dict[str, int | None] | None,
    ) -> bool:
        if current_user_id is None:
            return False
        try:
            user_id = int(current_user_id)
        except (TypeError, ValueError):
            return False
        return user_id > 0

    @classmethod
    def _first_matching_query_key(
        cls,
        *,
        field_template_id: int,
        field_path_candidates: list[str],
        counts_by_field_path: dict[tuple[int, str], int],
        active_query_contexts_by_field_path: dict[tuple[int, str], dict[str, Any]],
        verified_query_keys_by_field_path: set[tuple[int, str]],
        closed_query_histories_by_field_path: dict[tuple[int, str], list[dict[str, Any]]],
    ) -> tuple[int, str] | None:
        for field_path in field_path_candidates:
            key = (field_template_id, str(field_path or "").strip())
            if (
                key in active_query_contexts_by_field_path
                or key in counts_by_field_path
                or key in verified_query_keys_by_field_path
                or key in closed_query_histories_by_field_path
            ):
                return key
        return None

    @classmethod
    def _field_path_candidates(
        cls,
        *,
        section_code: str,
        field_key: str,
        repeat_index: int,
        is_repeatable_group_item: bool,
    ) -> list[str]:
        normalized_section_code = str(section_code or "").strip()
        normalized_field_key = str(field_key or "").strip()
        if not normalized_field_key:
            return []
        if not normalized_section_code:
            return [cls._jsonpath_for_field_key(cls._repeat_field_key(normalized_field_key, repeat_index))]

        candidates: list[str] = []
        if is_repeatable_group_item:
            row_key = f"row_{int(repeat_index or 1):03d}"
            candidates.append(
                build_field_path(
                    normalized_section_code,
                    normalized_field_key,
                    row_key=row_key,
                )
            )
            if int(repeat_index or 1) == 1:
                candidates.append(build_field_path(normalized_section_code, normalized_field_key))
        else:
            candidates.append(build_field_path(normalized_section_code, normalized_field_key))
        return cls._dedupe_strings(candidates)

    @staticmethod
    def _jsonpath_for_field_key(field_key: str) -> str:
        normalized = str(field_key or "").strip()
        if not normalized:
            return "$"
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", normalized):
            return f"$.{normalized}"
        return f"$[{json.dumps(normalized, ensure_ascii=False)}]"

    @staticmethod
    def _dedupe_strings(values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = str(value or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    @classmethod
    def _group_fields_by_section(cls, field_templates_payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sections_by_key: dict[str, dict[str, Any]] = {}
        for field_position, field_row in enumerate(field_templates_payload or []):
            section_template = (
                field_row.get("section_template")
                if isinstance(field_row.get("section_template"), dict)
                else {}
            )
            section_title = str(section_template.get("name") or "").strip() or "General"
            section_key = str(
                section_template.get("id")
                or section_template.get("code")
                or f"general::{section_title}"
            )
            if section_key not in sections_by_key:
                sections_by_key[section_key] = {
                    "key": section_key,
                    "code": str(section_template.get("code") or "").strip(),
                    "title": section_title,
                    "order": cls._sort_int(section_template.get("display_order"), default=999999),
                    "first_position": field_position,
                    "is_repeatable": bool(section_template.get("is_repeatable")),
                    "max_repeats": section_template.get("max_repeats"),
                    "fields": [],
                }
            sections_by_key[section_key]["fields"].append((field_position, field_row))

        sections = sorted(
            sections_by_key.values(),
            key=lambda section: (
                section["order"],
                section["first_position"],
                str(section["title"]).lower(),
            ),
        )
        for section in sections:
            section["fields"] = [
                field_row
                for _, field_row in sorted(
                    section["fields"],
                    key=lambda item: (
                        cls._sort_int(item[1].get("display_order"), default=999999),
                        item[0],
                    ),
                )
            ]
        return sections

    @staticmethod
    def _sort_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _group_item_label(repeat_index: int) -> str:
        return str(max(1, int(repeat_index or 1)))

    @classmethod
    def _repeat_field_key(cls, field_key: str, repeat_index: int) -> str:
        normalized_field_key = str(field_key or "").strip()
        if repeat_index <= 1:
            return normalized_field_key
        return f"{normalized_field_key}__repeat_{repeat_index}"

    @classmethod
    def _base_storage_aliases(cls, field_key: str, field_template_id: int) -> list[str]:
        aliases = []
        normalized_field_key = str(field_key or "").strip()
        if normalized_field_key:
            aliases.append(normalized_field_key)
        aliases.append(f"field_{int(field_template_id)}")
        return aliases

    @classmethod
    def _repeat_storage_aliases(
        cls,
        *,
        field_key: str,
        field_template_id: int,
        repeat_index: int,
    ) -> list[str]:
        return [
            cls._repeat_field_key(alias, repeat_index)
            for alias in cls._base_storage_aliases(field_key, field_template_id)
        ]

    @classmethod
    def _resolve_repeat_count_for_section(
        cls,
        section_fields: list[dict[str, Any]],
        payload: dict[str, Any],
        max_repeats: Any,
        section_code: str | None = None,
    ) -> int:
        repeat_count = cls._repeat_count_from_payload_meta(payload, section_code)
        if repeat_count is None:
            repeat_count = 0
        aliases: list[str] = []
        for field_row in section_fields or []:
            try:
                field_template_id = int(field_row.get("id"))
            except (TypeError, ValueError):
                continue
            for alias in cls._base_storage_aliases(
                str(field_row.get("field_key") or "").strip(),
                field_template_id,
            ):
                aliases.append(alias)
                if cls._payload_has_meaningful_value_for_storage_key(payload, alias):
                    repeat_count = max(repeat_count, 1)
        escaped_aliases = [re.escape(alias) for alias in aliases if alias]
        if escaped_aliases and isinstance(payload, dict):
            repeat_pattern = re.compile(
                rf"^(?:{'|'.join(escaped_aliases)}){_REPEAT_SUFFIX_RE.pattern}"
            )
            for payload_key, payload_value in payload.items():
                matched = repeat_pattern.match(str(payload_key))
                if matched and cls._has_meaningful_form_value(payload_value):
                    repeat_count = max(repeat_count, int(matched.group("repeat_index")))
        if max_repeats is not None:
            try:
                repeat_count = min(repeat_count, int(max_repeats))
            except (TypeError, ValueError):
                pass
        return repeat_count

    @staticmethod
    def _repeat_count_from_payload_meta(payload: dict[str, Any], section_code: str | None) -> int | None:
        if not isinstance(payload, dict):
            return None
        meta = payload.get(REPEAT_COUNTS_EXPORT_META_KEY)
        if not isinstance(meta, dict):
            return None
        normalized_section_code = str(section_code or "").strip()
        if not normalized_section_code:
            return None
        if normalized_section_code not in meta:
            return None
        try:
            return max(0, int(meta.get(normalized_section_code) or 0))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _payload_has_meaningful_value_for_storage_key(cls, payload: dict[str, Any], storage_key: str) -> bool:
        if not isinstance(payload, dict) or not storage_key:
            return False
        if storage_key in payload and cls._has_meaningful_form_value(payload.get(storage_key)):
            return True
        return any(
            cls._has_meaningful_form_value(payload.get(f"{storage_key}{suffix}"))
            for suffix in _DATE_PART_SUFFIXES
            if f"{storage_key}{suffix}" in payload
        )

    @staticmethod
    def _has_meaningful_form_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return True
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, list):
            return any(FormFieldReviewTableService._has_meaningful_form_value(item) for item in value)
        if isinstance(value, dict):
            return any(FormFieldReviewTableService._has_meaningful_form_value(item) for item in value.values())
        return True

    @classmethod
    def storage_keys_for_field(
        cls,
        field_key: str,
        field_template_id: int,
        *,
        repeat_index: int = 1,
    ) -> list[str]:
        bases = (
            cls._repeat_storage_aliases(
                field_key=field_key,
                field_template_id=field_template_id,
                repeat_index=repeat_index,
            )
            if repeat_index > 1
            else cls._base_storage_aliases(field_key, field_template_id)
        )
        ordered: list[str] = []
        seen: set[str] = set()
        for base in bases:
            candidates = [base, *[f"{base}{suffix}" for suffix in _DATE_PART_SUFFIXES]]
            for k in candidates:
                if k not in seen:
                    seen.add(k)
                    ordered.append(k)
        return ordered

    @classmethod
    def slice_payload_for_field(
        cls,
        payload: dict[str, Any],
        *,
        field_key: str,
        field_template_id: int,
        repeat_index: int = 1,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        out: dict[str, Any] = {}
        for k in cls.storage_keys_for_field(
            field_key,
            field_template_id,
            repeat_index=repeat_index,
        ):
            if k in payload:
                out[k] = payload[k]
        return out

    @classmethod
    def all_form_storage_keys(cls, field_templates_payload: list[dict[str, Any]]) -> set[str]:
        keys: set[str] = set()
        for field_row in field_templates_payload or []:
            try:
                tid = int(field_row.get("id"))
            except (TypeError, ValueError):
                continue
            fk = str(field_row.get("field_key") or "").strip()
            keys.update(cls.storage_keys_for_field(fk, tid))
        return keys

    @classmethod
    def field_storage_matches_snapshot(
        cls,
        snapshot: dict[str, Any],
        entry: dict[str, Any],
        *,
        field_key: str,
        field_template_id: int,
        repeat_index: int = 1,
    ) -> bool:
        a = cls.slice_payload_for_field(
            snapshot,
            field_key=field_key,
            field_template_id=field_template_id,
            repeat_index=repeat_index,
        )
        b = cls.slice_payload_for_field(
            entry,
            field_key=field_key,
            field_template_id=field_template_id,
            repeat_index=repeat_index,
        )
        return cls._storage_slices_equal(a, b)

    @classmethod
    def all_fields_verified_against_entry(
        cls,
        *,
        final_payload: dict[str, Any],
        entry_payload: dict[str, Any],
        field_templates_payload: list[dict[str, Any]],
    ) -> bool:
        for field_row in field_templates_payload or []:
            try:
                tid = int(field_row.get("id"))
            except (TypeError, ValueError):
                continue
            fk = str(field_row.get("field_key") or "").strip()
            if not cls.field_storage_matches_snapshot(final_payload, entry_payload, field_key=fk, field_template_id=tid):
                return False
        return True

    @classmethod
    def _storage_slices_equal(cls, a: dict[str, Any], b: dict[str, Any]) -> bool:
        keys = set(a) | set(b)
        for k in keys:
            if _normalize_storage_scalar(a.get(k)) != _normalize_storage_scalar(b.get(k)):
                return False
        return True

    @staticmethod
    def _format_datetime(value: datetime | None) -> str:
        if value is None:
            return "—"
        local_value = timezone.localtime(value) if timezone.is_aware(value) else value
        return formats.date_format(local_value, format="SHORT_DATETIME_FORMAT", use_l10n=True)

    @classmethod
    def _format_query_messages(cls, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for message in messages[:10]:
            opened_by_id = message.get("opened_by_id")
            opened_by = get_username_display_for_user_id(opened_by_id)
            timestamp = message.get("opened_at") or message.get("created_at")
            out.append(
                {
                    "dataquery_id": message.get("dataquery_id"),
                    "text": str(message.get("text") or "").strip(),
                    "status": str(message.get("status") or "").strip(),
                    "tone": str(message.get("tone") or "").strip(),
                    "opened_by": opened_by,
                    "opened_at": cls._format_datetime(timestamp),
                },
            )
        return out

    @classmethod
    def _format_closed_query_histories(cls, histories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for history in histories:
            dataquery_id = history.get("dataquery_id")
            closed_at = history.get("closed_at") or history.get("updated_at") or history.get("created_at")
            out.append(
                {
                    "dataquery_id": dataquery_id,
                    "status": str(history.get("status") or "").strip(),
                    "label": f"Query #{dataquery_id}" if dataquery_id else "Query",
                    "question_text": str(history.get("question_text") or "").strip(),
                    "opened_at": cls._format_datetime(history.get("opened_at")),
                    "closed_at": cls._format_datetime(closed_at),
                    "messages": cls._format_query_messages(history.get("messages", [])),
                },
            )
        return out

    def _format_validation_issues(
        self,
        issues: list[dict[str, Any]],
        *,
        control_norm: str,
        label_by_value: dict[str, str],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for issue in issues:
            normalized = dict(issue)
            normalized["failed_value_display"] = self._resolve_display_value(
                raw_value=issue.get("failed_value"),
                control_norm=control_norm,
                label_by_value=label_by_value,
            )
            out.append(normalized)
        return out

    def _format_validation_issue_histories(
        self,
        issues: list[dict[str, Any]],
        *,
        control_norm: str,
        label_by_value: dict[str, str],
    ) -> list[dict[str, Any]]:
        grouped: dict[object, list[dict[str, Any]]] = {}
        for issue in issues:
            grouped.setdefault(issue.get("validation_issue_id") or issue.get("id"), []).append(issue)
        histories = []
        for issue_id, snapshots in grouped.items():
            snapshots.sort(key=lambda item: (
                item.get("created_at") is not None,
                item.get("created_at") or datetime.min,
                int(item.get("id") or 0),
            ))
            messages = []
            latest_status = "OPEN"
            for snapshot in snapshots:
                acknowledged = snapshot.get("source") == "VALIDATION_ISSUE_ACKNOWLEDGEMENT"
                latest_status = "Acknowledged" if acknowledged else (
                    str(snapshot.get("result") or snapshot.get("status") or "OPEN").strip().upper()
                )
                if not acknowledged and snapshot.get("acknowledgment_preserved") and latest_status == "FAIL":
                    latest_status = "Acknowledgement retained"
                value = self._resolve_display_value(
                    raw_value=snapshot.get("evaluated_value"), control_norm=control_norm,
                    label_by_value=label_by_value,
                )
                # Format each snapshot independently so the generic query's ten-message cap
                # cannot silently truncate a validation issue's audit history.
                message = self._format_query_messages([{
                    "dataquery_id": f"validation_issue_{issue_id}_snapshot_{snapshot.get('id')}",
                    "text": snapshot.get("message"), "status": latest_status,
                    "tone": "resolved" if acknowledged or latest_status in {"PASS", "Acknowledgement retained"} else "warning",
                    "created_at": snapshot.get("created_at"),
                    "opened_by_id": snapshot.get("triggered_by"),
                }])[0]
                timestamp = snapshot.get("created_at")
                if timestamp is not None:
                    local_timestamp = timezone.localtime(timestamp) if timezone.is_aware(timestamp) else timestamp
                    message["opened_at"] = (
                        f"{formats.date_format(local_timestamp, 'SHORT_DATE_FORMAT')} "
                        f"{formats.time_format(local_timestamp, 'H:i:s')}"
                    )
                message["value_snapshot"] = value
                messages.append(message)
            first, last = snapshots[0], snapshots[-1]
            histories.append({
                "dataquery_id": f"validation_issue_{issue_id}",
                "status": latest_status,
                "label": f"Validation Issue #{issue_id}" if issue_id else "Validation Issue",
                "question_text": str(first.get("message") or "").strip(),
                "opened_at": self._format_datetime(first.get("created_at")),
                "closed_at": self._format_datetime(last.get("created_at")),
                "value_snapshot": messages[-1]["value_snapshot"],
                "messages": messages,
                "_sort": (last.get("created_at") is not None, last.get("created_at") or datetime.min,
                          int(last.get("id") or 0)),
            })
        histories.sort(key=lambda history: history["_sort"], reverse=True)
        for history in histories:
            history.pop("_sort")
        return histories

    @staticmethod
    def _normalize_control_type(control_type: object) -> str:
        raw = str(control_type or "").strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "textbox": "text",
            "entry_box": "text",
            "numeric": "number",
            "date_picker": "date",
            "date_text": "date",
            "time_picker": "datetime",
            "datetime_text": "datetime",
            "textarea": "free_text",
            "dropdown": "select",
            "dropdown_list": "select",
            "radio_button_list": "radio",
            "checkbox_list": "multi_select",
        }
        normalized = aliases.get(raw, raw)
        if normalized == "textarea":
            return "free_text"
        return normalized

    @staticmethod
    def _resolve_stored_value(payload: dict[str, Any], canonical_key: str) -> Any:
        if not canonical_key or not payload:
            return None
        date_keys = [f"{canonical_key}{suffix}" for suffix in _DATE_PART_SUFFIXES]
        if any(k in payload for k in date_keys):
            return {
                "__day": payload.get(f"{canonical_key}__day"),
                "__month": payload.get(f"{canonical_key}__month"),
                "__year": payload.get(f"{canonical_key}__year"),
                "__time": payload.get(f"{canonical_key}__time"),
            }
        return payload.get(canonical_key)

    @classmethod
    def _resolve_entry_value_for_field(
        cls,
        payload: dict[str, Any],
        *,
        field_key: str,
        field_template_id: int,
        repeat_index: int = 1,
    ) -> Any:
        if not payload:
            return None
        if repeat_index > 1:
            for alias in cls._repeat_storage_aliases(
                field_key=field_key,
                field_template_id=field_template_id,
                repeat_index=repeat_index,
            ):
                value = cls._resolve_stored_value(payload, alias)
                if value is not None:
                    return value
            return None
        if field_key:
            value = cls._resolve_stored_value(payload, field_key)
            if value is not None:
                return value
        alt_key = f"field_{field_template_id}"
        return cls._resolve_stored_value(payload, alt_key)

    @staticmethod
    def _is_date_parts_dict(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        return any(k in value for k in ("__day", "__month", "__year", "__time"))

    @staticmethod
    def _format_date_part_map(value: dict[str, Any]) -> str:
        day = str(value.get("__day") or "").strip()
        month = str(value.get("__month") or "").strip()
        year = str(value.get("__year") or "").strip()
        time_part = str(value.get("__time") or "").strip()
        if not (day or month or year) and not time_part:
            return "—"
        try:
            if year and month and day:
                d = date(int(year), int(month), int(day))
                out = formats.date_format(d, format="SHORT_DATE_FORMAT", use_l10n=True)
                return f"{out} {time_part}".strip() if time_part else out
        except (ValueError, TypeError):
            pass
        parts = [p for p in (day, month, year) if p]
        base = " / ".join(parts) if parts else "—"
        return f"{base} {time_part}".strip() if time_part else base

    @staticmethod
    def _parse_options_list(parsed: Any) -> list[Any]:
        if isinstance(parsed, dict) and parsed.get("source") == "static":
            inner = parsed.get("static")
            if isinstance(inner, list):
                return inner
        return []

    def _options_value_label_map(self, options_raw: object) -> dict[str, str]:
        if options_raw in (None, ""):
            return {}
        if isinstance(options_raw, (list, dict)):
            parsed = options_raw
        else:
            text = str(options_raw).strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
        out: dict[str, str] = {}
        for item in self._parse_options_list(parsed):
            if not isinstance(item, dict):
                continue
            value = item.get("value", item.get("code", item.get("id", "")))
            value_key = "" if value is None else str(value).strip()
            label = item.get("label", item.get("text", value_key))
            label_text = "" if label is None else str(label).strip()
            if value_key:
                out[value_key] = label_text or value_key
        return out

    def _resolve_display_value(
        self,
        *,
        raw_value: Any,
        control_norm: str,
        label_by_value: dict[str, str],
    ) -> str:
        if not label_by_value:
            return self._raw_display_value(raw_value)
        return self._map_options_value(raw_value, label_by_value)

    @staticmethod
    def _raw_display_value(raw_value: Any) -> str:
        if raw_value is None:
            return "—"
        if FormFieldReviewTableService._is_date_parts_dict(raw_value):
            return FormFieldReviewTableService._format_date_part_map(raw_value)
        if isinstance(raw_value, bool):
            return "true" if raw_value else "false"
        if isinstance(raw_value, (list, dict)):
            text = json.dumps(raw_value, ensure_ascii=False)
            return text if text else "—"
        text = str(raw_value).strip()
        return text if text else "—"

    def _map_options_value(
        self,
        raw_value: Any,
        label_by_value: dict[str, str],
    ) -> str:
        if not label_by_value:
            return self._raw_display_value(raw_value)
        if FormFieldReviewTableService._is_date_parts_dict(raw_value):
            return self._raw_display_value(raw_value)
        tokens = self._storage_tokens(raw_value)
        if not tokens:
            return "—"
        labels = [label_by_value.get(t, t) for t in tokens]
        joined = ", ".join(labels)
        return joined if joined else "—"

    @staticmethod
    def _storage_tokens(raw_value: Any) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, bool):
            return ["true"] if raw_value else ["false"]
        if isinstance(raw_value, list):
            return ["" if v is None else str(v).strip() for v in raw_value if str(v).strip() != ""]
        if isinstance(raw_value, dict):
            return [json.dumps(raw_value, ensure_ascii=False)]
        text = str(raw_value).strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                loaded = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, list):
                return FormFieldReviewTableService._storage_tokens(loaded)
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip() != ""]
        return [text]


__all__ = ["FormFieldReviewTableService"]
