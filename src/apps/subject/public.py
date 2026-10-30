from apps.subject.application import (
    SubjectEventCompletionService,
    SubjectEventInstanceNotFoundError,
    SubjectEventInstanceResyncService,
    SubjectEventTransitionService,
    TriggerSubjectEventTransitionCommand,
)
from apps.subject.application.services.eligibility_workflow import (
    SubjectEligibilityWorkflowService,
    SubjectEnrollmentTransitionResult,
    SubjectEventScopeSnapshot,
    SubjectScopeSnapshot,
)
from apps.subject.application.services.identifier_policy_migration import (
    SubjectIdentifierMigrationBlockedError,
    SubjectIdentifierMigrationConfirmationRequiredError,
    SubjectIdentifierMigrationStalePlanError,
)
from apps.subject.models import (
    Subject,
    SubjectEventInstance,
    SubjectEventInstanceFile,
    SubjectMilestone,
    SubjectPeriod,
    SubjectPeriodMilestone,
)
from apps.subject.presentation.web.views.base import SubjectAbstractVerifyStudy


class SubjectEventLifecycleAdapter:
    def __init__(self, transition_service=None, completion_service=None, resync_service=None):
        self.transition_service = transition_service or SubjectEventTransitionService()
        self.completion_service = completion_service or SubjectEventCompletionService()
        self.resync_service = resync_service or SubjectEventInstanceResyncService()

    def trigger_event_transition(
        self,
        *,
        source_event_instance_id: int,
        facts=None,
        actor_user_id: int | None = None,
        trigger_source: str = "system",
    ):
        command = TriggerSubjectEventTransitionCommand(
            source_event_instance_id=source_event_instance_id,
            facts=facts or {},
            actor_user_id=actor_user_id,
            trigger_source=trigger_source,
        )
        return self.transition_service.execute(command)

    def complete_event_instance(
        self,
        *,
        event_instance_id: int,
        actor_user_id: int | None = None,
    ) -> bool:
        return self.completion_service.complete_event_instance(
            event_instance_id=event_instance_id,
            actor_user_id=actor_user_id,
        )

    def verify_event_instance(
        self,
        *,
        event_instance_id: int,
        actor_user_id: int | None = None,
    ) -> bool:
        return self.completion_service.verify_event_instance(
            event_instance_id=event_instance_id,
            actor_user_id=actor_user_id,
        )

    def mark_event_instance_in_progress(
        self,
        *,
        event_instance_id: int,
        actor_user_id: int | None = None,
    ) -> bool:
        return self.completion_service.mark_event_instance_in_progress(
            event_instance_id=event_instance_id,
            actor_user_id=actor_user_id,
        )

    def resync_event_instances(
        self,
        *,
        study_id: int,
        study_version: str,
        actor_user_id: int | None = None,
        include_all_subjects: bool = False,
        include_terminal_subjects: bool = False,
        create_missing_future_events: bool = False,
        subject_ids=None,
        trigger_source: str = "study_eventdefinition_resync",
    ):
        return self.resync_service.resync_study_version(
            study_id=study_id,
            study_version=study_version,
            actor_user_id=actor_user_id,
            include_all_subjects=include_all_subjects,
            include_terminal_subjects=include_terminal_subjects,
            create_missing_future_events=create_missing_future_events,
            subject_ids=subject_ids,
            trigger_source=trigger_source,
        )

    def resync_subject_active_study_version(
        self,
        *,
        study_id: int,
        subject_id: int,
        actor_user_id: int | None = None,
        include_terminal_subjects: bool = False,
        create_missing_future_events: bool = False,
        trigger_source: str = "subject_list_resync_stage",
    ):
        return self.resync_service.resync_subject_active_study_version(
            study_id=study_id,
            subject_id=subject_id,
            actor_user_id=actor_user_id,
            include_terminal_subjects=include_terminal_subjects,
            create_missing_future_events=create_missing_future_events,
            trigger_source=trigger_source,
        )


def trigger_subject_event_transition(
    *,
    source_event_instance_id: int,
    facts=None,
    actor_user_id: int | None = None,
    trigger_source: str = "system",
):
    return SubjectEventLifecycleAdapter().trigger_event_transition(
        source_event_instance_id=source_event_instance_id,
        facts=facts,
        actor_user_id=actor_user_id,
        trigger_source=trigger_source,
    )


def complete_subject_event_instance(
    *,
    event_instance_id: int,
    actor_user_id: int | None = None,
) -> bool:
    return SubjectEventLifecycleAdapter().complete_event_instance(
        event_instance_id=event_instance_id,
        actor_user_id=actor_user_id,
    )


def verify_subject_event_instance(
    *,
    event_instance_id: int,
    actor_user_id: int | None = None,
) -> bool:
    return SubjectEventLifecycleAdapter().verify_event_instance(
        event_instance_id=event_instance_id,
        actor_user_id=actor_user_id,
    )


def mark_subject_event_instance_in_progress(
    *,
    event_instance_id: int,
    actor_user_id: int | None = None,
) -> bool:
    return SubjectEventLifecycleAdapter().mark_event_instance_in_progress(
        event_instance_id=event_instance_id,
        actor_user_id=actor_user_id,
    )


def resync_subject_event_instances(
    *,
    study_id: int,
    study_version: str,
    actor_user_id: int | None = None,
    include_all_subjects: bool = False,
    include_terminal_subjects: bool = False,
    create_missing_future_events: bool = False,
    subject_ids=None,
    trigger_source: str = "study_eventdefinition_resync",
):
    return SubjectEventLifecycleAdapter().resync_event_instances(
        study_id=study_id,
        study_version=study_version,
        actor_user_id=actor_user_id,
        include_all_subjects=include_all_subjects,
        include_terminal_subjects=include_terminal_subjects,
        create_missing_future_events=create_missing_future_events,
        subject_ids=subject_ids,
        trigger_source=trigger_source,
    )


def resync_subject_active_study_version(
    *,
    study_id: int,
    subject_id: int,
    actor_user_id: int | None = None,
    include_terminal_subjects: bool = False,
    create_missing_future_events: bool = False,
    trigger_source: str = "subject_list_resync_stage",
):
    return SubjectEventLifecycleAdapter().resync_subject_active_study_version(
        study_id=study_id,
        subject_id=subject_id,
        actor_user_id=actor_user_id,
        include_terminal_subjects=include_terminal_subjects,
        create_missing_future_events=create_missing_future_events,
        trigger_source=trigger_source,
    )


def randomize_subject(**kwargs):
    from apps.subject.application.services.randomize_subject import RandomizeSubject, RandomizeSubjectCommand

    return RandomizeSubject().execute(RandomizeSubjectCommand(**kwargs))


def reconcile_imported_randomization_slots(*, assignments) -> int:
    from apps.subject.infrastructure.repositories.randomization import (
        DjangoSubjectRandomizationRepository,
    )

    return DjangoSubjectRandomizationRepository().reconcile_imported_slot_assignments(
        assignments=assignments,
    )


def preview_subject_identifier_policy_migration(
    *,
    study_id,
    current_policy,
    target_policy,
):
    from apps.subject.application.services.identifier_policy_migration import (
        SubjectIdentifierPolicyMigrationService,
    )

    return SubjectIdentifierPolicyMigrationService().preview(
        study_id=study_id,
        current_policy=current_policy,
        target_policy=target_policy,
    )


def migrate_subject_identifiers_for_policy(
    *,
    study_id,
    current_policy,
    target_policy,
    actor_user_id,
    expected_plan_hash,
    confirmation_code,
):
    from apps.subject.application.services.identifier_policy_migration import (
        SubjectIdentifierPolicyMigrationService,
    )

    return SubjectIdentifierPolicyMigrationService().execute(
        study_id=study_id,
        current_policy=current_policy,
        target_policy=target_policy,
        actor_user_id=actor_user_id,
        expected_plan_hash=expected_plan_hash,
        confirmation_code=confirmation_code,
    )


def preview_subject_identifier_policy_rollback(*, study_id, current_policy):
    from apps.subject.application.services.identifier_policy_migration import (
        SubjectIdentifierPolicyMigrationService,
    )

    return SubjectIdentifierPolicyMigrationService().preview_rollback(
        study_id=study_id,
        current_policy=current_policy,
    )


def rollback_subject_identifier_policy(
    *,
    study_id,
    current_policy,
    actor_user_id,
    expected_plan_hash,
    confirmation_code,
):
    from apps.subject.application.services.identifier_policy_migration import (
        SubjectIdentifierPolicyMigrationService,
    )

    return SubjectIdentifierPolicyMigrationService().execute_rollback(
        study_id=study_id,
        current_policy=current_policy,
        actor_user_id=actor_user_id,
        expected_plan_hash=expected_plan_hash,
        confirmation_code=confirmation_code,
    )


def advance_subject_period_after_washout(
    *,
    subject_id: int,
    actor_user_id: int | None = None,
    source_event_instance_id: int | None = None,
    trigger_source: str = "manual_period_transition",
):
    from apps.subject.application.services.period_lifecycle import (
        SubjectPeriodLifecycleService,
    )

    return SubjectPeriodLifecycleService().advance_after_washout(
        subject_id=subject_id,
        actor_user_id=actor_user_id,
        source_event_instance_id=source_event_instance_id,
        trigger_source=trigger_source,
    )


def get_subject_site_id(*, study_id: int, subject_id: int) -> int | None:
    return (
        Subject.objects.filter(pk=subject_id, study_id=study_id, deleted=False)
        .values_list("site_id", flat=True)
        .first()
    )


def get_event_instance_snapshot(*, event_instance_id: int):
    return (
        SubjectEventInstance.objects.filter(pk=event_instance_id, deleted=False)
        .values(
            "id",
            "subject_id",
            "study_id",
            "study_version",
            "event_definition_id",
            "repeat_index",
            "updated_at",
            "status",
        )
        .first()
    )


def get_subject_capture_eligibility(
    *,
    subject_id: int,
    event_instance_id: int,
    for_update: bool = False,
):
    from apps.subject.application.services.early_termination import (
        SubjectCaptureEligibilityService,
    )

    return SubjectCaptureEligibilityService().get_eligibility(
        subject_id=subject_id,
        event_instance_id=event_instance_id,
        for_update=for_update,
    )


class SubjectEligibilityWorkflowAdapter:
    def __init__(self, workflow_service=None):
        self.workflow_service = workflow_service or SubjectEligibilityWorkflowService()

    def get_subject_scope(self, *, study_id: int, site_id: int, subject_id: int) -> SubjectScopeSnapshot:
        return self.workflow_service.get_subject_scope(
            study_id=study_id,
            site_id=site_id,
            subject_id=subject_id,
        )

    def get_event_scope(self, *, event_instance_id: int) -> SubjectEventScopeSnapshot | None:
        return self.workflow_service.get_event_scope(event_instance_id=event_instance_id)

    def mark_eligible_from_assessment(self, **kwargs) -> SubjectEnrollmentTransitionResult:
        return self.workflow_service.mark_eligible_from_assessment(**kwargs)

    def mark_screen_failure_from_assessment(self, **kwargs) -> SubjectEnrollmentTransitionResult:
        return self.workflow_service.mark_screen_failure_from_assessment(**kwargs)

    def mark_screened_after_retract(self, **kwargs) -> SubjectEnrollmentTransitionResult:
        return self.workflow_service.mark_screened_after_retract(**kwargs)

    def enroll_subject(self, **kwargs) -> SubjectEnrollmentTransitionResult:
        return self.workflow_service.enroll_subject(**kwargs)

    def is_subject_randomized(self, *, study_id: int, subject_id: int) -> bool:
        return self.workflow_service.is_subject_randomized(study_id=study_id, subject_id=subject_id)

    def is_subject_enrolled(self, *, study_id: int, subject_id: int) -> bool:
        return self.workflow_service.is_subject_enrolled(study_id=study_id, subject_id=subject_id)


__all__ = [
    "Subject",
    "SubjectAbstractVerifyStudy",
    "SubjectEventInstance",
    "SubjectEventInstanceFile",
    "SubjectEventInstanceNotFoundError",
    "SubjectEventLifecycleAdapter",
    "SubjectEligibilityWorkflowAdapter",
    "SubjectEnrollmentTransitionResult",
    "SubjectEventScopeSnapshot",
    "SubjectMilestone",
    "SubjectIdentifierMigrationBlockedError",
    "SubjectIdentifierMigrationConfirmationRequiredError",
    "SubjectIdentifierMigrationStalePlanError",
    "SubjectPeriod",
    "SubjectPeriodMilestone",
    "SubjectScopeSnapshot",
    "advance_subject_period_after_washout",
    "complete_subject_event_instance",
    "get_subject_site_id",
    "get_event_instance_snapshot",
    "get_subject_capture_eligibility",
    "mark_subject_event_instance_in_progress",
    "resync_subject_active_study_version",
    "resync_subject_event_instances",
    "randomize_subject",
    "migrate_subject_identifiers_for_policy",
    "preview_subject_identifier_policy_migration",
    "preview_subject_identifier_policy_rollback",
    "rollback_subject_identifier_policy",
    "trigger_subject_event_transition",
    "verify_subject_event_instance",
]
