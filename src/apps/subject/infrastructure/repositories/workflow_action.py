from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone

from apps.core.choices import EventExecutionModeChoices, EventInstanceStatusChoices
from apps.study.models import EventTransitionRule, SubjectEligibilityAssessment
from apps.subject.models import (
    SubjectEnrollment,
    SubjectEventInstance,
    SubjectEventInstanceTransitionLog,
    SubjectRandomization,
)

_EVENT_CATEGORY_RANDOMIZATION = "randomization"
_EVENT_CATEGORY_WASHOUT = "washout"
_EVENT_CODE_ELIGIBILITY_ASSESSMENT = "eligibility_assessment"
_EVENT_CODE_ENROLLMENT = "enrollment"


@dataclass(frozen=True)
class SubjectEventWorkflowContext:
    event_instance_id: int
    study_id: int
    subject_id: int
    site_id: int
    study_version: str
    status: str
    event_definition_id: int
    event_code: str
    event_type: str
    event_category: str
    execution_mode: str


@dataclass(frozen=True)
class SubjectWorkflowActionRuleContext:
    condition_code: str | None
    condition_expression_json: str | None


class DjangoSubjectWorkflowActionRepository:
    def now(self):
        return timezone.now()

    @staticmethod
    def _supported_workflow_action_filter() -> Q:
        return (
            Q(event_definition__code__iexact=_EVENT_CODE_ELIGIBILITY_ASSESSMENT)
            | Q(event_definition__code__iexact=_EVENT_CODE_ENROLLMENT)
            | Q(event_definition__event_category__iexact=_EVENT_CATEGORY_RANDOMIZATION)
            | Q(event_definition__event_category__iexact=_EVENT_CATEGORY_WASHOUT)
        )

    @staticmethod
    def _triggerable_workflow_action_filter() -> Q:
        failed_events = SubjectEligibilityAssessment.objects.filter(
            deleted=False,
            is_current=True,
            assessment_type="SCREENING",
            result="NOT_ELIGIBLE",
            assessment_status__in=("FINAL", "STALE"),
            source_context="datacapture",
            source_object_type="EVENT_INSTANCE",
        ).values("event_instance_id")
        enrolled_subjects = SubjectEnrollment.objects.filter(
            deleted=False,
            is_enrolled=True,
        ).values("subject_id")
        randomized_subjects = SubjectRandomization.objects.filter(
            deleted=False,
            randomization_number__isnull=False,
        ).values("subject_id")
        recovery_candidate = (
            Q(
                status=EventInstanceStatusChoices.COMPLETED,
                event_definition__code__iexact=_EVENT_CODE_ELIGIBILITY_ASSESSMENT,
                pk__in=failed_events,
            )
            & ~Q(subject_id__in=enrolled_subjects)
            & ~Q(subject_id__in=randomized_subjects)
        )
        return Q(status=EventInstanceStatusChoices.OPEN) | recovery_candidate

    def is_open_workflow_action_event(
        self,
        *,
        study_id: int,
        subject_id: int,
        event_instance_id: int,
    ) -> bool:
        return SubjectEventInstance.objects.filter(
            pk=event_instance_id,
            study_id=study_id,
            subject_id=subject_id,
            deleted=False,
            event_definition__execution_mode=EventExecutionModeChoices.WORKFLOW_ACTION,
        ).filter(
            self._supported_workflow_action_filter(),
            self._triggerable_workflow_action_filter(),
        ).exists()

    def get_open_workflow_action_context(
        self,
        *,
        study_id: int,
        subject_id: int,
        event_instance_id: int,
    ) -> SubjectEventWorkflowContext | None:
        event_instance = (
            SubjectEventInstance.objects.select_related("event_definition", "subject")
            .filter(
                pk=event_instance_id,
                study_id=study_id,
                subject_id=subject_id,
                deleted=False,
                event_definition__execution_mode=EventExecutionModeChoices.WORKFLOW_ACTION,
            )
            .filter(self._supported_workflow_action_filter(), self._triggerable_workflow_action_filter())
            .only(
                "id",
                "study_id",
                "subject_id",
                "study_version",
                "status",
                "event_definition_id",
                "event_definition__code",
                "event_definition__event_type",
                "event_definition__event_category",
                "event_definition__execution_mode",
                "subject__site_id",
            )
            .first()
        )
        return self._to_workflow_context(event_instance)

    def map_open_workflow_action_context_by_subject_id(
        self,
        *,
        study_id: int,
        subject_ids,
    ) -> dict[int, SubjectEventWorkflowContext]:
        subject_id_list = tuple(subject_ids or ())
        if not subject_id_list:
            return {}

        context_by_subject_id = {}
        event_instances = (
            SubjectEventInstance.objects.select_related("event_definition", "subject")
            .filter(
                study_id=study_id,
                subject_id__in=subject_id_list,
                deleted=False,
                event_definition__execution_mode=EventExecutionModeChoices.WORKFLOW_ACTION,
            )
            .filter(self._supported_workflow_action_filter(), self._triggerable_workflow_action_filter())
            .only(
                "id",
                "study_id",
                "subject_id",
                "study_version",
                "status",
                "event_definition_id",
                "event_definition__code",
                "event_definition__event_type",
                "event_definition__event_category",
                "event_definition__execution_mode",
                "event_definition__sequence_no",
                "subject__site_id",
            )
            .order_by("subject_id", "event_definition__sequence_no", "id")
        )
        for event_instance in event_instances:
            context_by_subject_id.setdefault(
                event_instance.subject_id,
                self._to_workflow_context(event_instance),
            )
        return context_by_subject_id

    def map_open_workflow_action_event_id_by_subject_id(
        self,
        *,
        study_id: int,
        subject_ids,
    ) -> dict[int, int]:
        subject_id_list = tuple(subject_ids or ())
        if not subject_id_list:
            return {}

        event_id_by_subject_id = {}
        rows = (
            SubjectEventInstance.objects.filter(
                study_id=study_id,
                subject_id__in=subject_id_list,
                deleted=False,
                event_definition__execution_mode=EventExecutionModeChoices.WORKFLOW_ACTION,
            )
            .filter(self._supported_workflow_action_filter(), self._triggerable_workflow_action_filter())
            .order_by("subject_id", "event_definition__sequence_no", "id")
            .values_list("subject_id", "id")
        )
        for subject_id, event_instance_id in rows:
            event_id_by_subject_id.setdefault(subject_id, event_instance_id)
        return event_id_by_subject_id

    def map_open_washout_event_id_by_subject_id(
        self,
        *,
        subject_ids,
    ) -> dict[int, int]:
        subject_id_list = tuple(subject_ids or ())
        if not subject_id_list:
            return {}

        event_id_by_subject_id = {}
        rows = (
            SubjectEventInstance.objects.filter(
                subject_id__in=subject_id_list,
                deleted=False,
                status=EventInstanceStatusChoices.OPEN,
                event_definition__event_category__iexact=_EVENT_CATEGORY_WASHOUT,
                event_definition__execution_mode=EventExecutionModeChoices.WORKFLOW_ACTION,
            )
            .order_by("subject_id", "event_definition__sequence_no", "id")
            .values_list("subject_id", "id")
        )
        for subject_id, event_instance_id in rows:
            event_id_by_subject_id.setdefault(subject_id, event_instance_id)
        return event_id_by_subject_id

    def get_event_workflow_context_for_update(self, *, event_instance_id: int) -> SubjectEventWorkflowContext | None:
        event_instance = (
            SubjectEventInstance.objects.select_for_update()
            .select_related("event_definition", "subject")
            .filter(pk=event_instance_id, deleted=False)
            .only(
                "id",
                "study_id",
                "subject_id",
                "study_version",
                "status",
                "event_definition_id",
                "event_definition__code",
                "event_definition__event_type",
                "event_definition__event_category",
                "event_definition__execution_mode",
                "subject__site_id",
            )
            .first()
        )
        return self._to_workflow_context(event_instance)

    @staticmethod
    def _to_workflow_context(event_instance) -> SubjectEventWorkflowContext | None:
        if event_instance is None:
            return None
        return SubjectEventWorkflowContext(
            event_instance_id=event_instance.pk,
            study_id=event_instance.study_id,
            subject_id=event_instance.subject_id,
            site_id=event_instance.subject.site_id,
            study_version=event_instance.study_version,
            status=event_instance.status,
            event_definition_id=event_instance.event_definition_id,
            event_code=event_instance.event_definition.code,
            event_type=event_instance.event_definition.event_type,
            event_category=event_instance.event_definition.event_category or "",
            execution_mode=event_instance.event_definition.execution_mode,
        )

    def has_subject_randomization(self, *, subject_id: int) -> bool:
        return SubjectRandomization.objects.filter(subject_id=subject_id, deleted=False).exists()

    def resolve_source_event_instance_id_for_workflow_event(self, *, event_instance_id: int) -> int | None:
        event_instance = (
            SubjectEventInstance.objects.filter(pk=event_instance_id, deleted=False)
            .only("id", "study_id", "subject_id", "study_version", "event_definition_id")
            .first()
        )
        if event_instance is None:
            return None

        source_event_definition_ids = list(
            EventTransitionRule.objects.filter(
                study_id=event_instance.study_id,
                study_version=event_instance.study_version,
                to_event_definition_id=event_instance.event_definition_id,
                deleted=False,
                is_enabled=True,
            )
            .order_by("display_order", "id")
            .values_list("from_event_definition_id", flat=True)
        )
        if not source_event_definition_ids:
            return None

        return (
            SubjectEventInstance.objects.filter(
                subject_id=event_instance.subject_id,
                event_definition_id__in=source_event_definition_ids,
                deleted=False,
            )
            .order_by("-id")
            .values_list("id", flat=True)
            .first()
        )

    def resolve_workflow_action_rule_for_event(self, *, event_instance_id: int) -> SubjectWorkflowActionRuleContext | None:
        event_instance = (
            SubjectEventInstance.objects.filter(pk=event_instance_id, deleted=False)
            .only("id", "study_id", "study_version", "event_definition_id")
            .first()
        )
        if event_instance is None:
            return None

        transition_rule = (
            EventTransitionRule.objects.select_related("condition_definition")
            .filter(
                study_id=event_instance.study_id,
                study_version=event_instance.study_version,
                to_event_definition_id=event_instance.event_definition_id,
                deleted=False,
                is_enabled=True,
            )
            .order_by("display_order", "id")
            .first()
        )
        if transition_rule is None:
            return None

        condition_definition = getattr(transition_rule, "condition_definition", None)
        condition_code = (
            getattr(condition_definition, "code", None)
            or transition_rule.condition_code
            or None
        )
        condition_expression_json = getattr(condition_definition, "expression_json", None)
        return SubjectWorkflowActionRuleContext(
            condition_code=condition_code,
            condition_expression_json=condition_expression_json,
        )

    def create_subject_randomization(
        self,
        *,
        study_id: int,
        site_id: int,
        subject_id: int,
        assignment,
        actor_user_id: int | None,
        now,
    ):
        return SubjectRandomization.objects.create(
            created_at=now,
            updated_at=now,
            deleted=False,
            randomization_status="randomized",
            randomization_datetime=now,
            randomization_sequence=str(assignment.sequence_no),
            randomization_number=str(assignment.sequence_no),
            randomization_source="workflow_action",
            randomized_by_id=actor_user_id,
            scheme_id=assignment.scheme_id,
            arm_id=assignment.arm_id,
            slot_id=assignment.slot_id,
            subject_id=subject_id,
            site_id=site_id,
            study_id=study_id,
            created_by_id=actor_user_id,
            updated_by_id=actor_user_id,
        )

    def complete_workflow_event_instance(
        self,
        *,
        event_instance_id: int,
        actor_user_id: int | None,
        now,
        reason: str,
    ) -> bool:
        event_instance = (
            SubjectEventInstance.objects.select_for_update()
            .filter(
                pk=event_instance_id,
                deleted=False,
                status=EventInstanceStatusChoices.OPEN,
            )
            .only("id", "study_id", "subject_id", "event_definition_id", "status")
            .first()
        )
        if event_instance is None:
            return False

        from_status = event_instance.status
        SubjectEventInstance.objects.filter(pk=event_instance.pk).update(
            status=EventInstanceStatusChoices.COMPLETED,
            completed_at=now,
            completed_by_id=actor_user_id,
            updated_at=now,
            updated_by_id=actor_user_id,
        )
        SubjectEventInstanceTransitionLog.objects.create(
            study_id=event_instance.study_id,
            subject_id=event_instance.subject_id,
            source_event_instance_id=event_instance.pk,
            target_event_instance_id=None,
            transition_rule_id=None,
            from_event_definition_id=event_instance.event_definition_id,
            to_event_definition_id=None,
            from_status=from_status,
            to_status=EventInstanceStatusChoices.COMPLETED,
            trigger_source="workflow_action",
            result="applied",
            reason=reason,
            facts_json="{}",
            created_at=now,
            updated_at=now,
            created_by_id=actor_user_id,
            updated_by_id=actor_user_id,
        )
        return True


__all__ = [
    "DjangoSubjectWorkflowActionRepository",
    "SubjectWorkflowActionRuleContext",
    "SubjectEventWorkflowContext",
]
