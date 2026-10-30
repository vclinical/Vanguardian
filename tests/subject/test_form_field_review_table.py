from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.form_data_document import REPEAT_COUNTS_EXPORT_META_KEY
from apps.subject.application.services.form_field_review_table import FormFieldReviewTableService


class _NoQueryReadService:
    def count_open_queries_by_page_state_and_field_templates(self, **kwargs):
        return {}

    def list_field_template_ids_with_verified_queries(self, **kwargs):
        return set()

    def list_latest_active_query_ids_by_page_state_and_field_templates(self, **kwargs):
        return {}

    def list_latest_active_query_participants_by_page_state_and_field_templates(self, **kwargs):
        return {}

    def list_latest_active_query_answered_flags_by_page_state_and_field_templates(self, **kwargs):
        return {}

    def count_query_threads_since_current_user_last_comment(self, **kwargs):
        return {}

    def list_latest_query_messages_by_page_state_and_field_templates(self, **kwargs):
        return {}

    def list_closed_query_histories_by_page_state_and_field_templates(self, **kwargs):
        return {}

    def list_open_validation_issues_by_page_state_and_field_templates(self, **kwargs):
        return {}

    def list_validation_issue_histories_by_page_state_and_field_templates(self, **kwargs):
        return {}

    def count_open_queries_by_page_state_field_paths(self, **kwargs):
        return {}

    def list_latest_active_query_contexts_by_page_state_and_field_templates(self, **kwargs):
        return {}

    def list_verified_query_keys_by_page_state_and_field_templates(self, **kwargs):
        return set()

    def list_latest_query_messages_by_dataquery_ids(self, **kwargs):
        return {}

    def count_query_threads_since_current_user_last_comment_by_dataquery_ids(self, **kwargs):
        return {}

    def list_closed_query_histories_by_page_state_field_paths(self, **kwargs):
        return {}


class FormFieldReviewTableServiceTests(SimpleTestCase):
    def test_radio_display_value_uses_static_option_label(self):
        result = self._build_review_rows(
            field_templates_payload=[
                {
                    "id": 11,
                    "field_key": "sex",
                    "label": "Sex",
                    "ui_config": {
                        "control_type": "radio",
                        "options": {
                            "source": "static",
                            "static": [
                                {"value": "M", "label": "Male"},
                                {"value": "F", "label": "Female"},
                            ],
                        },
                    },
                },
            ],
            entry_payload={"sex": "F"},
        )

        self.assertEqual(result[0]["display_value"], "Female")

    def test_checkbox_display_value_uses_static_option_labels(self):
        result = self._build_review_rows(
            field_templates_payload=[
                {
                    "id": 12,
                    "field_key": "symptoms",
                    "label": "Symptoms",
                    "ui_config": {
                        "control_type": "checkbox",
                        "options": {
                            "source": "static",
                            "static": [
                                {"value": "headache", "label": "Headache"},
                                {"value": "nausea", "label": "Nausea"},
                            ],
                        },
                    },
                },
            ],
            entry_payload={"symptoms": ["headache", "nausea"]},
        )

        self.assertEqual(result[0]["display_value"], "Headache, Nausea")

    def test_blank_non_repeatable_field_still_renders_for_review(self):
        result = self._build_review_rows(
            field_templates_payload=[
                {
                    "id": 13,
                    "field_key": "COMMENT",
                    "label": "Comment",
                    "ui_config": {"control_type": "text"},
                },
            ],
            entry_payload={},
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["field_key"], "COMMENT")
        self.assertEqual(result[0]["display_value"], "—")

    def test_repeatable_section_expands_review_rows_for_group_items(self):
        result = self._build_review_rows(
            field_templates_payload=[
                {
                    "id": 21,
                    "field_key": "MED_NAME",
                    "label": "Medication",
                    "display_order": 1,
                    "section_template": {
                        "id": 3,
                        "code": "MEDS",
                        "name": "Medication History",
                        "display_order": 1,
                        "is_repeatable": True,
                        "max_repeats": 3,
                    },
                },
                {
                    "id": 22,
                    "field_key": "MED_REASON",
                    "label": "Reason",
                    "display_order": 2,
                    "section_template": {
                        "id": 3,
                        "code": "MEDS",
                        "name": "Medication History",
                        "display_order": 1,
                        "is_repeatable": True,
                        "max_repeats": 3,
                    },
                },
            ],
            entry_payload={
                "MED_NAME": "Aspirin",
                "MED_REASON": "Fever",
                "MED_NAME__repeat_2": "Paracetamol",
                "MED_REASON__repeat_2": "Headache",
            },
        )

        self.assertEqual([row["field_key"] for row in result], [
            "MED_NAME",
            "MED_REASON",
            "MED_NAME__repeat_2",
            "MED_REASON__repeat_2",
        ])
        self.assertEqual(result[2]["display_value"], "Paracetamol")
        self.assertEqual(result[3]["display_value"], "Headache")
        self.assertEqual(result[2]["section_title"], "Medication History")
        self.assertEqual(result[2]["group_item_label"], "2")
        self.assertIs(result[2]["is_repeatable_group_item"], True)

    def test_repeatable_section_reads_repeated_composite_date_parts(self):
        result = self._build_review_rows(
            field_templates_payload=[
                {
                    "id": 31,
                    "field_key": "AESTDTC",
                    "label": "Start date",
                    "section_template": {
                        "code": "AE",
                        "name": "Adverse Events",
                        "is_repeatable": True,
                    },
                },
            ],
            entry_payload={
                "AESTDTC__repeat_2__day": "22",
                "AESTDTC__repeat_2__month": "5",
                "AESTDTC__repeat_2__year": "2026",
            },
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(
            result[1]["raw_value"],
            {"__day": "22", "__month": "5", "__year": "2026", "__time": None},
        )
        self.assertIn("2026", result[1]["display_value"])

    def test_repeatable_section_keeps_blank_fields_from_canonical_repeat_count(self):
        result = self._build_review_rows(
            field_templates_payload=[
                {
                    "id": 21,
                    "field_key": "MED_NAME",
                    "label": "Medication",
                    "display_order": 1,
                    "section_template": {
                        "id": 3,
                        "code": "MEDS",
                        "name": "Medication History",
                        "display_order": 1,
                        "is_repeatable": True,
                    },
                },
                {
                    "id": 22,
                    "field_key": "MED_REASON",
                    "label": "Reason",
                    "display_order": 2,
                    "section_template": {
                        "id": 3,
                        "code": "MEDS",
                        "name": "Medication History",
                        "display_order": 1,
                        "is_repeatable": True,
                    },
                },
            ],
            entry_payload={
                REPEAT_COUNTS_EXPORT_META_KEY: {"MEDS": 2},
                "MED_NAME": "Aspirin",
                "MED_REASON": "Fever",
                "MED_NAME__repeat_2": "Paracetamol",
            },
        )

        self.assertEqual([row["field_key"] for row in result], [
            "MED_NAME",
            "MED_REASON",
            "MED_NAME__repeat_2",
            "MED_REASON__repeat_2",
        ])
        self.assertEqual(result[3]["display_value"], "—")
        self.assertEqual(result[3]["group_item_label"], "2")

    def test_repeatable_section_does_not_render_empty_group_item(self):
        result = self._build_review_rows(
            field_templates_payload=[
                {
                    "id": 21,
                    "field_key": "MED_NAME",
                    "label": "Medication",
                    "display_order": 1,
                    "section_template": {
                        "id": 3,
                        "code": "MEDS",
                        "name": "Medication History",
                        "display_order": 1,
                        "is_repeatable": True,
                    },
                },
                {
                    "id": 22,
                    "field_key": "MED_REASON",
                    "label": "Reason",
                    "display_order": 2,
                    "section_template": {
                        "id": 3,
                        "code": "MEDS",
                        "name": "Medication History",
                        "display_order": 1,
                        "is_repeatable": True,
                    },
                },
            ],
            entry_payload={
                "MED_NAME": "",
                "MED_REASON": None,
                "MED_NAME__repeat_2": "",
                "MED_REASON__repeat_2": "",
            },
        )

        self.assertEqual(result, [])

    def test_repeatable_section_scopes_open_query_to_matching_group_item(self):
        service = FormFieldReviewTableService(reconcile_read_service=_RepeatPathQueryReadService())
        with patch(
            "apps.subject.application.services.form_field_review_table."
            "DjangoSubjectEventInstanceScheduleReadRepository.get_event_start_datetime",
            return_value=None,
        ):
            review = service.build_for_verification(
                subject_code="S-001",
                site_id=1,
                event_name="Visit 1",
                event_instance_id=1,
                form_name="Meds",
                form_status="submitted",
                entry_version="1",
                entry_updated_at=None,
                entry_updated_by_id=None,
                field_templates_payload=[
                    {
                        "id": 21,
                        "field_key": "MED_NAME",
                        "label": "Medication",
                        "section_template": {
                            "code": "MEDS",
                            "name": "Medication History",
                            "is_repeatable": True,
                        },
                    },
                ],
                entry_payload={
                    "MED_NAME": "Aspirin",
                    "MED_NAME__repeat_2": "Paracetamol",
                },
                page_state_id=23,
                current_user_id=7,
            )

        self.assertEqual(len(review["rows"]), 2)
        self.assertEqual(review["rows"][0]["field_key"], "MED_NAME")
        self.assertEqual(review["rows"][0]["open_query_count"], 1)
        self.assertEqual(review["rows"][0]["active_query_id"], 101)
        self.assertEqual(review["rows"][0]["query_messages"][0]["text"], "Check group 1")
        self.assertEqual(review["rows"][1]["field_key"], "MED_NAME__repeat_2")
        self.assertEqual(review["rows"][1]["open_query_count"], 0)
        self.assertIsNone(review["rows"][1]["active_query_id"])
        self.assertEqual(review["rows"][1]["query_messages"], [])

    def test_sets_verified_query_flag_from_reconcile_query_status(self):
        service = FormFieldReviewTableService(reconcile_read_service=_VerifiedQueryReadService())
        with patch(
            "apps.subject.application.services.form_field_review_table."
            "DjangoSubjectEventInstanceScheduleReadRepository.get_event_start_datetime",
            return_value=None,
        ):
            review = service.build_for_verification(
                subject_code="S-001",
                site_id=1,
                event_name="Visit 1",
                event_instance_id=1,
                form_name="Vitals",
                form_status="submitted",
                entry_version="1",
                entry_updated_at=None,
                entry_updated_by_id=None,
                field_templates_payload=[
                    {
                        "id": 11,
                        "field_key": "sex",
                        "label": "Sex",
                    },
                    {
                        "id": 12,
                        "field_key": "symptoms",
                        "label": "Symptoms",
                    },
                ],
                entry_payload={},
                page_state_id=23,
            )

        self.assertIs(review["rows"][0]["has_verified_query"], True)
        self.assertIs(review["rows"][1]["has_verified_query"], False)

    def test_sets_closed_query_histories_from_reconcile_query_status(self):
        service = FormFieldReviewTableService(reconcile_read_service=_ClosedQueryHistoryReadService())
        with patch(
            "apps.subject.application.services.form_field_review_table."
            "DjangoSubjectEventInstanceScheduleReadRepository.get_event_start_datetime",
            return_value=None,
        ):
            review = service.build_for_verification(
                subject_code="S-001",
                site_id=1,
                event_name="Visit 1",
                event_instance_id=1,
                form_name="Vitals",
                form_status="submitted",
                entry_version="1",
                entry_updated_at=None,
                entry_updated_by_id=None,
                field_templates_payload=[
                    {
                        "id": 11,
                        "field_key": "sex",
                        "label": "Sex",
                    },
                ],
                entry_payload={},
                page_state_id=23,
            )

        history = review["rows"][0]["closed_query_histories"][0]
        self.assertEqual(history["dataquery_id"], 201)
        self.assertEqual(history["label"], "Query #201")
        self.assertEqual(history["messages"][0]["text"], "Closed message")

    def test_sets_validation_issue_counts_and_history_messages(self):
        service = FormFieldReviewTableService(reconcile_read_service=_ValidationIssueReadService())
        with patch(
            "apps.subject.application.services.form_field_review_table."
            "DjangoSubjectEventInstanceScheduleReadRepository.get_event_start_datetime",
            return_value=None,
        ):
            review = service.build_for_verification(
                subject_code="S-001",
                site_id=1,
                event_name="Visit 1",
                event_instance_id=1,
                form_name="Vitals",
                form_status="submitted",
                entry_version="1",
                entry_updated_at=None,
                entry_updated_by_id=None,
                field_templates_payload=[
                    {
                        "id": 11,
                        "field_key": "sex",
                        "label": "Sex",
                    },
                ],
                entry_payload={},
                page_state_id=23,
            )

        row = review["rows"][0]
        self.assertEqual(row["validation_issue_count"], 1)
        self.assertEqual(row["validation_issues"][0]["message"], "Soft warning")
        self.assertEqual(row["closed_query_histories"], [])
        issue_history = row["validation_issue_histories"][0]
        self.assertEqual(issue_history["label"], "Validation Issue #701")
        self.assertEqual(issue_history["messages"][0]["text"], "Soft warning")
        self.assertEqual(len(issue_history["messages"]), 1)
        self.assertEqual(issue_history["messages"][0]["tone"], "warning")

    def test_sets_active_query_answered_flag_from_reconcile_query(self):
        service = FormFieldReviewTableService(reconcile_read_service=_AnsweredQueryReadService())
        with patch(
            "apps.subject.application.services.form_field_review_table."
            "DjangoSubjectEventInstanceScheduleReadRepository.get_event_start_datetime",
            return_value=None,
        ):
            review = service.build_for_verification(
                subject_code="S-001",
                site_id=1,
                event_name="Visit 1",
                event_instance_id=1,
                form_name="Vitals",
                form_status="submitted",
                entry_version="1",
                entry_updated_at=None,
                entry_updated_by_id=None,
                field_templates_payload=[
                    {
                        "id": 11,
                        "field_key": "sex",
                        "label": "Sex",
                    },
                ],
                entry_payload={},
                page_state_id=23,
            )

        self.assertIs(review["rows"][0]["active_query_is_answered"], True)

    def test_sets_active_query_can_respond_for_any_current_user(self):
        service = FormFieldReviewTableService(reconcile_read_service=_ParticipantQueryReadService())
        with patch(
            "apps.subject.application.services.form_field_review_table."
            "DjangoSubjectEventInstanceScheduleReadRepository.get_event_start_datetime",
            return_value=None,
        ):
            review = service.build_for_verification(
                subject_code="S-001",
                site_id=1,
                event_name="Visit 1",
                event_instance_id=1,
                form_name="Vitals",
                form_status="submitted",
                entry_version="1",
                entry_updated_at=None,
                entry_updated_by_id=None,
                field_templates_payload=[
                    {
                        "id": 11,
                        "field_key": "sex",
                        "label": "Sex",
                    },
                    {
                        "id": 12,
                        "field_key": "symptoms",
                        "label": "Symptoms",
                    },
                ],
                entry_payload={},
                page_state_id=23,
                current_user_id=7,
            )

        self.assertIs(review["rows"][0]["active_query_can_respond"], True)
        self.assertIs(review["rows"][1]["active_query_can_respond"], True)

    @staticmethod
    def _build_review_rows(*, field_templates_payload, entry_payload):
        service = FormFieldReviewTableService(reconcile_read_service=_NoQueryReadService())
        with patch(
            "apps.subject.application.services.form_field_review_table."
            "DjangoSubjectEventInstanceScheduleReadRepository.get_event_start_datetime",
            return_value=None,
        ):
            review = service.build_for_verification(
                subject_code="S-001",
                site_id=1,
                event_name="Visit 1",
                event_instance_id=1,
                form_name="Vitals",
                form_status="submitted",
                entry_version="1",
                entry_updated_at=None,
                entry_updated_by_id=None,
                field_templates_payload=field_templates_payload,
                entry_payload=entry_payload,
                page_state_id=None,
            )
        return review["rows"]


class _VerifiedQueryReadService(_NoQueryReadService):
    def list_field_template_ids_with_verified_queries(self, **kwargs):
        return {11}


class _ClosedQueryHistoryReadService(_NoQueryReadService):
    def list_closed_query_histories_by_page_state_and_field_templates(self, **kwargs):
        return {
            11: [
                {
                    "dataquery_id": 201,
                    "question_text": "Old query",
                    "opened_at": None,
                    "closed_at": None,
                    "messages": [
                        {
                            "dataquery_id": 201,
                            "text": "Closed message",
                            "status": "comment",
                            "created_at": None,
                            "opened_by_id": None,
                        },
                    ],
                },
            ],
        }


class _ValidationIssueReadService(_NoQueryReadService):
    def list_open_validation_issues_by_page_state_and_field_templates(self, **kwargs):
        return {
            11: [
                {
                    "id": 701,
                    "rule_id": 31,
                    "field_instance_id": 51,
                    "mode": "soft_warning",
                    "severity": "warning",
                    "status": "OPEN",
                    "message": "Soft warning",
                    "created_at": None,
                }
            ]
        }

    def list_validation_issue_histories_by_page_state_and_field_templates(self, **kwargs):
        return {
            11: [
                {
                    "id": 701,
                    "rule_id": 31,
                    "field_instance_id": 51,
                    "mode": "soft_warning",
                    "severity": "warning",
                    "status": "OPEN",
                    "message": "Soft warning",
                    "created_at": None,
                    "acknowledged_by": None,
                    "acknowledged_at": None,
                    "acknowledgement_comment": "",
                    "resolved_at": None,
                }
            ]
        }


class _AnsweredQueryReadService(_NoQueryReadService):
    def list_latest_active_query_ids_by_page_state_and_field_templates(self, **kwargs):
        return {11: 101}

    def list_latest_active_query_answered_flags_by_page_state_and_field_templates(self, **kwargs):
        return {11: True}


class _ParticipantQueryReadService(_NoQueryReadService):
    def list_latest_active_query_ids_by_page_state_and_field_templates(self, **kwargs):
        return {11: 101, 12: 102}

    def list_latest_active_query_participants_by_page_state_and_field_templates(self, **kwargs):
        return {
            11: {"opened_by_id": 7, "assigned_to_id": 9},
            12: {"opened_by_id": 8, "assigned_to_id": 9},
        }


class _RepeatPathQueryReadService(_NoQueryReadService):
    field_path = "groups.MEDS.rows[row_001].items.MED_NAME"

    def count_open_queries_by_page_state_field_paths(self, **kwargs):
        return {(21, self.field_path): 1}

    def list_latest_active_query_contexts_by_page_state_and_field_templates(self, **kwargs):
        return {
            (21, self.field_path): {
                "active_query_id": 101,
                "opened_by_id": 9,
                "assigned_to_id": 7,
                "active_query_is_answered": False,
            },
        }

    def list_latest_query_messages_by_dataquery_ids(self, **kwargs):
        return {
            101: [
                {
                    "dataquery_id": 101,
                    "text": "Check group 1",
                    "status": "comment",
                    "created_at": None,
                    "opened_by_id": None,
                },
            ],
        }
