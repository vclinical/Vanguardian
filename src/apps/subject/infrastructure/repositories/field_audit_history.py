from __future__ import annotations

import json
import re

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.audit.public import list_file_import_events
from apps.core.choices import (
    DataCaptureFieldReviewStatusChoices,
    DataCaptureFieldReviewTypeChoices,
    DataCapturePageEntryStatusChoices,
)
from apps.crf.models import CrfFieldTemplate
from apps.datacapture.infrastructure.persistence.models import (
    DataCaptureFieldReview,
    DataCapturePageEntry,
    DataCapturePageState,
)
from apps.identity.models import User
from apps.subject.models import Subject


class DjangoSubjectFieldAuditHistoryRepository:
    _date_time_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?$")

    def get_subject_context(self, *, study_id: int, subject_id: int):
        subject = (
            Subject.objects.filter(study_id=study_id, pk=subject_id, deleted=False)
            .select_related("study", "site")
            .only("id", "study_id", "subject_code", "screening_code", "study__code", "study__name", "site__code")
            .first()
        )
        if subject is None:
            return None
        return {
            "subject_id": int(subject.pk),
            "study_id": int(subject.study_id),
            "study_code": getattr(subject.study, "code", "") or "",
            "study_name": getattr(subject.study, "name", "") or "",
            "site_code": getattr(subject.site, "code", "") or "",
            "screening_code": subject.screening_code or "",
            "subject_code": subject.subject_code or "",
        }

    def list_field_audit_history(
        self,
        *,
        study_id: int,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        field_template_id: int,
        field_key: str,
        limit: int = 100,
        event_form_binding_id: int | None = None,
    ) -> list[dict]:
        page_state = self._get_page_state(
            study_id=study_id,
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            event_form_binding_id=event_form_binding_id,
        )
        if page_state is None:
            return []

        normalized_field_key = self._resolve_field_key(field_template_id=field_template_id, field_key=field_key)
        user_display_by_id = self._load_user_display_by_id(page_state_id=page_state.pk)
        rows: list[dict] = []
        rows.extend(self._build_file_import_rows(
            visit_id=visit_id, crf_template_id=crf_template_id,
            field_template_id=field_template_id, field_key=normalized_field_key,
            event_form_binding_id=page_state.event_form_binding_id,
        ))
        rows.extend(
            self._build_page_entry_rows(
                page_state_id=page_state.pk,
                field_key=normalized_field_key,
                user_display_by_id=user_display_by_id,
            )
        )
        rows = self._merge_value_history(rows)
        rows.extend(
            self._build_field_review_rows(
                page_state_id=page_state.pk,
                field_template_id=field_template_id,
                field_key=normalized_field_key,
                user_display_by_id=user_display_by_id,
            )
        )
        rows.sort(
            key=lambda row: (
                row["changed_at"] is not None,
                row["changed_at"],
                row["sort_key"],
            ),
            reverse=True,
        )
        if limit:
            rows = rows[:limit]
        return [
            {
                "audit_event": row["audit_event"],
                "changed_at": self._format_datetime(row["changed_at"]),
                "changed_by": row["changed_by"],
                "field_name": row["field_name"],
                "value_from": row["value_from"],
                "value_to": row["value_to"],
            }
            for row in rows
        ]

    def _build_file_import_rows(self, *, visit_id, crf_template_id, field_template_id, field_key, event_form_binding_id):
        rows = []
        for event in list_file_import_events(visit_id=visit_id):
            try:
                payload = json.loads(event["after_data"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict) or not isinstance(payload.get("changes"), list):
                continue
            for change in payload["changes"]:
                if not isinstance(change, dict):
                    continue
                if (change.get("form_id") != crf_template_id or change.get("field_key") != field_key
                        or change.get("key") != f"{event_form_binding_id}:{field_template_id}"
                        or change.get("status") not in {"new", "overwrite"}
                        or payload.get("field_decisions", {}).get(change["key"]) == "ecrf"):
                    continue
                rows.append({
                    "sort_key": f"import:{event['id']:09d}",
                    "audit_event": "Item data value imported (submitted)" if payload.get("entry_status") == "submitted"
                        else "Item data value imported (draft)",
                    "submitted_entry_id": next((entry["entry_id"] for entry in payload.get("submitted_entries", [])
                        if entry["form_id"] == crf_template_id), None),
                    "changed_at": event["created_at"],
                    "changed_by": event["created_by__display_name"] or event["created_by__username"]
                        or self._user_display(event["created_by_id"], {}),
                    "field_name": field_key,
                    "value_from": self._display_value(change.get("old_value")),
                    "value_to": self._display_value(change.get("new_value")),
                })
        return rows

    def _build_page_entry_rows(self, *, page_state_id: int, field_key: str, user_display_by_id: dict[int, str]) -> list[dict]:
        rows: list[dict] = []
        previous_value = ""
        entries = (
            DataCapturePageEntry.objects.filter(page_state_id=page_state_id, deleted=False)
            .exclude(status=DataCapturePageEntryStatusChoices.DRAFT)
            .only("id", "entry_no", "data", "submitted_at", "submitted_by_id", "created_at", "created_by_id", "status")
            .order_by("entry_no", "id")
        )
        for entry in entries:
            if entry.status not in {DataCapturePageEntryStatusChoices.SUBMITTED, DataCapturePageEntryStatusChoices.SUPERSEDED}:
                continue
            payload = self._extract_entry_payload_map(entry.data)
            current_value = self._stringify_value(payload.get(field_key, ""))
            rows.append(
                {
                    "sort_key": f"entry:{int(entry.entry_no or 0):09d}:{int(entry.id or 0):09d}",
                    "entry_id": entry.id,
                    "audit_event": "Item data value updated",
                    "changed_at": entry.submitted_at or entry.created_at,
                    "changed_by": self._user_display(entry.submitted_by_id or entry.created_by_id, user_display_by_id),
                    "field_name": field_key,
                    "value_from": self._display_value(previous_value),
                    "value_to": self._display_value(current_value),
                }
            )
            previous_value = current_value
        return rows

    @staticmethod
    def _merge_value_history(rows):
        merged = []
        current_value = ""
        imported_entry_ids = {row["submitted_entry_id"] for row in rows if row.get("submitted_entry_id")}
        for row in sorted(rows, key=lambda item: (item["changed_at"] is not None, item["changed_at"], item["sort_key"])):
            if row.get("entry_id") in imported_entry_ids:
                continue
            if row["sort_key"].startswith("entry:"):
                # Submitting the imported draft is not another value change.
                if row["value_to"] == current_value:
                    continue
                row = {**row, "value_from": current_value}
            merged.append(row)
            current_value = row["value_to"]
        return merged

    def _build_field_review_rows(
        self,
        *,
        page_state_id: int,
        field_template_id: int,
        field_key: str,
        user_display_by_id: dict[int, str],
    ) -> list[dict]:
        review = (
            DataCaptureFieldReview.objects.filter(
                page_state_id=page_state_id,
                field_template_id=field_template_id,
                review_type=DataCaptureFieldReviewTypeChoices.DATA_REVIEW,
                deleted=False,
            )
            .only(
                "id",
                "updated_at",
                "verified_at",
                "verified_by_id",
                "updated_by_id",
                "status",
                "value_snapshot",
            )
            .order_by("-updated_at", "-id")
            .first()
        )
        if review is None:
            return []
        normalized_status = str(review.status or "").strip().lower()
        if normalized_status == str(DataCaptureFieldReviewStatusChoices.VERIFIED).lower():
            value_from = "Not Verified"
            value_to = "Verified"
            changed_at = review.verified_at or review.updated_at
            changed_by = review.verified_by_id or review.updated_by_id
        elif normalized_status == str(DataCaptureFieldReviewStatusChoices.STALE).lower():
            value_from = "Verified"
            value_to = "Not Verified"
            changed_at = review.updated_at
            changed_by = review.updated_by_id or review.verified_by_id
        elif normalized_status == str(DataCaptureFieldReviewStatusChoices.WAIVED).lower():
            value_from = "Not Verified"
            value_to = "Waived"
            changed_at = review.verified_at or review.updated_at
            changed_by = review.verified_by_id or review.updated_by_id
        else:
            return []

        return [
            {
                "sort_key": f"review:{changed_at.isoformat() if changed_at else ''}:{int(review.id or 0):09d}",
                "audit_event": "Item Data SDV Status Updated",
                "changed_at": changed_at,
                "changed_by": self._user_display(changed_by, user_display_by_id),
                "field_name": field_key or f"field_{field_template_id}",
                "value_from": value_from,
                "value_to": value_to,
            }
        ]

    def _get_page_state(
        self,
        *,
        study_id: int,
        subject_id: int,
        visit_id: int,
        crf_template_id: int,
        event_form_binding_id: int | None,
    ):
        queryset = DataCapturePageState.objects.filter(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            deleted=False,
        )
        if event_form_binding_id is not None:
            queryset = queryset.filter(event_form_binding_id=event_form_binding_id)
        return (
            queryset.select_related("subject", "visit", "crf_template")
            .only("id", "subject_id", "visit_id", "crf_template_id")
            .first()
        )

    def _load_user_display_by_id(self, *, page_state_id: int) -> dict[int, str]:
        user_ids: set[int] = set()
        for submitted_by, created_by in DataCapturePageEntry.objects.filter(
            page_state_id=page_state_id, deleted=False,
        ).exclude(status=DataCapturePageEntryStatusChoices.DRAFT).values_list("submitted_by_id", "created_by_id"):
            if submitted_by or created_by:
                user_ids.add(int(submitted_by or created_by))
        user_ids.update(
            int(row)
            for row in DataCaptureFieldReview.objects.filter(page_state_id=page_state_id, deleted=False)
            .exclude(verified_by_id__isnull=True)
            .values_list("verified_by_id", flat=True)
        )
        user_ids.update(
            int(row)
            for row in DataCaptureFieldReview.objects.filter(page_state_id=page_state_id, deleted=False)
            .exclude(updated_by_id__isnull=True)
            .values_list("updated_by_id", flat=True)
        )
        if not user_ids:
            return {}
        rows = User.objects.filter(pk__in=user_ids, deleted=False).values(
            "id",
            "display_name",
            "first_name",
            "last_name",
            "username",
        )
        display_by_id: dict[int, str] = {}
        for row in rows:
            user_id = int(row["id"])
            display = " ".join(
                part
                for part in (
                    str(row.get("display_name") or "").strip(),
                    str(row.get("first_name") or "").strip(),
                    str(row.get("last_name") or "").strip(),
                    str(row.get("username") or "").strip(),
                )
                if part
            ).strip()
            display_by_id[user_id] = display or f"User #{user_id}"
        return display_by_id

    def _resolve_field_key(self, *, field_template_id: int, field_key: str) -> str:
        normalized_field_key = str(field_key or "").strip()
        if normalized_field_key:
            return normalized_field_key
        template_field_key = (
            CrfFieldTemplate.objects.filter(pk=field_template_id, deleted=False)
            .values_list("field_key", flat=True)
            .first()
        )
        return str(template_field_key or "").strip() or f"field_{field_template_id}"

    def _extract_entry_payload_map(self, raw_payload: str | None) -> dict:
        try:
            loaded = json.loads(raw_payload or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        from apps.core.form_data_document import flatten_form_data_for_export, normalize_form_data

        doc = normalize_form_data(loaded, strict=False)
        return flatten_form_data_for_export(doc, repeat_strategy="legacy_repeat_suffix")

    @classmethod
    def _stringify_value(cls, raw_value) -> str:
        if raw_value is None:
            return ""
        if isinstance(raw_value, bool):
            return "true" if raw_value else "false"
        if isinstance(raw_value, (list, tuple)):
            return ", ".join(cls._stringify_value(item) for item in raw_value if cls._stringify_value(item))
        if isinstance(raw_value, dict):
            return json.dumps(raw_value, ensure_ascii=False, sort_keys=True)
        return str(raw_value).strip()

    @classmethod
    def _display_value(cls, raw_value) -> str:
        text = cls._stringify_value(raw_value)
        if not text:
            return ""
        if cls._date_time_pattern.match(text):
            parsed_date = parse_date(text)
            if parsed_date is not None:
                return parsed_date.strftime("%d-%m-%Y")
            parsed_datetime = parse_datetime(text)
            if parsed_datetime is not None:
                if parsed_datetime.hour == 0 and parsed_datetime.minute == 0 and parsed_datetime.second == 0:
                    return parsed_datetime.strftime("%d-%m-%Y")
                return parsed_datetime.strftime("%d-%m-%Y %H:%M")
        return text

    @staticmethod
    def _humanize_value(value) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return text.replace("_", " ").replace("-", " ").title()

    @staticmethod
    def _format_datetime(value) -> str:
        if value is None:
            return ""
        try:
            if timezone.is_aware(value):
                value = timezone.localtime(value)
            return value.strftime("%d-%m-%Y %H:%M")
        except AttributeError:
            return str(value)

    @classmethod
    def _user_display(cls, user_id, user_display_by_id: dict[int, str]) -> str:
        if user_id in (None, ""):
            return "System"
        try:
            normalized_user_id = int(user_id)
        except (TypeError, ValueError):
            return "System"
        return user_display_by_id.get(normalized_user_id, f"User #{normalized_user_id}")


__all__ = ["DjangoSubjectFieldAuditHistoryRepository"]
