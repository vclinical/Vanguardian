from datetime import datetime, timezone
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import resolve, reverse

from apps.subject.application.services.audit_history import SubjectAuditHistoryQueryService
from apps.subject.presentation.web.forms import SubjectAuditHistoryFilterForm
from apps.subject.presentation.web.views.audit_history import SubjectAuditHistoryView


class SubjectAuditHistoryQueryServiceTests(SimpleTestCase):
    def test_audit_history_url_routes_to_subject_audit_history_view(self):
        url = reverse("subject:subject_audit_history", kwargs={"study_id": 1, "subject_id": 20})

        match = resolve(url)

        self.assertEqual(match.func.view_class, SubjectAuditHistoryView)

    def test_audit_history_template_uses_query_workbench_table_layout(self):
        template_source = Path("src/templates/subject/subject_audit_history.html").read_text()

        self.assertIn("query-workbench subject-audit-workbench", template_source)
        self.assertIn("{% render_table table %}", template_source)
        self.assertIn('_entity_table_toolbar_icon.html"', template_source)
        self.assertNotIn("filter_form.field_name", template_source)
        self.assertIn("filter_form.user", template_source)
        self.assertNotIn("filter_form.search", template_source)
        self.assertIn("subject-audit-workbench__search-placeholder", template_source)

    def test_audit_history_filter_form_maps_user(self):
        form = SubjectAuditHistoryFilterForm(
            {
                "user": "Nguyen CRC",
            },
            user_choices=("Nguyen CRC", "System"),
        )

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["user"], "Nguyen CRC")
        self.assertNotIn("field_name", form.fields)
        self.assertNotIn("search", form.fields)
        self.assertEqual(
            list(form.fields["user"].widget.choices),
            [("", "All Users"), ("Nguyen CRC", "Nguyen CRC"), ("System", "System")],
        )
        self.assertEqual(
            form.fields["user"].widget.attrs["onchange"],
            "this.form.requestSubmit()",
        )

    def test_audit_history_table_rows_fit_and_wrap_long_content(self):
        stylesheet = Path("src/staticfiles/subject/css/subject_audit_history.css").read_text()

        self.assertIn("height: auto;", stylesheet)
        self.assertIn("white-space: normal;", stylesheet)
        self.assertIn("overflow-wrap: anywhere;", stylesheet)
        self.assertIn("word-break: break-word;", stylesheet)
        self.assertIn(".subject-audit-workbench__search-placeholder", stylesheet)
        self.assertIn("flex: 0 0 220px;", stylesheet)

    def test_audit_history_search_uses_mariadb_compatible_audit_fields(self):
        sources = "\n".join(
            Path(path).read_text()
            for path in (
                "src/apps/subject/infrastructure/repositories/audit_history.py",
                "src/apps/datacapture/infrastructure/repositories/page_capture.py",
                "src/apps/study/infrastructure/repositories/event_gate.py",
            )
        )

        self.assertNotIn("django.contrib.postgres.search", sources)
        self.assertIn("audit_value__icontains", sources)
        self.assertIn("audit_field_description__icontains", sources)
        self.assertIn("audit_user_display__icontains", sources)

    def test_get_subject_audit_history_combines_sources_and_sorts_latest_first(self):
        repository = _SubjectAuditHistoryRepositoryStub()
        datacapture_reader = _PageStateHistoryReaderStub()
        study_gate_reader = _EventGateHistoryReaderStub()
        service = SubjectAuditHistoryQueryService(
            repository=repository,
            datacapture_history_reader=datacapture_reader,
            study_gate_history_reader=study_gate_reader,
        )

        result = service.get_subject_audit_history(
            study_id=1,
            subject_id=20,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "SUBJ-001")
        self.assertEqual(result["total_count"], 6)
        self.assertEqual(
            [record["source"] for record in result["records"]],
            [
                "Subject Identifier",
                "Period Transition",
                "Event Gate",
                "Page State",
                "Event Transition",
                "Subject Status",
            ],
        )
        self.assertEqual(
            {item["key"]: item["count"] for item in result["source_counts"]},
            {
                "subject_identifier": 1,
                "subject_status": 1,
                "period_transition": 1,
                "event_transition": 1,
                "page_state": 1,
                "event_gate": 1,
                "file_import": 0,
            },
        )
        period_record = result["records"][1]
        self.assertEqual(period_record["to_value"], "Period 2 / Active")
        self.assertEqual(period_record["field_name"], "period_status")
        self.assertEqual(period_record["user_display"], "Nguyen CRC")
        self.assertIn(
            {"label": "Override ID", "value": "71"},
            period_record["details"],
        )
        self.assertEqual(
            period_record["reason"],
            "Source Document Issue: Tài liệu nguồn cần hiệu chỉnh",
        )
        self.assertEqual(result["user_options"], ["Nguyen CRC", "System", "User #11"])
        self.assertEqual(repository.status_kwargs, {"search": "", "field_name": ""})
        self.assertEqual(repository.period_kwargs, {"search": "", "field_name": ""})
        self.assertEqual(datacapture_reader.kwargs, {"search": "", "field_name": ""})
        self.assertEqual(study_gate_reader.kwargs, {"search": "", "field_name": ""})

    def test_get_subject_audit_history_filters_visible_field_user_and_details(self):
        service = SubjectAuditHistoryQueryService(
            repository=_SubjectAuditHistoryRepositoryStub(),
            datacapture_history_reader=_PageStateHistoryReaderStub(),
            study_gate_history_reader=_EventGateHistoryReaderStub(),
        )

        result = service.get_subject_audit_history(
            study_id=1,
            subject_id=20,
            field_name="period_status",
            user="Nguyen CRC",
            search="Override ID 71",
        )

        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["records"][0]["source"], "Period Transition")
        self.assertEqual(result["user_options"], ["Nguyen CRC", "System", "User #11"])

    def test_get_subject_audit_history_search_does_not_match_other_columns(self):
        service = SubjectAuditHistoryQueryService(
            repository=_SubjectAuditHistoryRepositoryStub(),
            datacapture_history_reader=_PageStateHistoryReaderStub(),
            study_gate_history_reader=_EventGateHistoryReaderStub(),
        )

        result = service.get_subject_audit_history(
            study_id=1,
            subject_id=20,
            search="EPREX_4000U",
        )

        self.assertEqual(result["records"], [])

    def test_get_subject_audit_history_returns_none_when_subject_is_missing(self):
        service = SubjectAuditHistoryQueryService(
            repository=_MissingSubjectAuditHistoryRepositoryStub(),
            datacapture_history_reader=_PageStateHistoryReaderStub(),
            study_gate_history_reader=_EventGateHistoryReaderStub(),
        )

        self.assertIsNone(service.get_subject_audit_history(study_id=1, subject_id=999))


class _SubjectAuditHistoryRepositoryStub:
    def list_import_field_history(self, *, study_id, subject_id):
        return []

    def __init__(self):
        self.status_kwargs = None
        self.event_kwargs = None
        self.period_kwargs = None

    def get_subject_context(self, *, study_id, subject_id, snapshot_class):
        return snapshot_class(
            subject_id=subject_id,
            study_id=study_id,
            study_code="NNG31",
            study_name="NNG31 Study",
            site_code="SITE01",
            screening_code="SCR-001",
            subject_code="SUBJ-001",
        )

    def list_subject_identifier_history(self, *, subject_id, record_class, limit):
        return [
            record_class(
                occurred_at=datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
                field_name="subject_identifier",
                field_description="Identifier assignment: subject_code",
                value="SUBJ-001 generated_at_enrollment",
                user_display="Nguyen CRC",
                identifier_type="subject_code",
                from_value="",
                to_value="SUBJ-001",
                assignment_source="generated_at_enrollment",
                related_randomization_event_id=None,
                actor_id=10,
            )
        ]

    def list_subject_status_history(self, *, subject_id, record_class, limit, search="", field_name=""):
        self.status_kwargs = {"search": search, "field_name": field_name}
        return [
            record_class(
                occurred_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
                field_name="subject_status",
                field_description="Subject status transition",
                value="Screening Enrolled Subject enrolled",
                user_display="Nguyen CRC",
                from_status="screening",
                to_status="enrolled",
                reason_code="eligible",
                reason_text="Subject enrolled",
                source="user",
                actor_id=10,
            )
        ]

    def list_event_instance_transition_history(
        self,
        *,
        study_id,
        subject_id,
        record_class,
        limit,
        search="",
        field_name="",
    ):
        self.event_kwargs = {"search": search, "field_name": field_name}
        return [
            record_class(
                occurred_at=datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc),
                field_name="event_transition",
                field_description="Screening -> Baseline",
                value="Completed Open All required forms submitted",
                user_display="System",
                from_event_label="Screening",
                to_event_label="Baseline",
                from_status="completed",
                to_status="open",
                trigger_source="datacapture",
                result="applied",
                reason="All required forms submitted",
                actor_id=None,
                transition_rule_id=7,
            )
        ]

    def list_period_transition_history(
        self,
        *,
        subject_id,
        record_class,
        limit,
        search="",
        field_name="",
    ):
        self.period_kwargs = {"search": search, "field_name": field_name}
        return [
            record_class(
                occurred_at=datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc),
                field_name="period_status",
                field_description="Period 2 / EPREX_4000U",
                value=(
                    "planned active manual_period_transition_override "
                    "manual_period_override"
                ),
                user_display="Nguyen CRC",
                period_no=2,
                treatment_code="EPREX_4000U",
                from_status="planned",
                to_status="active",
                trigger_source="manual_period_override",
                reason="manual_period_transition_override",
                actor_id=10,
                source_event_label="Visit 8",
                facts_json=(
                    '{"override_id": 71, "pending_form_count": 2, '
                    '"pending_data_acknowledged": true}'
                ),
                override_reason_code="source_document_issue",
                override_reason_text="Tài liệu nguồn cần hiệu chỉnh",
            )
        ]


class _MissingSubjectAuditHistoryRepositoryStub(_SubjectAuditHistoryRepositoryStub):
    def get_subject_context(self, *, study_id, subject_id, snapshot_class):
        return None


class _PageStateHistoryReaderStub:
    def __init__(self):
        self.kwargs = None

    def __call__(self, *, subject_id, limit, search="", field_name=""):
        self.kwargs = {"search": search, "field_name": field_name}
        return [
            {
                "occurred_at": datetime(2026, 6, 3, 9, 0, tzinfo=timezone.utc),
                "category": "page_state",
                "source": "Page State",
                "field_name": "page_state_status",
                "field_description": "Baseline / VITALS",
                "value": "Draft Submitted",
                "user_display": "User #11",
                "scope": "Baseline / VITALS",
                "action": "Page state transition",
                "from_value": "Draft",
                "to_value": "Submitted",
                "actor": "User #11",
                "reason": "",
                "details": [{"label": "Trigger Source", "value": "User"}],
            }
        ]


class _EventGateHistoryReaderStub:
    def __init__(self):
        self.kwargs = None

    def __call__(self, *, study_id, subject_id, limit, search="", field_name=""):
        self.kwargs = {"search": search, "field_name": field_name}
        return [
            {
                "occurred_at": datetime(2026, 6, 4, 9, 0, tzinfo=timezone.utc),
                "category": "event_gate",
                "source": "Event Gate",
                "field_name": "baseline_ready",
                "field_description": "Baseline / Open Event",
                "value": "Fail Missing baseline facts",
                "user_display": "System",
                "scope": "Baseline",
                "action": "Open Event",
                "from_value": "Transition",
                "to_value": "Fail",
                "actor": "System",
                "reason": "Missing baseline facts",
                "details": [{"label": "Gate Code", "value": "baseline_ready"}],
            }
        ]
