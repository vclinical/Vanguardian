from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.datacapture.models import DataCapturePageEntry
from apps.subject.infrastructure.repositories.field_audit_history import DjangoSubjectFieldAuditHistoryRepository


class FieldAuditSubmitAttributionTests(SimpleTestCase):
    def test_superseded_entry_keeps_original_submit_actor_and_time(self):
        submitted = datetime(2026, 10, 28, tzinfo=timezone.utc)
        entry = SimpleNamespace(id=489, entry_no=1, status="superseded", data='{"RBC":"5.38"}',
            submitted_at=submitted, submitted_by_id=31, created_at=submitted, created_by_id=32,
            updated_at=submitted + timedelta(days=2), updated_by_id=23)
        query = MagicMock()
        query.exclude.return_value.only.return_value.order_by.return_value = [entry]
        with patch.object(DataCapturePageEntry.objects, "filter", return_value=query):
            rows = DjangoSubjectFieldAuditHistoryRepository()._build_page_entry_rows(
                page_state_id=656, field_key="RBC", user_display_by_id={31: "Original submitter", 23: "Importer"})
        self.assertEqual(rows[0]["changed_by"], "Original submitter")
        self.assertEqual(rows[0]["changed_at"], submitted)

    def history(self, submitted_value):
        time = datetime(2026, 10, 28, tzinfo=timezone.utc)
        return [
            dict(sort_key="entry:1", changed_at=time, value_from="", value_to="5.38"),
            dict(sort_key="import:1", changed_at=time + timedelta(days=2), value_from="5.38", value_to="5.50"),
            dict(sort_key="entry:2", changed_at=time + timedelta(days=2, minutes=7), value_from="5.38", value_to=submitted_value),
        ]

    def test_submit_same_imported_value_does_not_duplicate_change(self):
        rows = DjangoSubjectFieldAuditHistoryRepository._merge_value_history(self.history("5.50"))
        self.assertEqual([(row["value_from"], row["value_to"]) for row in rows], [("", "5.38"), ("5.38", "5.50")])

    def test_manual_edit_or_revert_after_import_is_still_audited(self):
        for value in ("5.60", "5.38"):
            rows = DjangoSubjectFieldAuditHistoryRepository._merge_value_history(self.history(value))
            self.assertEqual(len(rows), 3)
            self.assertEqual((rows[-1]["value_from"], rows[-1]["value_to"]), ("5.50", value))
