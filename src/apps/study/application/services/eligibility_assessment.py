import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from django.db import transaction

from apps.audit.public import AuditContextAdapter
from apps.core.choices import EligibilityAssessmentStatusChoices, EligibilityResultChoices
from apps.shared.constants import AuditEventActionEnum, AuditEventObjectTypeEnum
from apps.study.application.commands import (
    EligibilityAssessmentResult,
    EnrollSubjectCommand,
    FinalizeEligibilityAssessmentCommand,
    MarkEligibilityStaleOnSourceDataChangeCommand,
    RetractEligibilityAssessmentCommand,
)
from apps.study.application.exceptions import (
    EligibilityAssessmentError,
    EligibilityAssessmentNotFoundError,
    EligibilityAssessmentPermissionError,
    EligibilityAssessmentRetractBlockedError,
    EligibilityEnrollmentGateError,
)
from apps.study.infrastructure.repositories import DjangoEligibilityAssessmentRepository


@dataclass(frozen=True)
class EligibilityEvaluation:
    result: str
    facts: dict[str, Any]
    failed_conditions: list[dict[str, Any]]
    rule_code: str | None = None
    conclusion_value: str | None = None


class DataCaptureEligibilityFactReader:
    def read_for_page_state(self, *, page_state_id: int):
        from apps.datacapture.public import read_fact_snapshot_for_page_state

        return read_fact_snapshot_for_page_state(page_state_id=page_state_id)


class StudyEligibilityTransitionFactService:
    repository_class = DjangoEligibilityAssessmentRepository

    def __init__(self, repository=None):
        self.repository = repository or self.repository_class()

    def build_facts(self, *, study_id: int, subject_id: int, assessment_type: str = "SCREENING") -> dict[str, object]:
        assessment = self.repository.get_current_assessment(
            study_id=study_id,
            subject_id=subject_id,
            assessment_type=assessment_type,
        )
        if assessment is None:
            return {}
        is_final_eligible = bool(
            assessment.assessment_status == EligibilityAssessmentStatusChoices.FINAL
            and assessment.result == EligibilityResultChoices.ELIGIBLE
            and assessment.is_current
        )
        is_final_not_eligible = bool(
            assessment.assessment_status == EligibilityAssessmentStatusChoices.FINAL
            and assessment.result == EligibilityResultChoices.NOT_ELIGIBLE
            and assessment.is_current
        )
        return {
            "eligibility.latest.result": assessment.result,
            "eligibility.latest.assessment_status": assessment.assessment_status,
            "eligibility.latest.is_current": assessment.is_current,
            "eligible": is_final_eligible,
            "not_eligible": is_final_not_eligible,
        }


class EligibilityAssessmentService:
    repository_class = DjangoEligibilityAssessmentRepository
    audit_context_adapter_class = AuditContextAdapter
    fact_reader_class = DataCaptureEligibilityFactReader

    def __init__(
        self,
        *,
        repository=None,
        subject_workflow_adapter=None,
        audit_context_adapter=None,
        fact_reader=None,
        transaction_context=None,
    ):
        self.repository = repository or self.repository_class()
        self.subject_workflow_adapter = subject_workflow_adapter or self._default_subject_workflow_adapter()
        self.audit_context_adapter = audit_context_adapter or self.audit_context_adapter_class()
        self.fact_reader = fact_reader or self.fact_reader_class()
        self.transaction_context = transaction_context or transaction.atomic

    @staticmethod
    def _default_subject_workflow_adapter():
        from apps.subject.public import SubjectEligibilityWorkflowAdapter

        return SubjectEligibilityWorkflowAdapter()

    def finalize(
        self,
        command: FinalizeEligibilityAssessmentCommand,
        *,
        recover_failed_only: bool = False,
    ) -> EligibilityAssessmentResult | None:
        self._require_permission(
            command.actor_id,
            command.study_id,
            "finalize_subject_eligibility",
            site_id=command.site_id,
            bypass=command.automatic,
        )
        if command.force_result:
            self._require_permission(
                command.actor_id,
                command.study_id,
                "override_subject_eligibility",
                site_id=command.site_id,
                bypass=command.automatic,
            )

        subject_scope = self.subject_workflow_adapter.get_subject_scope(
            study_id=command.study_id,
            site_id=command.site_id,
            subject_id=command.subject_id,
        )
        fact_snapshot = self._read_fact_snapshot(command)
        facts = dict(getattr(fact_snapshot, "facts", {}) or {})
        if getattr(fact_snapshot, "blocking_queries_open", None) is not None:
            facts["screening.blocking_queries_open"] = fact_snapshot.blocking_queries_open

        evaluation = self._evaluate(command=command, facts=facts)
        now = self.repository.now()
        with self.transaction_context():
            if recover_failed_only and not self._can_recover_failed_assessment(
                command=command,
                fact_snapshot=fact_snapshot,
                evaluation=evaluation,
            ):
                return None
            superseded_assessments = self.repository.supersede_current_assessments(
                subject_id=subject_scope.subject_id,
                assessment_type=command.assessment_type,
                actor_id=command.actor_id,
                now=now,
            )
            assessment_no = self.repository.next_assessment_no(
                subject_id=subject_scope.subject_id,
                assessment_type=command.assessment_type,
            )
            assessment = self.repository.create_assessment(
                created_at=now,
                updated_at=now,
                deleted=False,
                study_id=command.study_id,
                site_id=command.site_id,
                subject_id=command.subject_id,
                event_instance_id=command.event_instance_id or getattr(fact_snapshot, "event_instance_id", None),
                assessment_type=command.assessment_type,
                assessment_no=assessment_no,
                result=evaluation.result,
                assessment_status=EligibilityAssessmentStatusChoices.FINAL,
                is_current=True,
                assessed_by_id=command.actor_id,
                assessed_at=now,
                finalized_by_id=command.actor_id,
                finalized_at=now,
                protocol_version=command.protocol_version,
                study_version=command.study_version,
                crf_version=command.crf_version,
                source_context=command.source_context,
                source_object_type=command.source_object_type,
                source_object_id=command.source_object_id,
                source_page_state_id=command.source_page_state_id or getattr(fact_snapshot, "page_state_id", None),
                source_page_entry_id=command.source_page_entry_id or getattr(fact_snapshot, "page_entry_id", None),
                source_data_version=command.source_data_version or getattr(fact_snapshot, "source_data_version", None),
                source_data_hash=command.source_data_hash or getattr(fact_snapshot, "source_data_hash", None),
                rule_code=evaluation.rule_code or command.rule_code,
                rule_version=command.rule_version,
                conclusion_field_key=command.conclusion_field_key,
                conclusion_value=evaluation.conclusion_value,
                facts_json=self._to_json(evaluation.facts),
                failed_conditions_json=self._to_json(evaluation.failed_conditions),
                failure_reason_code=self._failure_reason_code(evaluation.failed_conditions),
                failure_reason_text=self._failure_reason_text(evaluation.failed_conditions),
                reason_code=command.reason_code,
                reason_text=command.reason_text,
                created_by_id=command.actor_id,
                updated_by_id=command.actor_id,
            )
            if evaluation.result == EligibilityResultChoices.NOT_ELIGIBLE:
                self.repository.bulk_create_failures(
                    assessment=assessment,
                    failures=evaluation.failed_conditions,
                    actor_id=command.actor_id,
                    now=now,
                )

            status_transition = self._apply_enrollment_decision(
                command=command,
                result=evaluation.result,
            )
            gate_evaluation = self._record_enrollment_gate(
                command=command,
                assessment=assessment,
                evaluation=evaluation,
                gate_result="pass" if evaluation.result == EligibilityResultChoices.ELIGIBLE else "fail",
                now=now,
            )
            self._audit_superseded(superseded_assessments, actor_id=command.actor_id)
            self._audit_assessment_finalized(assessment, command=command)
            if status_transition is not None:
                self._audit_subject_status_changed(status_transition, command=command)

        return EligibilityAssessmentResult(
            assessment_id=assessment.pk,
            result=assessment.result,
            assessment_status=assessment.assessment_status,
            is_current=assessment.is_current,
            gate_result=getattr(gate_evaluation, "result", None),
        )

    def _can_recover_failed_assessment(self, *, command, fact_snapshot, evaluation) -> bool:
        if command.force_result or evaluation.result != EligibilityResultChoices.ELIGIBLE:
            return False
        assessment = self.repository.get_current_assessment(
            study_id=command.study_id,
            subject_id=command.subject_id,
            assessment_type=command.assessment_type,
        )
        if assessment is None:
            return False
        assessment = self.repository.get_assessment_for_update(
            study_id=command.study_id,
            subject_id=command.subject_id,
            assessment_id=assessment.pk,
        )
        if (
            assessment is None
            or not assessment.is_current
            or assessment.result != EligibilityResultChoices.NOT_ELIGIBLE
            or assessment.assessment_status not in {
                EligibilityAssessmentStatusChoices.FINAL,
                EligibilityAssessmentStatusChoices.STALE,
            }
            or assessment.event_instance_id != command.event_instance_id
            or assessment.source_context != command.source_context
            or assessment.source_object_type != command.source_object_type
            or assessment.source_object_id != command.source_object_id
        ):
            return False
        source_hash = getattr(fact_snapshot, "source_data_hash", None)
        if not source_hash or source_hash == assessment.source_data_hash:
            return False
        # Event snapshots hash all source pages; their reference page is simply
        # the most recently updated page and can change after a correction.
        if command.source_object_type != "EVENT_INSTANCE":
            source_version = getattr(fact_snapshot, "source_data_version", None)
            if (
                getattr(fact_snapshot, "page_state_id", None) != assessment.source_page_state_id
                or source_version is None
                or assessment.source_data_version is None
                or source_version <= assessment.source_data_version
            ):
                return False
        return not (
            self.subject_workflow_adapter.is_subject_enrolled(
                study_id=command.study_id,
                subject_id=command.subject_id,
            )
            or self.subject_workflow_adapter.is_subject_randomized(
                study_id=command.study_id,
                subject_id=command.subject_id,
            )
        )

    def retract(self, command: RetractEligibilityAssessmentCommand) -> EligibilityAssessmentResult:
        self._require_permission(command.actor_id, command.study_id, "retract_subject_eligibility")
        if not command.reason_code or not command.reason_text:
            raise EligibilityAssessmentError("Reason code and reason text are required to retract eligibility.")

        with self.transaction_context():
            assessment = self.repository.get_assessment_for_update(
                study_id=command.study_id,
                subject_id=command.subject_id,
                assessment_id=command.assessment_id,
            )
            if assessment is None:
                raise EligibilityAssessmentNotFoundError()
            if self.subject_workflow_adapter.is_subject_randomized(
                study_id=command.study_id,
                subject_id=command.subject_id,
            ):
                raise EligibilityAssessmentRetractBlockedError("Cannot retract eligibility after randomization.")

            before_data = self._assessment_snapshot(assessment)
            now = self.repository.now()
            assessment.assessment_status = EligibilityAssessmentStatusChoices.RETRACTED
            assessment.is_current = False
            assessment.retracted_by_id = command.actor_id
            assessment.retracted_at = now
            assessment.reason_code = command.reason_code
            assessment.reason_text = command.reason_text
            assessment.updated_at = now
            assessment.updated_by_id = command.actor_id
            self.repository.save_assessment(
                assessment,
                update_fields=[
                    "assessment_status",
                    "is_current",
                    "retracted_by_id",
                    "retracted_at",
                    "reason_code",
                    "reason_text",
                    "updated_at",
                    "updated_by_id",
                ],
            )
            status_transition = self.subject_workflow_adapter.mark_screened_after_retract(
                study_id=assessment.study_id,
                site_id=assessment.site_id,
                subject_id=assessment.subject_id,
                actor_user_id=command.actor_id,
                reason_code=command.reason_code,
                reason_text=command.reason_text,
            )
            self.audit_context_adapter.record_event(
                action=AuditEventActionEnum.ELIGIBILITY_ASSESSMENT_RETRACTED,
                object_type=AuditEventObjectTypeEnum.SUBJECT_ELIGIBILITY_ASSESSMENT,
                object_id=str(assessment.pk),
                actor_user_id=command.actor_id,
                before_data=before_data,
                after_data=self._assessment_snapshot(assessment),
            )
            self._audit_subject_status_changed(status_transition, command=command)

        return EligibilityAssessmentResult(
            assessment_id=assessment.pk,
            result=assessment.result,
            assessment_status=assessment.assessment_status,
            is_current=assessment.is_current,
        )

    def mark_stale_on_source_data_change(
        self,
        command: MarkEligibilityStaleOnSourceDataChangeCommand,
    ) -> list[EligibilityAssessmentResult]:
        stale_results = []
        with self.transaction_context():
            assessments = self.repository.list_current_final_assessments_for_source(
                source_context=command.source_context,
                source_object_type=command.source_object_type,
                source_object_id=command.source_object_id,
                source_page_state_id=command.source_page_state_id,
                source_page_entry_id=command.source_page_entry_id,
                source_data_hash=command.source_data_hash,
            )
            now = self.repository.now()
            for assessment in assessments:
                before_data = self._assessment_snapshot(assessment)
                assessment.assessment_status = EligibilityAssessmentStatusChoices.STALE
                assessment.updated_at = now
                assessment.updated_by_id = command.actor_id
                assessment.reason_code = command.reason_code
                assessment.reason_text = command.reason_text
                self.repository.save_assessment(
                    assessment,
                    update_fields=[
                        "assessment_status",
                        "updated_at",
                        "updated_by_id",
                        "reason_code",
                        "reason_text",
                    ],
                )
                self.audit_context_adapter.record_event(
                    action=AuditEventActionEnum.ELIGIBILITY_ASSESSMENT_STALE,
                    object_type=AuditEventObjectTypeEnum.SUBJECT_ELIGIBILITY_ASSESSMENT,
                    object_id=str(assessment.pk),
                    actor_user_id=command.actor_id,
                    before_data=before_data,
                    after_data=self._assessment_snapshot(assessment),
                )
                status_transition = self._mark_subject_screened_if_reassessment_required(
                    assessment=assessment,
                    command=command,
                )
                if status_transition is not None:
                    self._audit_subject_status_changed(status_transition, command=command)
                stale_results.append(
                    EligibilityAssessmentResult(
                        assessment_id=assessment.pk,
                        result=assessment.result,
                        assessment_status=assessment.assessment_status,
                        is_current=assessment.is_current,
                    )
                )
        return stale_results

    def _mark_subject_screened_if_reassessment_required(self, *, assessment, command):
        if self.subject_workflow_adapter.is_subject_randomized(
            study_id=assessment.study_id,
            subject_id=assessment.subject_id,
        ):
            return None
        if self.subject_workflow_adapter.is_subject_enrolled(
            study_id=assessment.study_id,
            subject_id=assessment.subject_id,
        ):
            return None
        return self.subject_workflow_adapter.mark_screened_after_retract(
            study_id=assessment.study_id,
            site_id=assessment.site_id,
            subject_id=assessment.subject_id,
            actor_user_id=command.actor_id,
            reason_code=command.reason_code,
            reason_text=command.reason_text,
        )

    def enroll_subject(self, command: EnrollSubjectCommand) -> EligibilityAssessmentResult:
        self._require_permission(
            command.actor_id,
            command.study_id,
            "finalize_subject_eligibility",
            site_id=command.site_id,
            bypass=command.automatic,
        )
        assessment = self.repository.get_current_assessment(
            study_id=command.study_id,
            subject_id=command.subject_id,
            assessment_type=command.assessment_type,
        )
        if not self._is_final_eligible(assessment):
            now = self.repository.now()
            self._record_enrollment_gate_for_missing_or_failed_assessment(
                command=command,
                assessment=assessment,
                now=now,
            )
            raise EligibilityEnrollmentGateError()

        status_transition = self.subject_workflow_adapter.enroll_subject(
            study_id=command.study_id,
            site_id=command.site_id,
            subject_id=command.subject_id,
            actor_user_id=command.actor_id,
            reason_code=command.reason_code,
            reason_text=command.reason_text,
        )
        self._audit_subject_status_changed(status_transition, command=command)
        self.audit_context_adapter.record_event(
            action=AuditEventActionEnum.ENROLL_SUBJECT,
            object_type=AuditEventObjectTypeEnum.SUBJECT_ENROLLMENT,
            object_id=str(command.subject_id),
            actor_user_id=command.actor_id,
            before_data={"assessment_id": assessment.pk, "is_enrolled": False},
            after_data={"assessment_id": assessment.pk, "is_enrolled": True, "status": status_transition.to_status},
        )
        return EligibilityAssessmentResult(
            assessment_id=assessment.pk,
            result=assessment.result,
            assessment_status=assessment.assessment_status,
            is_current=assessment.is_current,
            gate_result="pass",
        )

    def _read_fact_snapshot(self, command: FinalizeEligibilityAssessmentCommand):
        if command.source_context != "datacapture":
            return None
        if command.source_object_type == "EVENT_INSTANCE" and command.source_object_id is not None:
            from apps.datacapture.public import read_fact_snapshot_for_event_instance

            return read_fact_snapshot_for_event_instance(event_instance_id=command.source_object_id)
        page_state_id = command.source_page_state_id or (
            command.source_object_id if command.source_object_type == "PAGE_STATE" else None
        )
        if page_state_id is None:
            return None
        return self.fact_reader.read_for_page_state(page_state_id=page_state_id)

    def _evaluate(self, *, command: FinalizeEligibilityAssessmentCommand, facts: dict[str, Any]) -> EligibilityEvaluation:
        if command.force_result:
            return EligibilityEvaluation(
                result=command.force_result,
                facts=facts,
                failed_conditions=[],
                rule_code=command.rule_code,
                conclusion_value=self._string_or_none(facts.get(command.conclusion_field_key or "")),
            )

        inline_condition = self._condition_from_command(command)
        conditions = [inline_condition] if inline_condition is not None else []
        if inline_condition is None and command.rule_code:
            conditions = self.repository.list_active_eligibility_conditions(
                study_id=command.study_id,
                study_version=command.study_version,
                rule_code=command.rule_code,
            )
        for condition in conditions:
            failed_conditions = self._evaluate_condition_definition(condition, facts)
            if not failed_conditions:
                return EligibilityEvaluation(
                    result=EligibilityResultChoices.ELIGIBLE,
                    facts=facts,
                    failed_conditions=[],
                    rule_code=condition.code,
                    conclusion_value=self._string_or_none(facts.get(command.conclusion_field_key or "")),
                )
            return EligibilityEvaluation(
                result=EligibilityResultChoices.NOT_ELIGIBLE,
                facts=facts,
                failed_conditions=failed_conditions,
                rule_code=condition.code,
                conclusion_value=self._string_or_none(facts.get(command.conclusion_field_key or "")),
            )

        failed_conditions = self._evaluate_convention_facts(facts)
        return EligibilityEvaluation(
            result=EligibilityResultChoices.NOT_ELIGIBLE if failed_conditions else EligibilityResultChoices.ELIGIBLE,
            facts=facts,
            failed_conditions=failed_conditions,
            rule_code=command.rule_code,
            conclusion_value=self._string_or_none(facts.get(command.conclusion_field_key or "screening.eligibility_conclusion")),
        )

    def _condition_from_command(self, command: FinalizeEligibilityAssessmentCommand):
        if command.rule_expression_json in (None, ""):
            return None
        expression_json = (
            self._to_json(command.rule_expression_json)
            if isinstance(command.rule_expression_json, dict)
            else str(command.rule_expression_json)
        )
        return SimpleNamespace(
            code=command.rule_code or "workflow_action_condition",
            expression_json=expression_json,
        )

    def _evaluate_condition_definition(self, condition, facts: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            expression = json.loads(condition.expression_json or "{}")
        except json.JSONDecodeError:
            return [self._failed_condition("eligibility.expression", "valid_json", condition.expression_json, "OTHER")]
        if isinstance(expression, dict) and "any" in expression:
            checks = expression.get("any")
            if isinstance(checks, list) and any(self._expression_matches(check, facts) for check in checks):
                return []
            return [
                self._failed_condition(
                    getattr(condition, "code", None) or "eligibility.expression",
                    "any_condition_satisfied",
                    False,
                    "OTHER",
                    operator="any",
                )
            ]
        if isinstance(expression, dict) and "not" in expression:
            if not self._expression_matches(expression.get("not"), facts):
                return []
            return [
                self._failed_condition(
                    getattr(condition, "code", None) or "eligibility.expression",
                    "not_condition_satisfied",
                    True,
                    "OTHER",
                    operator="not",
                )
            ]
        checks = expression.get("all") if isinstance(expression, dict) else None
        if not isinstance(checks, list):
            return []
        failed_conditions = []
        for index, check in enumerate(checks, start=1):
            if isinstance(check, dict) and any(key in check for key in ("all", "any", "not")):
                if self._expression_matches(check, facts):
                    continue
                failed_conditions.append(
                    self._failed_condition(
                        getattr(condition, "code", None) or "eligibility.expression",
                        "nested_condition_satisfied",
                        False,
                        "OTHER",
                        operator="expression",
                        display_order=index,
                    )
                )
                continue
            fact_key = check.get("fact")
            operator = check.get("operator") or "equals"
            expected = check.get("value")
            actual = facts.get(fact_key)
            if not self._evaluate_operator(actual, operator, expected):
                failed_conditions.append(
                    self._failed_condition(
                        fact_key,
                        expected,
                        actual,
                        self._criterion_type_for_fact(fact_key),
                        operator=operator,
                        display_order=index,
                    )
                )
        return failed_conditions

    def _expression_matches(self, expression, facts: dict[str, Any]) -> bool:
        if not isinstance(expression, dict):
            return False
        if "all" in expression:
            checks = expression.get("all")
            return isinstance(checks, list) and all(self._expression_matches(check, facts) for check in checks)
        if "any" in expression:
            checks = expression.get("any")
            return isinstance(checks, list) and any(self._expression_matches(check, facts) for check in checks)
        if "not" in expression:
            return not self._expression_matches(expression.get("not"), facts)
        fact_key = expression.get("fact")
        if not fact_key:
            return False
        return self._evaluate_operator(
            facts.get(fact_key),
            expression.get("operator") or "equals",
            expression.get("value"),
        )

    def _evaluate_convention_facts(self, facts: dict[str, Any]) -> list[dict[str, Any]]:
        checks = [
            ("screening.inclusion.all_required_passed", "equals", True, "INCLUSION"),
            ("screening.exclusion.any_exclusion_present", "equals", False, "EXCLUSION"),
        ]
        if "screening.eligibility_conclusion" in facts:
            checks.append(("screening.eligibility_conclusion", "in", ["yes", "eligible", True], "OTHER"))
        failed_conditions = []
        for index, (fact_key, operator, expected, criterion_type) in enumerate(checks, start=1):
            actual = facts.get(fact_key)
            if actual is None:
                continue
            if not self._evaluate_operator(actual, operator, expected):
                failed_conditions.append(
                    self._failed_condition(
                        fact_key,
                        expected,
                        actual,
                        criterion_type,
                        operator=operator,
                        display_order=index,
                    )
                )
        return failed_conditions

    @staticmethod
    def _evaluate_operator(actual, operator: str, expected) -> bool:
        normalized_operator = (operator or "equals").strip().lower()
        if normalized_operator == "equals":
            return actual == expected
        if normalized_operator == "in":
            expected_values = expected if isinstance(expected, list) else [expected]
            return actual in expected_values or str(actual).strip().lower() in {
                str(value).strip().lower() for value in expected_values
            }
        return actual == expected

    @staticmethod
    def _failed_condition(fact_key, expected, actual, criterion_type, *, operator="equals", display_order=1):
        return {
            "criterion_code": fact_key,
            "criterion_type": criterion_type,
            "fact_key": fact_key,
            "operator": operator,
            "expected_value": EligibilityAssessmentService._to_json(expected),
            "actual_value": EligibilityAssessmentService._to_json(actual),
            "value_type": "json" if isinstance(expected, (dict, list)) else "string",
            "reason_code": "eligibility_condition_failed",
            "reason_message": f"{fact_key} did not satisfy {operator}",
            "display_order": display_order,
        }

    @staticmethod
    def _criterion_type_for_fact(fact_key: str | None) -> str:
        fact_key = (fact_key or "").lower()
        if "inclusion" in fact_key:
            return "INCLUSION"
        if "exclusion" in fact_key:
            return "EXCLUSION"
        return "OTHER"

    def _apply_enrollment_decision(self, *, command, result):
        if result == EligibilityResultChoices.ELIGIBLE:
            return self.subject_workflow_adapter.mark_eligible_from_assessment(
                study_id=command.study_id,
                site_id=command.site_id,
                subject_id=command.subject_id,
                actor_user_id=command.actor_id,
                reason_code=command.reason_code,
                reason_text=command.reason_text,
            )
        if result == EligibilityResultChoices.NOT_ELIGIBLE:
            return self.subject_workflow_adapter.mark_screen_failure_from_assessment(
                study_id=command.study_id,
                site_id=command.site_id,
                subject_id=command.subject_id,
                actor_user_id=command.actor_id,
                reason_code=command.reason_code,
                reason_text=command.reason_text,
            )
        return None

    def _record_enrollment_gate(self, *, command, assessment, evaluation, gate_result: str, now):
        event_definition_id = self._resolve_gate_event_definition_id(command)
        if event_definition_id is None:
            return None
        gate_evaluation = self.repository.create_gate_evaluation(
            created_at=now,
            study_id=command.study_id,
            subject_id=command.subject_id,
            event_definition_id=event_definition_id,
            event_instance_id=command.event_instance_id,
            gate_code="eligibility_for_enrollment",
            gate_type="action",
            target_action="enroll_subject",
            result=gate_result,
            evaluated_at=now,
            evaluated_by_id=command.actor_id,
            rule_code=evaluation.rule_code,
            rule_version=command.rule_version,
            facts_json=self._to_json(evaluation.facts),
            failed_conditions_json=self._to_json(evaluation.failed_conditions),
            blocking_reasons_json=self._to_json([]),
            source_context="eligibility",
            source_object_id=assessment.pk,
        )
        self.repository.bulk_create_gate_condition_results(
            gate_evaluation=gate_evaluation,
            conditions=evaluation.failed_conditions,
        )
        return gate_evaluation

    def _record_enrollment_gate_for_missing_or_failed_assessment(self, *, command, assessment, now):
        event_definition_id = self.repository.find_gate_event_definition_id(
            study_id=command.study_id,
            study_version=getattr(assessment, "study_version", "") or "",
            preferred_codes=["ENROLLMENT", "ELIGIBILITY_ASSESSMENT"],
        )
        if event_definition_id is None:
            return None
        return self.repository.create_gate_evaluation(
            created_at=now,
            study_id=command.study_id,
            subject_id=command.subject_id,
            event_definition_id=event_definition_id,
            event_instance_id=None,
            gate_code="eligibility_for_enrollment",
            gate_type="action",
            target_action="enroll_subject",
            result="fail",
            evaluated_at=now,
            evaluated_by_id=command.actor_id,
            rule_code="ELIGIBILITY_FINAL_ELIGIBLE",
            rule_version=None,
            facts_json=self._to_json(self._eligibility_latest_facts(assessment)),
            failed_conditions_json=self._to_json([
                {
                    "fact_key": "eligibility.latest.result",
                    "operator": "equals",
                    "expected_value": EligibilityResultChoices.ELIGIBLE,
                    "actual_value": getattr(assessment, "result", None),
                    "reason_code": "eligibility_not_final_eligible",
                    "reason_message": "Latest current eligibility assessment is not FINAL ELIGIBLE.",
                }
            ]),
            blocking_reasons_json=self._to_json(["eligibility_not_final_eligible"]),
            source_context="eligibility",
            source_object_id=getattr(assessment, "pk", None),
        )

    def _resolve_gate_event_definition_id(self, command):
        if command.event_instance_id:
            event_scope = self.subject_workflow_adapter.get_event_scope(event_instance_id=command.event_instance_id)
            if event_scope is not None:
                return event_scope.event_definition_id
        return self.repository.find_gate_event_definition_id(
            study_id=command.study_id,
            study_version=command.study_version,
            preferred_codes=["ENROLLMENT", "ELIGIBILITY_ASSESSMENT", "SCREENING"],
        )

    def _require_permission(
        self,
        actor_id: int | None,
        study_id: int,
        permission_codename: str,
        *,
        site_id: int | None = None,
        bypass: bool = False,
    ) -> None:
        if bypass:
            return
        if not self.repository.actor_has_permission(
            actor_id=actor_id,
            study_id=study_id,
            site_id=site_id,
            permission_codename=permission_codename,
        ):
            raise EligibilityAssessmentPermissionError(
                f"Permission study.{permission_codename} is required.",
            )

    @staticmethod
    def _is_final_eligible(assessment) -> bool:
        return bool(
            assessment
            and assessment.is_current
            and assessment.assessment_status == EligibilityAssessmentStatusChoices.FINAL
            and assessment.result == EligibilityResultChoices.ELIGIBLE
        )

    @staticmethod
    def _eligibility_latest_facts(assessment) -> dict[str, Any]:
        return {
            "eligibility.latest.result": getattr(assessment, "result", None),
            "eligibility.latest.assessment_status": getattr(assessment, "assessment_status", None),
            "eligibility.latest.is_current": getattr(assessment, "is_current", False),
        }

    @staticmethod
    def _to_json(value) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)

    @staticmethod
    def _string_or_none(value) -> str | None:
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _failure_reason_code(failed_conditions: list[dict[str, Any]]) -> str | None:
        return failed_conditions[0].get("reason_code") if failed_conditions else None

    @staticmethod
    def _failure_reason_text(failed_conditions: list[dict[str, Any]]) -> str | None:
        if not failed_conditions:
            return None
        return "; ".join(str(item.get("reason_message") or item.get("fact_key")) for item in failed_conditions)

    @staticmethod
    def _assessment_snapshot(assessment) -> dict[str, Any]:
        return {
            "id": assessment.pk,
            "study_id": assessment.study_id,
            "site_id": assessment.site_id,
            "subject_id": assessment.subject_id,
            "assessment_type": assessment.assessment_type,
            "assessment_no": assessment.assessment_no,
            "result": assessment.result,
            "assessment_status": assessment.assessment_status,
            "is_current": assessment.is_current,
            "source_context": assessment.source_context,
            "source_object_type": assessment.source_object_type,
            "source_object_id": assessment.source_object_id,
            "source_page_state_id": assessment.source_page_state_id,
            "source_page_entry_id": assessment.source_page_entry_id,
            "source_data_version": assessment.source_data_version,
            "source_data_hash": assessment.source_data_hash,
            "rule_code": assessment.rule_code,
            "rule_version": assessment.rule_version,
            "reason_code": assessment.reason_code,
            "reason_text": assessment.reason_text,
        }

    def _audit_superseded(self, assessments, *, actor_id):
        for assessment in assessments:
            self.audit_context_adapter.record_event(
                action=AuditEventActionEnum.ELIGIBILITY_ASSESSMENT_SUPERSEDED,
                object_type=AuditEventObjectTypeEnum.SUBJECT_ELIGIBILITY_ASSESSMENT,
                object_id=str(assessment.pk),
                actor_user_id=actor_id,
                before_data={"is_current": True, "assessment_status": "FINAL"},
                after_data=self._assessment_snapshot(assessment),
            )

    def _audit_assessment_finalized(self, assessment, *, command):
        self.audit_context_adapter.record_event(
            action=AuditEventActionEnum.ELIGIBILITY_ASSESSMENT_FINALIZED,
            object_type=AuditEventObjectTypeEnum.SUBJECT_ELIGIBILITY_ASSESSMENT,
            object_id=str(assessment.pk),
            actor_user_id=command.actor_id,
            before_data={},
            after_data=self._assessment_snapshot(assessment),
        )

    def _audit_subject_status_changed(self, status_transition, *, command):
        actor_id = getattr(command, "actor_id", None)
        self.audit_context_adapter.record_event(
            action=AuditEventActionEnum.SUBJECT_STATUS_CHANGED_FROM_ELIGIBILITY,
            object_type=AuditEventObjectTypeEnum.SUBJECT_ENROLLMENT,
            object_id=str(status_transition.subject_id),
            actor_user_id=actor_id,
            before_data={"status": status_transition.from_status},
            after_data={
                "status": status_transition.to_status,
                "is_enrolled": status_transition.is_enrolled,
                "status_datetime": status_transition.status_datetime,
            },
        )


__all__ = [
    "DataCaptureEligibilityFactReader",
    "EligibilityAssessmentService",
    "EligibilityEvaluation",
    "StudyEligibilityTransitionFactService",
]
