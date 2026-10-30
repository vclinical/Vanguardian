from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.subject.application.services.form_field_review_table import FormFieldReviewTableService


class ValidationIssueHistoryGroupingTests(SimpleTestCase):
    def format(self, rows):
        with patch('apps.subject.application.services.form_field_review_table.get_username_display_for_user_id', return_value='Tester'):
            return FormFieldReviewTableService()._format_validation_issue_histories(
                rows, control_norm='number', label_by_value={})

    def test_one_issue_has_three_chronological_events_and_ack_is_not_pass(self):
        start = datetime(2026, 10, 28, 8, 27, tzinfo=timezone.utc)
        rows = [dict(id=551, validation_issue_id=301, result='FAIL', source='SUBMIT_FOR_REVIEW',
                     evaluated_value='405', message='Outside range', created_at=start, triggered_by=31),
                dict(id=674, validation_issue_id=301, result='PASS', source='VALIDATION_ISSUE_ACKNOWLEDGEMENT',
                     evaluated_value='405', message='Clinically insignificant', created_at=start + timedelta(minutes=9), triggered_by=31),
                dict(id=675, validation_issue_id=301, result='FAIL', source='SUBMIT_FOR_REVIEW',
                     evaluated_value='405', message='Outside range', created_at=start + timedelta(minutes=9, seconds=2), triggered_by=31)]
        histories = self.format(list(reversed(rows)))
        self.assertEqual(len(histories), 1)
        self.assertEqual(histories[0]['label'], 'Validation Issue #301')
        self.assertEqual(histories[0]['status'], 'FAIL')
        messages = histories[0]['messages']
        self.assertEqual([message['status'] for message in messages], ['FAIL', 'Acknowledged', 'FAIL'])
        self.assertEqual([message['text'] for message in messages], ['Outside range', 'Clinically insignificant', 'Outside range'])
        self.assertEqual([message['value_snapshot'] for message in messages], ['405'] * 3)
        self.assertTrue(all(message['opened_by'] == 'Tester' for message in messages))

    def test_actual_pass_remains_pass_and_history_over_ten_events_is_not_truncated(self):
        start = datetime(2026, 10, 28, tzinfo=timezone.utc)
        rows = [dict(id=index, validation_issue_id=301, result='FAIL' if index < 12 else 'PASS',
                     source='SUBMIT_FOR_REVIEW', evaluated_value='405' if index < 12 else '380',
                     created_at=start + timedelta(seconds=index), message='Rule checked') for index in range(1, 13)]
        rows.append(dict(id=20, validation_issue_id=302, result='PASS', source='SUBMIT_FOR_REVIEW',
                         evaluated_value='380', created_at=start + timedelta(minutes=1), message='Other rule'))
        histories = self.format(rows)
        self.assertEqual([item['label'] for item in histories], ['Validation Issue #302', 'Validation Issue #301'])
        self.assertEqual(len(histories[1]['messages']), 12)
        self.assertEqual(histories[1]['messages'][-1]['status'], 'PASS')
        self.assertEqual(histories[1]['messages'][-1]['value_snapshot'], '380')

    def test_preserved_acknowledgement_is_distinct_from_actual_validation_pass(self):
        history = self.format([dict(id=800, validation_issue_id=301, result='FAIL',
            source='SUBMIT_FOR_REVIEW', evaluated_value='405', acknowledgment_preserved=True,
            created_at=datetime(2026, 10, 30, tzinfo=timezone.utc), message='Outside range')])[0]
        self.assertEqual(history['status'], 'Acknowledgement retained')
        self.assertEqual(history['messages'][0]['tone'], 'resolved')
        self.assertNotEqual(history['messages'][0]['status'], 'PASS')

    def test_snapshot_reader_exposes_source_and_unwraps_evaluation_context(self):
        from apps.reconcile.infrastructure.repositories.dataquery_read import DjangoReconcileDataQueryReadRepository

        row = dict(id=800, validation_issue_id=301, validation_issue__rule_id=1648,
                   validation_issue__field_instance_id=4699, validation_issue__field_instance__field_template_id=2481,
                   validation_issue__rule__field_template_id=2481, validation_issue__mode='SOFT', result='FAIL',
                   evaluated_values_json={'_validation_context': 'digest', 'value': '405', 'acknowledged': True},
                   validation_run_id=1000, validation_run__source='SUBMIT_FOR_REVIEW', validation_run__triggered_by=31,
                   severity='MAJOR', message='Outside range', created_at=None)
        with patch('apps.reconcile.infrastructure.repositories.dataquery_read.ReconcileValidationIssueSnapshot') as model:
            model.objects.filter.return_value.filter.return_value.order_by.return_value.values.return_value = [row]
            result = DjangoReconcileDataQueryReadRepository().list_validation_issue_histories_by_page_state_and_field_templates(
                page_state_id=667, field_template_ids=(2481,))[2481][0]
        self.assertEqual(result['evaluated_value'], '405')
        self.assertEqual(result['source'], 'SUBMIT_FOR_REVIEW')
        self.assertEqual(result['triggered_by'], 31)
        self.assertIs(result['acknowledgment_preserved'], True)
