import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.subject.application.services.audit_history import SubjectAuditHistoryQueryService
from apps.subject.infrastructure.repositories.audit_history import DjangoSubjectAuditHistoryRepository
from tests.subject.test_subject_audit_history import _SubjectAuditHistoryRepositoryStub


class SubjectImportAuditHistoryTests(SimpleTestCase):
    def test_import_changes_are_scoped_to_subject_visits_and_expand_to_field_rows(self):
        change = dict(key="638:2419", field_key="ALT", status="overwrite", old_value=0, new_value="52",
                      form_code="LAB", field_label="ALT (GPT)", column="B")
        payload = dict(filename="NTN.xlsx", row_number=5, changes=[change,
            dict(change, key="638:2420", field_key="AST"), dict(change, status="unchanged")],
            field_decisions={"638:2420": "ecrf"})
        event = dict(id=765, object_id="421", created_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
                     created_by_id=44, created_by__username="tester", created_by__display_name="Tester",
                     after_data=json.dumps(payload))
        with (patch("apps.subject.infrastructure.repositories.audit_history.SubjectEventInstance") as visits,
              patch("apps.subject.infrastructure.repositories.audit_history.list_file_import_events", return_value=[event]) as read):
            visits.objects.filter.return_value.values_list.return_value = [(421, "SCREENING")]
            rows = DjangoSubjectAuditHistoryRepository().list_import_field_history(study_id=3, subject_id=29)
        visits.objects.filter.assert_called_once_with(subject_id=29, subject__study_id=3, deleted=False)
        read.assert_called_once_with(visit_ids={421: "SCREENING"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], "0 → 52")
        self.assertEqual(rows[0]["user_display"], "Tester")
        self.assertEqual(rows[0]["field_description"], "SCREENING / LAB / ALT (GPT)")

    def test_subject_history_includes_import_rows_in_count_and_filters(self):
        repo = _SubjectAuditHistoryRepositoryStub()
        repo.list_import_field_history = Mock(return_value=[dict(occurred_at=None, category="file_import",
            source="File Import", field_name="ALT", user_display="Tester", value="6 → 52",
            details=[dict(label="File", value="NTN.xlsx")])])
        service = SubjectAuditHistoryQueryService(repository=repo,
            datacapture_history_reader=lambda **kwargs: [], study_gate_history_reader=lambda **kwargs: [])
        result = service.get_subject_audit_history(study_id=1, subject_id=20, field_name="ALT", user="Tester", search="NTN")
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["records"][0]["value"], "6 → 52")
        self.assertEqual(next(x["count"] for x in result["source_counts"] if x["key"] == "file_import"), 1)
