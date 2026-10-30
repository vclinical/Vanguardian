from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase

from apps.datacapture.application.services.acknowledgement_validation import get_current_validation_failures_for_acknowledgement
from apps.subject.presentation.web.views.verification_verify_checked import SubjectValidationIssueAcknowledgeView


class AcknowledgementValidationTests(SimpleTestCase):
    def test_fresh_ack_evaluates_latest_persisted_entry_and_all_rule_modes(self):
        repository = Mock()
        repository.get_current_entry.return_value = SimpleNamespace(data='{"QT": "405"}')
        with patch("apps.datacapture.application.services.acknowledgement_validation.DataCaptureFieldValidationRulesService") as evaluator:
            expected = (SimpleNamespace(rule_id=1648),)
            evaluator.return_value.check_field_validation_rules.return_value = SimpleNamespace(failures=expected)
            result = get_current_validation_failures_for_acknowledgement(subject_id=31, visit_id=473,
                crf_template_id=361, event_form_binding_id=639, repository=repository)
            self.assertEqual(result, expected)
            evaluator.return_value.check_field_validation_rules.assert_called_once_with(crf_template_id=361,
                payload_data='{"QT": "405"}', subject_id=31, visit_id=473, include_hard=True, evaluate_all=True)
        repository.get_current_entry.return_value = None
        with self.assertRaises(ValueError):
            get_current_validation_failures_for_acknowledgement(subject_id=31, visit_id=473,
                crf_template_id=361, repository=repository)

    def test_ack_endpoint_passes_current_validation_evidence(self):
        module = "apps.subject.presentation.web.views.verification_verify_checked."
        request = RequestFactory().post('/ack/?event_form_binding_id=639', data='{}', content_type='application/json')
        request.user = SimpleNamespace(id=44)
        issues = [{"issue_id": 301, "comment": "Reviewed"}]
        failures = (SimpleNamespace(rule_id=1648),)
        with (patch(module + "SubjectFormVerificationRequestValidator.parse_validation_issue_acknowledgements", return_value=issues),
              patch(module + "get_page_state_id_for_subject_visit_crf", return_value=667),
              patch(module + "get_page_state_status_for_subject_visit_crf", return_value="submitted"),
              patch(module + "get_current_validation_failures_for_acknowledgement", return_value=failures) as evaluate,
              patch(module + "acknowledge_reconcile_validation_issues", return_value={"acknowledged_issue_ids": [301], "acknowledged_count": 1}) as acknowledge):
            response = SubjectValidationIssueAcknowledgeView().post(request, study_id=3, subject_id=31, visit_id=473, crf_template_id=361)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(evaluate.call_args.kwargs["subject_id"], 31)
        acknowledge.assert_called_once_with(page_state_id=667, issues=issues, actor_user_id=44, current_failures=failures)
