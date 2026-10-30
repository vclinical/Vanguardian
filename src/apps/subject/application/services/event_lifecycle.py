from datetime import timedelta

from django.db import transaction

from apps.subject.application.commands.trigger_event_transition import (
    SubjectEventInstanceNotFoundError,
    TriggerSubjectEventTransitionCommand,
)
from apps.subject.application.services.workflow_action import SubjectWorkflowActionService
from apps.subject.domain import (
    SubjectEventInstance,
    SubjectEventTransitionApplied,
    SubjectEventTransitionPolicy,
    SubjectEventTransitionResult,
)
from apps.subject.infrastructure.repositories import DjangoSubjectEventLifecycleRepository


class NoopSubjectEventPublisher:
    def publish_many(self, events):
        return None


class StudyEventGateEvaluationRecorder:
    def record(self, command):
        from apps.study.public import record_event_gate_evaluation

        return record_event_gate_evaluation(command)


class StudyRandomizationTransitionFactReader:
    def build_facts(self, *, study_id: int):
        from apps.study.public import build_randomization_transition_facts

        return build_randomization_transition_facts(study_id=study_id)


class StudyEligibilityTransitionFactReader:
    def build_facts(self, *, study_id: int, subject_id: int):
        from apps.study.public import build_eligibility_transition_facts

        return build_eligibility_transition_facts(study_id=study_id, subject_id=subject_id)


class SubjectEventTransitionService:
    repository_class = DjangoSubjectEventLifecycleRepository
    transition_policy_class = SubjectEventTransitionPolicy
    event_publisher_class = NoopSubjectEventPublisher
    workflow_action_service_class = SubjectWorkflowActionService
    gate_evaluation_recorder_class = StudyEventGateEvaluationRecorder
    randomization_fact_reader_class = StudyRandomizationTransitionFactReader
    eligibility_fact_reader_class = StudyEligibilityTransitionFactReader

    def __init__(
        self,
        repository=None,
        transition_policy=None,
        event_publisher=None,
        workflow_action_service=None,
        gate_evaluation_recorder=None,
        randomization_fact_reader=None,
        eligibility_fact_reader=None,
        source_event_certification_checker=None,
    ):
        self.repository = repository or self.repository_class()
        self.transition_policy = transition_policy or self.transition_policy_class()
        self.event_publisher = event_publisher or self.event_publisher_class()
        self.workflow_action_service = workflow_action_service or self.workflow_action_service_class()
        self.gate_evaluation_recorder = gate_evaluation_recorder or self.gate_evaluation_recorder_class()
        self.randomization_fact_reader = randomization_fact_reader or self.randomization_fact_reader_class()
        self.eligibility_fact_reader = eligibility_fact_reader or self.eligibility_fact_reader_class()
        self.source_event_certification_checker = (
            source_event_certification_checker or self._default_source_event_certification_checker
        )

    def execute(
        self,
        command: TriggerSubjectEventTransitionCommand,
    ) -> SubjectEventTransitionResult:
        with transaction.atomic():
            source_event = self.repository.get_event_instance_for_update(
                event_instance_id=command.source_event_instance_id,
            )
            if source_event is None:
                raise SubjectEventInstanceNotFoundError(command.source_event_instance_id)

            transition_rules = self.repository.list_enabled_transition_rules_from(
                study_id=source_event.study_id,
                study_version=source_event.study_version,
                from_event_definition_id=source_event.event_definition_id,
            )
            if command.target_event_definition_id is not None:
                transition_rules = [
                    transition_rule
                    for transition_rule in transition_rules
                    if transition_rule.to_event_definition_id == command.target_event_definition_id
                ]
            target_guard = getattr(
                self.repository,
                "is_transition_target_allowed",
                None,
            )
            if target_guard is not None:
                transition_rules = [
                    transition_rule
                    for transition_rule in transition_rules
                    if target_guard(
                        subject_id=source_event.subject_id,
                        event_definition_id=transition_rule.to_event_definition_id,
                    )
                ]
            if not transition_rules:
                return SubjectEventTransitionResult(
                    source_event_instance_id=source_event.id,
                )

            target_events_by_definition = self.repository.list_event_instances_for_update(
                subject_id=source_event.subject_id,
                event_definition_ids=[
                    transition_rule.to_event_definition_id
                    for transition_rule in transition_rules
                ],
            )
            facts = self._build_transition_facts(
                source_event=source_event,
                external_facts=command.facts,
                trigger_source=command.trigger_source,
            )
            facts.update(
                self._build_scoped_transition_facts(
                    source_event=source_event,
                    transition_rules=transition_rules,
                )
            )
            decisions = self.transition_policy.decide(
                source_event=source_event,
                transition_rules=transition_rules,
                target_events_by_definition=target_events_by_definition,
                facts=facts,
            )
            rule_by_id = {transition_rule.id: transition_rule for transition_rule in transition_rules}
            self._record_rule_gate_evaluations(
                source_event=source_event,
                decisions=decisions,
                rule_by_id=rule_by_id,
                facts=facts,
                actor_user_id=command.actor_user_id,
                trigger_source=command.trigger_source,
            )
            now = self.repository.now()
            applied_events: list[SubjectEventTransitionApplied] = []
            skipped_decisions = []

            for decision in decisions:
                if not decision.should_open and not decision.should_create:
                    self._retry_open_workflow_action(
                        decision=decision,
                        rule=rule_by_id[decision.rule_id],
                        source_event=source_event,
                        target_events_by_definition=target_events_by_definition,
                        actor_user_id=command.actor_user_id,
                    )
                    skipped_decisions.append(decision)
                    continue

                rule = rule_by_id[decision.rule_id]
                target_event = target_events_by_definition.get(decision.target_event_definition_id)
                if decision.should_create:
                    event_definition = self.repository.get_event_definition(
                        event_definition_id=decision.target_event_definition_id,
                    )
                    if event_definition is None:
                        skipped_decisions.append(decision)
                        continue
                    target_event = self.repository.create_open_event_instance(
                        subject_id=source_event.subject_id,
                        study_id=source_event.study_id,
                        event_definition=event_definition,
                        actor_user_id=command.actor_user_id,
                        now=now,
                        planned_date=self._resolve_planned_date(
                            offset_days=rule.offset_days,
                            anchor_datetime=now,
                        ),
                    )
                else:
                    opened_event = self.repository.open_event_instance(
                        event_instance_id=target_event.id,
                        actor_user_id=command.actor_user_id,
                        now=now,
                    )
                    if opened_event is None:
                        skipped_decisions.append(decision)
                        continue
                    target_event = opened_event

                applied_events.append(
                    SubjectEventTransitionApplied(
                        subject_id=source_event.subject_id,
                        source_event_instance_id=source_event.id,
                        target_event_instance_id=target_event.id,
                        rule_id=rule.id,
                        from_status=source_event.status,
                        to_status=SubjectEventInstance.OPEN,
                        event_name=target_event.event_name,
                        facts=facts,
                    )
                )
                self.repository.record_transition_log(
                    study_id=source_event.study_id,
                    subject_id=source_event.subject_id,
                    source_event_instance_id=source_event.id,
                    target_event_instance_id=target_event.id,
                    transition_rule_id=rule.id,
                    from_event_definition_id=rule.from_event_definition_id,
                    to_event_definition_id=rule.to_event_definition_id,
                    from_status=source_event.status,
                    to_status=SubjectEventInstance.OPEN,
                    trigger_source=command.trigger_source,
                    result="applied",
                    reason=decision.reason,
                    facts=facts,
                    actor_user_id=command.actor_user_id,
                    now=now,
                )
                if rule.auto_execute:
                    self.workflow_action_service.execute_for_open_event(
                        event_instance_id=target_event.id,
                        actor_user_id=command.actor_user_id,
                        source_event_instance_id=source_event.id,
                        automatic=True,
                    )

            result = SubjectEventTransitionResult(
                source_event_instance_id=source_event.id,
                applied_events=tuple(applied_events),
                skipped_decisions=tuple(skipped_decisions),
            )
            self.event_publisher.publish_many(result.applied_events)
            return result

    def _retry_open_workflow_action(
        self,
        *,
        decision,
        rule,
        source_event,
        target_events_by_definition,
        actor_user_id,
    ) -> None:
        if not rule.auto_execute or decision.reason != "target_event_not_openable":
            return
        target_event = target_events_by_definition.get(decision.target_event_definition_id)
        if target_event is None:
            return
        target_status = str(getattr(target_event, "status", "") or "").strip().lower()
        failed_eligibility_retry = (
            target_status == SubjectEventInstance.COMPLETED
            and str(getattr(target_event, "event_code", "") or "").strip().lower() == "eligibility_assessment"
        )
        if target_status != SubjectEventInstance.OPEN and not failed_eligibility_retry:
            return
        self.workflow_action_service.execute_for_open_event(
            event_instance_id=target_event.id,
            actor_user_id=actor_user_id,
            source_event_instance_id=source_event.id,
            automatic=True,
        )

    @staticmethod
    def _default_source_event_certification_checker(*, event_instance_id: int) -> bool:
        from apps.datacapture.public import has_current_event_certification_attestation

        return has_current_event_certification_attestation(event_instance_id=event_instance_id)

    def _build_transition_facts(self, *, source_event, external_facts, trigger_source=None):
        facts = {
            "subject_event.triggered": True,
            "subject_event.completed": SubjectEventInstance.is_transition_ready(source_event.status),
            f"subject.{source_event.subject_id}.event.{source_event.event_definition_id}.completed": (
                SubjectEventInstance.is_transition_ready(source_event.status)
            ),
            f"event.{source_event.event_definition_id}.completed": (
                SubjectEventInstance.is_transition_ready(source_event.status)
            ),
            f"event.{source_event.event_definition_id}.status.{source_event.status}": True,
            f"source_event.status.{source_event.status}": True,
        }
        if trigger_source:
            facts[f"trigger_source.{trigger_source}"] = True
        facts.update(self._source_event_certification_facts(source_event=source_event))
        facts.update(external_facts or {})
        return facts

    def _source_event_certification_facts(self, *, source_event) -> dict[str, bool]:
        event_code = str(getattr(source_event, "event_code", "") or "").strip().lower()
        is_certified = bool(self.source_event_certification_checker(event_instance_id=source_event.id))
        facts = {"source_event.certified": is_certified}
        if event_code:
            facts[f"{event_code}.event_certified"] = is_certified
        return facts

    def _build_scoped_transition_facts(self, *, source_event, transition_rules):
        scopes = {
            str(getattr(transition_rule, "condition_scope", "") or "").strip().lower()
            for transition_rule in transition_rules
        }
        if "randomization" not in scopes:
            if "eligibility" not in scopes:
                return {}
            return self.eligibility_fact_reader.build_facts(
                study_id=source_event.study_id,
                subject_id=source_event.subject_id,
            )
        facts = self.eligibility_fact_reader.build_facts(
            study_id=source_event.study_id,
            subject_id=source_event.subject_id,
        )
        facts.update(self.randomization_fact_reader.build_facts(study_id=source_event.study_id))
        return facts

    @staticmethod
    def _resolve_planned_date(*, offset_days, anchor_datetime):
        if offset_days is None:
            return None
        return anchor_datetime + timedelta(days=offset_days)

    def _record_rule_gate_evaluations(
        self,
        *,
        source_event,
        decisions,
        rule_by_id,
        facts,
        actor_user_id,
        trigger_source,
    ) -> None:
        from apps.study.public import RecordEventGateEvaluationCommand

        for decision in decisions:
            rule = rule_by_id.get(decision.rule_id)
            if rule is None:
                continue
            passed = decision.reason == "allowed"
            failed_conditions = [] if passed else [self._build_failed_transition_condition(decision=decision, rule=rule)]
            self.gate_evaluation_recorder.record(
                RecordEventGateEvaluationCommand(
                    study_id=source_event.study_id,
                    subject_id=source_event.subject_id,
                    event_definition_id=source_event.event_definition_id,
                    event_instance_id=source_event.id,
                    transition_rule_id=rule.id,
                    gate_code=f"transition_rule:{rule.id}",
                    gate_type="transition",
                    target_action=f"open_event:{rule.to_event_definition_id}",
                    result="pass" if passed else "fail",
                    evaluated_by_id=actor_user_id,
                    rule_code=str(rule.condition_code or rule.condition_definition_code or rule.id),
                    rule_version=source_event.study_version,
                    facts=facts,
                    failed_conditions=failed_conditions,
                    blocking_reasons=[] if passed else [decision.reason],
                    source_context=trigger_source or "subject_event_transition",
                    source_object_id=source_event.id,
                    condition_results=self._build_condition_results(
                        decision=decision,
                        rule=rule,
                        facts=facts,
                    ),
                )
            )

    @staticmethod
    def _build_failed_transition_condition(*, decision, rule):
        return {
            "transition_rule_id": rule.id,
            "from_event_definition_id": rule.from_event_definition_id,
            "to_event_definition_id": rule.to_event_definition_id,
            "condition_code": rule.condition_code or rule.condition_definition_code,
            "reason_code": decision.reason,
            "reason_message": f"Transition rule {rule.id} was not applied: {decision.reason}.",
        }

    @staticmethod
    def _build_condition_results(*, decision, rule, facts):
        condition_code = (rule.condition_code or rule.condition_definition_code or "").strip()
        passed = decision.reason == "allowed"
        condition_results = []
        if condition_code:
            actual_value = facts.get(condition_code)
            condition_results.append(
                {
                    "condition_order": 1,
                    "fact_key": condition_code,
                    "source_context": "subject_event_transition",
                    "source_object_type": "transition_rule",
                    "source_object_id": rule.id,
                    "operator": "truthy",
                    "expected_value": "true",
                    "actual_value": str(actual_value),
                    "value_type": "boolean",
                    "result": "pass" if passed else "fail",
                    "reason_code": None if passed else decision.reason,
                    "reason_message": None if passed else f"Fact {condition_code!r} did not satisfy transition rule.",
                }
            )

        condition_results.append(
            {
                "condition_order": len(condition_results) + 1,
                "fact_key": condition_code or f"transition_rule:{rule.id}",
                "source_context": "subject_event_transition",
                "source_object_type": "transition_rule",
                "source_object_id": rule.id,
                "operator": "evaluate_rule",
                "expected_value": "allowed",
                "actual_value": decision.reason,
                "value_type": "string",
                "result": "pass" if passed else "fail",
                "reason_code": None if passed else decision.reason,
                "reason_message": (
                    f"Transition rule {rule.id} evaluated as {decision.reason}."
                    if passed
                    else f"Transition rule {rule.id} was not applied: {decision.reason}."
                ),
            }
        )
        return condition_results


__all__ = [
    "NoopSubjectEventPublisher",
    "StudyEligibilityTransitionFactReader",
    "StudyEventGateEvaluationRecorder",
    "StudyRandomizationTransitionFactReader",
    "SubjectEventTransitionService",
]
