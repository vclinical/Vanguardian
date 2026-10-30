import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.subject.infrastructure.repositories.field_audit_history import DjangoSubjectFieldAuditHistoryRepository


class ImportFieldAuditHistoryTests(SimpleTestCase):
    def test_draft_imports_appear_in_field_history_with_actor_and_before_after(self):
        repo = DjangoSubjectFieldAuditHistoryRepository()
        change = dict(key="638:2419", form_id=360, field_key="ALT", status="overwrite", old_value="6", new_value="52")
        event = dict(id=1, created_at=datetime(2026, 10, 30, tzinfo=timezone.utc), created_by_id=44,
                     created_by__display_name="Tester", created_by__username="tester",
                     after_data=json.dumps({"changes": [change]}))
        with (patch.object(repo, "_get_page_state", return_value=SimpleNamespace(pk=577, event_form_binding_id=638)),
              patch.object(repo, "_load_user_display_by_id", return_value={}),
              patch.object(repo, "_build_page_entry_rows", return_value=[]),
              patch.object(repo, "_build_field_review_rows", return_value=[]),
              patch("apps.subject.infrastructure.repositories.field_audit_history.list_file_import_events", return_value=[event]) as read):
            rows = repo.list_field_audit_history(study_id=3, subject_id=44, visit_id=811,
                crf_template_id=360, field_template_id=2419, field_key="ALT")
        read.assert_called_once_with(visit_id=811)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["value_from"], rows[0]["value_to"], rows[0]["changed_by"]), ("6", "52", "Tester"))
        self.assertIn("draft", rows[0]["audit_event"])

    def test_other_forms_fields_bindings_and_kept_values_are_excluded(self):
        change = dict(key="638:2419", form_id=360, field_key="ALT", status="overwrite", old_value="6", new_value="52")
        payloads = [
            {"changes": [dict(change, form_id=359)]},
            {"changes": [dict(change, field_key="AST")]},
            {"changes": [dict(change, key="639:2419")]},
            {"changes": [change], "field_decisions": {"638:2419": "ecrf"}},
            {"changes": [dict(change, status="unchanged")]},
            {"changes": [None]},
        ]
        events = [{"after_data": json.dumps(payload)} for payload in payloads] + [{"after_data": "invalid"}]
        with patch("apps.subject.infrastructure.repositories.field_audit_history.list_file_import_events", return_value=events):
            rows = DjangoSubjectFieldAuditHistoryRepository()._build_file_import_rows(visit_id=811,
                crf_template_id=360, field_template_id=2419, field_key="ALT", event_form_binding_id=638)
        self.assertEqual(rows, [])
