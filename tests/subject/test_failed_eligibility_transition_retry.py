from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.subject.application.commands import TriggerSubjectEventTransitionCommand
from apps.subject.application.services.event_lifecycle import SubjectEventTransitionService
from apps.subject.domain import StudyEventTransitionRuleSnapshot, SubjectEventInstanceSnapshot
from tests.subject.test_create_subject_service import (
    _GateEvaluationRecorderStub,
    _SubjectEventLifecycleRepositoryStub,
    _WorkflowActionServiceStub,
)


class FailedEligibilityTransitionRetryTests(SimpleTestCase):
    def execute_transition(self, *, target_code="ELIGIBILITY_ASSESSMENT", certified=True, auto_execute=True):
        target = SubjectEventInstanceSnapshot(
            id=1358,
            study_id=1,
            subject_id=20,
            event_definition_id=101,
            study_version="1.0",
            repeat_index=1,
            status="completed",
            event_code=target_code,
            event_type="operational",
        )
        rule = StudyEventTransitionRuleSnapshot(
            id=3,
            from_event_definition_id=100,
            to_event_definition_id=101,
            transition_type="conditional",
            condition_scope="subject_event",
            condition_code="screening_source_ready",
            condition_definition_id=55,
            condition_definition_scope="event",
            condition_definition_code="screening_source_ready",
            condition_expression_json={
                "all": [{"fact": "screening.event_certified", "operator": "equals", "value": True}]
            },
            auto_open=True,
            auto_create=True,
            auto_execute=auto_execute,
            requires_previous_completion=True,
            allow_skip=False,
            display_order=1,
        )
        repository = _SubjectEventLifecycleRepositoryStub(
            now=datetime(2026, 10, 30, 9, 30, tzinfo=timezone.utc),
            target_event=target,
            transition_rule=rule,
        )
        workflow = _WorkflowActionServiceStub()
        with patch("apps.subject.application.services.event_lifecycle.transaction.atomic", return_value=nullcontext()):
            result = SubjectEventTransitionService(
                repository=repository,
                workflow_action_service=workflow,
                gate_evaluation_recorder=_GateEvaluationRecorderStub(),
                source_event_certification_checker=lambda **kwargs: certified,
            ).execute(
                TriggerSubjectEventTransitionCommand(
                    source_event_instance_id=1357,
                    actor_user_id=99,
                    trigger_source="datacapture_event_certification",
                )
            )
        return result, workflow

    def test_certification_reaches_completed_eligibility_recovery(self):
        result, workflow = self.execute_transition()

        self.assertFalse(result.has_changes)
        self.assertEqual(
            workflow.calls,
            [{
                "event_instance_id": 1358,
                "actor_user_id": 99,
                "source_event_instance_id": 1357,
                "automatic": True,
            }],
        )

    def test_completed_noneligibility_events_are_not_replayed(self):
        for code in ("ENROLLMENT", "RANDOMIZATION", "WASHOUT"):
            with self.subTest(code=code):
                _, workflow = self.execute_transition(target_code=code)
                self.assertEqual(workflow.calls, [])

    def test_manual_transition_rule_does_not_automatically_retry(self):
        _, workflow = self.execute_transition(auto_execute=False)

        self.assertEqual(workflow.calls, [])
