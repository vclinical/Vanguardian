from datetime import datetime, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.subject.application.services.form_field_review_table import FormFieldReviewTableService


class ValidationHistorySecondsTests(SimpleTestCase):
    def test_validation_events_in_same_minute_keep_distinct_seconds(self):
        rows = [dict(id=index, validation_issue_id=301, result='FAIL', source='SUBMIT_FOR_REVIEW',
                     created_at=datetime(2026, 10, 28, 8, 36, second, tzinfo=timezone.utc),
                     evaluated_value='405', message='Outside range') for index, second in enumerate((23, 25))]
        service = FormFieldReviewTableService()
        with patch('apps.subject.application.services.form_field_review_table.get_username_display_for_user_id', return_value='Tester'):
            history = service._format_validation_issue_histories(rows, control_norm='number', label_by_value={})
        times = [message['opened_at'] for message in history[0]['messages']]
        self.assertTrue(times[0].endswith('15:36:23'), times)
        self.assertTrue(times[1].endswith('15:36:25'), times)
        self.assertNotIn(':23', service._format_datetime(rows[0]['created_at']))
