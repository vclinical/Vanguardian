import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase

from apps.datacapture.application.commands.save_submit_page import SubmitPageCommand
from apps.datacapture.presentation.api.views.file_import import FileImportAPIView, FileImportApplyAPIView
from apps.subject.infrastructure.repositories.field_audit_history import DjangoSubjectFieldAuditHistoryRepository
from tests.datacapture.test_file_import_api_contract import MODULE, FileImportApiContractTests
from tests.datacapture.test_mapped_file_import import MappedImportTests


class ImportSubmitTests(SimpleTestCase):
    def setUp(self):
        self.fixture = MappedImportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_save_submit_validate_and_audit_in_order_with_reason_and_scope(self):
        f = self.fixture
        calls = []
        f.saver.save.side_effect = lambda command: calls.append('save') or f.saver.save.return_value
        f.saver.submit.side_effect = lambda command: calls.append('submit')
        f.validation.validate.side_effect = lambda **kwargs: calls.append('validate')
        f.audit.record_event.side_effect = lambda **kwargs: calls.append('audit')
        result = f.apply(f.preview(), reason='Correct laboratory result')
        self.assertEqual(calls, ['save', 'submit', 'validate', 'audit'])
        command = f.saver.submit.call_args.args[0]
        self.assertIsInstance(command, SubmitPageCommand)
        self.assertEqual((command.subject_id, command.visit_id, command.crf_template_id,
                          command.event_form_binding_id, command.actor_user_id), (10, 20, 8, 2, 7))
        self.assertEqual(json.loads(command.data), {'UNRELATED': 'keep', 'RESULT': '5'})
        self.assertEqual(command.change_reasons[0].reason, 'Correct laboratory result')
        self.assertEqual(command.change_reasons[0].field_key, 'RESULT')
        audit = f.audit.record_event.call_args.kwargs['after_data']
        self.assertEqual(audit['entry_status'], 'submitted')
        self.assertEqual(audit['submitted_entries'], result['forms'])

    def test_submit_failure_rolls_back_and_does_not_audit_success(self):
        f = self.fixture
        events = []

        @contextmanager
        def atomic(*args):
            try:
                yield
            except Exception:
                events.append('rollback')
                raise
            else:
                events.append('commit')

        f.capture.atomic_scope = atomic
        f.saver.submit.side_effect = ValueError('Submit rejected')
        with self.assertRaisesMessage(ValueError, 'Submit rejected'):
            f.apply(f.preview())
        self.assertEqual(events, ['rollback'])
        f.validation.validate.assert_not_called()
        f.audit.record_event.assert_not_called()

    def test_keep_all_does_not_submit_existing_draft(self):
        f = self.fixture
        f.snapshot['data']['RESULT'] = '8'
        f.snapshot['raw_data']['RESULT'] = '8'
        f.apply(f.preview(), field_decisions={'2:4': 'ecrf'})
        f.saver.submit.assert_not_called()

    def test_submit_permission_is_required_before_apply(self):
        f = FileImportApiContractTests()
        f.setUp()
        with patch.object(FileImportAPIView, '_scope', side_effect=[f.scope, PermissionDenied('no submit')]) as scope, patch(
            MODULE + '.inspect_import_preview', return_value={'overwrite_count': 0, 'forms': {}},
        ), patch(MODULE + '.MappedFileImportService') as service:
            response = FileImportApplyAPIView().post(f.request({'token': 'signed', 'confirmed': True}), **f.scope)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(scope.call_args.kwargs['permission'], 'CRF.SUBMIT')
        service.return_value.apply.assert_not_called()

    def test_submitted_import_has_one_value_change_and_preserves_old_draft_label(self):
        repo = DjangoSubjectFieldAuditHistoryRepository()
        now = datetime(2026, 10, 31, tzinfo=timezone.utc)
        change = dict(key='2:4', form_id=8, field_key='RESULT', status='overwrite', old_value='4.87', new_value='5.50')
        payload = dict(changes=[change], entry_status='submitted', submitted_entries=[dict(form_id=8, entry_id=99)])
        event = dict(id=1, created_at=now, created_by_id=7, created_by__display_name='Importer',
                     created_by__username='importer', after_data=json.dumps(payload))
        with patch('apps.subject.infrastructure.repositories.field_audit_history.list_file_import_events', return_value=[event]):
            rows = repo._build_file_import_rows(visit_id=20, crf_template_id=8, field_template_id=4,
                                               field_key='RESULT', event_form_binding_id=2)
            self.assertEqual(rows[0]['audit_event'], 'Item data value imported (submitted)')
            rows.append(dict(sort_key='entry:2:99', entry_id=99, changed_at=now - timedelta(seconds=1),
                             value_from='4.87', value_to='5.50'))
            merged = repo._merge_value_history(rows)
            self.assertEqual(len(merged), 1)
            self.assertEqual((merged[0]['value_from'], merged[0]['value_to']), ('4.87', '5.50'))
            event['after_data'] = json.dumps(dict(changes=[change]))
            old = repo._build_file_import_rows(visit_id=20, crf_template_id=8, field_template_id=4,
                                              field_key='RESULT', event_form_binding_id=2)
            self.assertEqual(old[0]['audit_event'], 'Item data value imported (draft)')

    def test_all_imported_forms_are_saved_then_submitted_before_cross_form_validation(self):
        f = self.fixture
        targets = f.config['confirmation']['mappings'][0]['targets']
        targets.append(dict(targets[0], binding_id=3, form_id=9))
        f.reader.catalog.return_value['bindings'].append(dict(
            id=3, form_id=9, visit_id=30, study_version='1', fields=[dict(id=4, key='RESULT')],
        ))
        calls = []
        f.saver.save.side_effect = lambda command: calls.append(('save', command.crf_template_id)) or f.saver.save.return_value
        f.saver.submit.side_effect = lambda command: calls.append(('submit', command.crf_template_id))
        f.validation.validate.side_effect = lambda **kwargs: calls.append(('validate', None))
        f.apply(f.preview())
        self.assertEqual(calls, [('save', 8), ('save', 9), ('submit', 8), ('submit', 9), ('validate', None)])
