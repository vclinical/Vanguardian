from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.study.application.commands import FinalizeEligibilityAssessmentCommand
from apps.study.application.services.eligibility_assessment import EligibilityAssessmentService
from apps.subject.application.services.workflow_action import SubjectWorkflowActionService
from apps.subject.infrastructure.repositories.workflow_action import SubjectEventWorkflowContext
from tests.study.test_eligibility_assessment_service import _FakeAudit, _FakeRepository, _FakeSubjectWorkflow
from tests.subject.test_workflow_action import _TransitionServiceStub, _WorkflowRepositoryStub


class FailedEligibilityAssessmentRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.repository = _FakeRepository()
        self.subject_workflow = _FakeSubjectWorkflow()
        self.audit = _FakeAudit()
        self.snapshot = SimpleNamespace(
            page_state_id=779,
            page_entry_id=611,
            event_instance_id=1357,
            source_data_version=2,
            source_data_hash="failed-source-hash",
            blocking_queries_open=False,
            facts={"screening.eligibility_conclusion": False},
        )
        self.reader = Mock()
        self.reader.read_for_page_state.side_effect = lambda **kwargs: self.snapshot
        self.service = EligibilityAssessmentService(
            repository=self.repository,
            subject_workflow_adapter=self.subject_workflow,
            audit_context_adapter=self.audit,
            fact_reader=self.reader,
            transaction_context=nullcontext,
        )
        self.command = FinalizeEligibilityAssessmentCommand(
            study_id=3,
            site_id=3,
            subject_id=65,
            event_instance_id=1358,
            assessment_type="SCREENING",
            source_context="datacapture",
            source_object_type="PAGE_STATE",
            source_object_id=779,
            source_page_state_id=779,
            study_version="v1.0",
            actor_id=99,
        )
        self.service.finalize(self.command)
        self.failed_assessment = self.repository.assessments[0]
        self.audit.events.clear()
        self.subject_workflow.status_transitions.clear()

    def correct_source(self, *, eligible=True):
        self.snapshot = SimpleNamespace(
            **{
                **vars(self.snapshot),
                "page_entry_id": 1622,
                "source_data_version": 3,
                "source_data_hash": "corrected-source-hash",
                "facts": {"screening.eligibility_conclusion": eligible},
            }
        )

    def assert_unchanged(self):
        self.assertEqual(len(self.repository.assessments), 1)
        self.assertTrue(self.failed_assessment.is_current)
        self.assertEqual(self.subject_workflow.status_transitions, [])
        self.assertEqual(self.audit.events, [])

    def test_corrected_pass_supersedes_failed_result_and_preserves_failure_history(self):
        self.correct_source()

        result = self.service.finalize(self.command, recover_failed_only=True)

        self.assertEqual(result.result, "ELIGIBLE")
        self.assertEqual(len(self.repository.assessments), 2)
        self.assertFalse(self.failed_assessment.is_current)
        self.assertEqual(self.failed_assessment.assessment_status, "SUPERSEDED")
        self.assertEqual(self.failed_assessment.result, "NOT_ELIGIBLE")
        self.assertEqual(self.failed_assessment.source_page_entry_id, 611)
        current = self.repository.assessments[1]
        self.assertTrue(current.is_current)
        self.assertEqual(current.source_page_entry_id, 1622)
        self.assertEqual(current.source_data_version, 3)
        self.assertEqual(current.assessment_no, 2)
        self.assertEqual(self.subject_workflow.status_transitions[0].to_status, "Eligible")
        self.assertFalse(self.subject_workflow.status_transitions[0].is_enrolled)
        self.assertEqual(self.repository.gates[-1].result, "pass")
        self.assertTrue(self.repository.failures)
        self.assertTrue(self.audit.events)

    def test_unchanged_source_is_noop_even_if_fact_mapping_now_evaluates_pass(self):
        self.snapshot.facts = {"screening.eligibility_conclusion": True}

        self.assertIsNone(self.service.finalize(self.command, recover_failed_only=True))

        self.assert_unchanged()

    def test_corrected_source_still_failing_preserves_current_failure(self):
        self.correct_source(eligible=False)

        self.assertIsNone(self.service.finalize(self.command, recover_failed_only=True))

        self.assert_unchanged()

    def test_repeated_success_does_not_create_another_assessment(self):
        self.correct_source()
        self.service.finalize(self.command, recover_failed_only=True)
        audit_count = len(self.audit.events)

        self.assertIsNone(self.service.finalize(self.command, recover_failed_only=True))

        self.assertEqual(len(self.repository.assessments), 2)
        self.assertEqual(len(self.subject_workflow.status_transitions), 1)
        self.assertEqual(len(self.audit.events), audit_count)

    def test_enrolled_or_randomized_subject_is_not_recovered(self):
        self.correct_source()
        for flag in ("enrolled", "randomized"):
            with self.subTest(flag=flag):
                setattr(self.subject_workflow, flag, True)
                self.assertIsNone(self.service.finalize(self.command, recover_failed_only=True))
                self.assert_unchanged()
                setattr(self.subject_workflow, flag, False)

    def test_existing_pass_is_not_changed_by_recovery(self):
        self.correct_source()
        self.failed_assessment.result = "ELIGIBLE"

        self.assertIsNone(self.service.finalize(self.command, recover_failed_only=True))

        self.assert_unchanged()

    def test_different_source_or_workflow_event_cannot_replace_failure(self):
        self.correct_source()
        for changes in ({"source_object_id": 780}, {"event_instance_id": 1359}):
            with self.subTest(changes=changes):
                command = replace(self.command, **changes)
                self.assertIsNone(self.service.finalize(command, recover_failed_only=True))
                self.assert_unchanged()

    def test_forced_pass_cannot_bypass_recovery_evaluation(self):
        self.correct_source(eligible=False)

        self.assertIsNone(
            self.service.finalize(replace(self.command, force_result="ELIGIBLE"), recover_failed_only=True)
        )

        self.assert_unchanged()


class EventSourceEligibilityRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.repository = _FakeRepository()
        self.subject_workflow = _FakeSubjectWorkflow()
        self.audit = _FakeAudit()
        self.service = EligibilityAssessmentService(
            repository=self.repository,
            subject_workflow_adapter=self.subject_workflow,
            audit_context_adapter=self.audit,
            transaction_context=nullcontext,
        )
        self.command = FinalizeEligibilityAssessmentCommand(
            study_id=3,
            site_id=3,
            subject_id=65,
            event_instance_id=1358,
            assessment_type="SCREENING",
            source_context="datacapture",
            source_object_type="EVENT_INSTANCE",
            source_object_id=1357,
            study_version="v1.0",
            actor_id=99,
        )
        self.snapshot = SimpleNamespace(
            page_state_id=779,
            page_entry_id=611,
            event_instance_id=1357,
            source_data_version=4,
            source_data_hash="original-whole-visit-hash",
            blocking_queries_open=False,
            facts={"screening.eligibility_conclusion": False},
        )
        reader_patch = patch(
            "apps.datacapture.public.read_fact_snapshot_for_event_instance",
            side_effect=lambda **kwargs: self.snapshot,
        )
        reader_patch.start()
        self.addCleanup(reader_patch.stop)
        self.service.finalize(self.command)

    def test_changed_multiform_visit_recovers_when_reference_page_has_lower_version(self):
        self.snapshot = SimpleNamespace(
            page_state_id=780,
            page_entry_id=1623,
            event_instance_id=1357,
            source_data_version=2,
            source_data_hash="corrected-whole-visit-hash",
            blocking_queries_open=False,
            facts={"screening.eligibility_conclusion": True},
        )

        result = self.service.finalize(self.command, recover_failed_only=True)

        self.assertIsNotNone(result)
        self.assertEqual(result.result, "ELIGIBLE")
        previous, current = self.repository.assessments
        self.assertEqual(previous.assessment_status, "SUPERSEDED")
        self.assertEqual(previous.source_page_state_id, 779)
        self.assertEqual(previous.source_data_version, 4)
        self.assertEqual(current.source_object_id, 1357)
        self.assertEqual(current.source_page_state_id, 780)
        self.assertEqual(current.source_data_version, 2)
        self.assertEqual(current.source_data_hash, "corrected-whole-visit-hash")

    def test_same_event_hash_cannot_recover_even_when_reference_page_changes(self):
        self.snapshot.page_state_id = 780
        self.snapshot.page_entry_id = 1623
        self.snapshot.source_data_version = 5
        self.snapshot.facts = {"screening.eligibility_conclusion": True}

        self.assertIsNone(self.service.finalize(self.command, recover_failed_only=True))

        self.assertEqual(len(self.repository.assessments), 1)
        self.assertTrue(self.repository.assessments[0].is_current)
        self.assertEqual(self.repository.assessments[0].result, "NOT_ELIGIBLE")


class CompletedEligibilityWorkflowRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.event = SubjectEventWorkflowContext(
            event_instance_id=1358,
            study_id=3,
            subject_id=65,
            site_id=3,
            study_version="v1.0",
            status="completed",
            event_definition_id=53,
            event_code="ELIGIBILITY_ASSESSMENT",
            event_type="operational",
            event_category="screening",
            execution_mode="workflow_action",
        )
        self.repository = _WorkflowRepositoryStub(event=self.event, resolved_source_event_instance_id=1357)
        self.recoverer = Mock(
            return_value=SimpleNamespace(
                assessment_id=24,
                result="ELIGIBLE",
                assessment_status="FINAL",
                is_current=True,
            )
        )
        self.finalizer = Mock()
        self.transition = _TransitionServiceStub()

    def execute(self, *, certified=True):
        with patch("apps.subject.application.services.workflow_action.transaction.atomic", return_value=nullcontext()):
            return SubjectWorkflowActionService(
                repository=self.repository,
                source_page_state_resolver=lambda **kwargs: 779,
                source_event_certification_checker=lambda **kwargs: certified,
                eligibility_assessment_finalizer=self.finalizer,
                eligibility_assessment_recoverer=self.recoverer,
                transition_service=self.transition,
            ).execute_for_open_event(event_instance_id=1358, actor_user_id=99)

    def test_completed_failed_workflow_can_continue_after_certified_correction(self):
        result = self.execute()

        self.assertTrue(result.executed)
        self.recoverer.assert_called_once()
        self.finalizer.assert_not_called()
        command = self.recoverer.call_args.args[0]
        self.assertEqual(command.subject_id, 65)
        self.assertEqual(command.event_instance_id, 1358)
        self.assertEqual(command.source_object_id, 1357)
        self.assertEqual(len(self.transition.commands), 1)
        self.assertTrue(self.transition.commands[0].facts["eligible"])
        self.assertEqual(self.repository.completed_events, [])

    def test_uncertified_correction_cannot_recover_or_open_downstream(self):
        result = self.execute(certified=False)

        self.assertFalse(result.executed)
        self.assertEqual(result.reason, "eligibility_source_event_certification_required")
        self.recoverer.assert_not_called()
        self.finalizer.assert_not_called()
        self.assertEqual(self.transition.commands, [])

    def test_unchanged_or_still_failing_assessment_does_not_open_downstream(self):
        self.recoverer.return_value = None

        result = self.execute()

        self.assertFalse(result.executed)
        self.assertEqual(result.reason, "eligibility_reassessment_not_required")
        self.assertEqual(self.transition.commands, [])
        self.assertEqual(self.repository.completed_events, [])

    def test_completed_enrollment_and_randomization_are_not_replayed(self):
        for code in ("ENROLLMENT", "RANDOMIZATION"):
            with self.subTest(code=code):
                self.repository.event = replace(self.event, event_code=code)
                result = self.execute()
                self.assertFalse(result.executed)
                self.recoverer.assert_not_called()
                self.finalizer.assert_not_called()
                self.assertEqual(self.transition.commands, [])
