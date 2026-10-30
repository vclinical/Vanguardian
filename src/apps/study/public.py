from apps.study.application.commands import (
    EnrollSubjectCommand,
    FinalizeEligibilityAssessmentCommand,
    MarkEligibilityStaleOnSourceDataChangeCommand,
    RecordEventGateEvaluationCommand,
    RetractEligibilityAssessmentCommand,
)
from apps.study.application.exceptions import (
    EligibilityAssessmentPermissionError,
    EligibilityEnrollmentGateError,
)
from apps.study.application.services.crf_page_lifecycle import (
    CrfPageLifecycleActionState,
    StudyCrfPageLifecycleService,
)
from apps.study.application.services.event_attestation_policy import (
    EventAttestationPolicySnapshot,
    StudyEventAttestationPolicyReader,
)
from apps.study.application.services.event_form_display_label import (
    EventFormDisplayConfigSnapshot,
    EventFormDisplayLabelService,
    EventFormDisplayLabelValidationError,
    EventFormDisplayTemplatePreview,
)
from apps.study.application.services.event_gate_evaluation import EventGateEvaluationHistoryReader
from apps.study.application.services.randomization_workflow import (
    RandomizationSlotAssignment,
    StudyRandomizationSlotAssignmentService,
    StudyRandomizationTransitionFactService,
)
from apps.study.application.services.site_directory import StudySiteDirectoryQueryService
from apps.study.application.services.study_subject_code_generation import (
    StudySubjectIdentifierPolicyQueryService,
)
from apps.study.application.services.subject_export import (
    SubjectExportFieldCatalogService,
)


def assign_randomization_slot_for_subject(
    *,
    study_id: int,
    subject_id: int,
    event_instance_id: int,
    actor_user_id: int | None = None,
    scheme_id: int | None = None,
    stratum_code: str | None = None,
) -> RandomizationSlotAssignment | None:
    return StudyRandomizationSlotAssignmentService().assign_random_available_slot(
        study_id=study_id,
        subject_id=subject_id,
        event_instance_id=event_instance_id,
        actor_user_id=actor_user_id,
        scheme_id=scheme_id,
        stratum_code=stratum_code,
    )


def build_randomization_transition_facts(*, study_id: int) -> dict[str, object]:
    return StudyRandomizationTransitionFactService().build_facts(study_id=study_id)


def get_subject_identifier_policy(*, study_id: int):
    return StudySubjectIdentifierPolicyQueryService().get_policy(study_id=study_id)


def randomize_subject(**kwargs):
    from apps.subject.application.services.randomize_subject import RandomizeSubject, RandomizeSubjectCommand

    return RandomizeSubject().execute(RandomizeSubjectCommand(**kwargs))


def get_subject_treatment_timeline(subject_id: int):
    from apps.subject.application.services.treatment_timeline import SubjectTreatmentTimelineService

    return SubjectTreatmentTimelineService().get_subject_treatment_timeline(subject_id=subject_id)


def get_current_subject_treatment(
    subject_id: int,
    *,
    event_instance_id: int | None = None,
    as_of=None,
):
    from apps.subject.application.services.treatment_timeline import SubjectTreatmentTimelineService

    return SubjectTreatmentTimelineService().get_current_subject_treatment(
        subject_id=subject_id,
        event_instance_id=event_instance_id,
        as_of=as_of,
    )


def finalize_subject_eligibility_assessment(command: FinalizeEligibilityAssessmentCommand):
    from apps.study.application.services.eligibility_assessment import EligibilityAssessmentService

    return EligibilityAssessmentService().finalize(command)


def recover_failed_subject_eligibility_assessment(command: FinalizeEligibilityAssessmentCommand):
    from apps.study.application.services.eligibility_assessment import EligibilityAssessmentService

    return EligibilityAssessmentService().finalize(command, recover_failed_only=True)


def build_eligibility_transition_facts(
    *,
    study_id: int,
    subject_id: int,
    assessment_type: str = "SCREENING",
) -> dict[str, object]:
    from apps.study.application.services.eligibility_assessment import StudyEligibilityTransitionFactService

    return StudyEligibilityTransitionFactService().build_facts(
        study_id=study_id,
        subject_id=subject_id,
        assessment_type=assessment_type,
    )


def retract_subject_eligibility_assessment(command: RetractEligibilityAssessmentCommand):
    from apps.study.application.services.eligibility_assessment import EligibilityAssessmentService

    return EligibilityAssessmentService().retract(command)


def mark_subject_eligibility_stale_on_source_data_change(command: MarkEligibilityStaleOnSourceDataChangeCommand):
    from apps.study.application.services.eligibility_assessment import EligibilityAssessmentService

    return EligibilityAssessmentService().mark_stale_on_source_data_change(command)


def enroll_subject_after_eligibility_gate(command: EnrollSubjectCommand):
    from apps.study.application.services.eligibility_assessment import EligibilityAssessmentService

    return EligibilityAssessmentService().enroll_subject(command)


def record_event_gate_evaluation(command: RecordEventGateEvaluationCommand):
    from apps.study.application.services.event_gate_evaluation import EventGateEvaluationRecorder

    return EventGateEvaluationRecorder().record(command)


def list_event_gate_evaluation_history_for_subject(
    *,
    study_id: int,
    subject_id: int,
    limit: int = 200,
    search: str = "",
    field_name: str = "",
) -> list[dict]:
    return EventGateEvaluationHistoryReader().list_for_subject(
        study_id=study_id,
        subject_id=subject_id,
        limit=limit,
        search=search,
        field_name=field_name,
    )


def study_site_belongs_to_study(*, study_id: int, study_site_id: int) -> bool:
    return StudySiteDirectoryQueryService.study_site_belongs_to_study(
        study_id=study_id,
        study_site_id=study_site_id,
    )


def list_subject_export_field_groups(*, study_id: int, language_code="en") -> list[dict]:
    return SubjectExportFieldCatalogService().list_groups(
        study_id=study_id,
        language_code=language_code,
    )


def list_event_attestation_policies_for_event(
    *,
    study_id: int,
    study_version: str,
    event_definition_id: int,
    language_code: str | None = None,
) -> list[EventAttestationPolicySnapshot]:
    return StudyEventAttestationPolicyReader().list_enabled_for_event(
        study_id=study_id,
        study_version=study_version,
        event_definition_id=event_definition_id,
        language_code=language_code,
    )


def get_crf_page_lifecycle_steps(*, study_id: int):
    return StudyCrfPageLifecycleService().list_steps(study_id=study_id)


def get_crf_page_lifecycle_action_state(
    *, study_id: int, page_status: str
) -> CrfPageLifecycleActionState:
    return StudyCrfPageLifecycleService().action_state(
        study_id=study_id,
        page_status=page_status,
    )


class EventFormDisplayConfigReader:
    def __init__(self, service=None):
        self.service = service or EventFormDisplayLabelService()

    def get_config(self, *, binding_id: int) -> EventFormDisplayConfigSnapshot | None:
        return self.service.get_config(binding_id=binding_id)

    def map_config_by_binding_ids(
        self,
        *,
        binding_ids: tuple[int, ...],
    ) -> dict[int, EventFormDisplayConfigSnapshot]:
        return self.service.map_config_by_binding_ids(binding_ids=binding_ids)

    def list_binding_choices(self, *, study_id: int):
        return self.service.list_binding_choices(study_id=study_id)


class EventFormDisplayLabelRenderer:
    def __init__(self, service=None):
        self.service = service or EventFormDisplayLabelService()

    def preview(self, **kwargs) -> EventFormDisplayTemplatePreview:
        return self.service.preview(**kwargs)

    def render_label(self, **kwargs) -> str:
        return self.service.render_label(**kwargs)

    def render_label_from_snapshot(self, **kwargs) -> str:
        return self.service.render_label_from_snapshot(**kwargs)

    def render_fallback_label(self, **kwargs) -> str:
        return self.service.render_fallback_label(**kwargs)

    def save_config(self, **kwargs) -> EventFormDisplayConfigSnapshot:
        return self.service.save_config(**kwargs)


class StudyEventFormBindingReader:
    def __init__(self, service=None):
        self.service = service or EventFormDisplayLabelService()

    def get_binding_snapshot(self, *, binding_id: int):
        return self.service.get_binding_snapshot(binding_id=binding_id)


__all__ = [
    "RandomizationSlotAssignment",
    "EnrollSubjectCommand",
    "EligibilityAssessmentPermissionError",
    "EligibilityEnrollmentGateError",
    "FinalizeEligibilityAssessmentCommand",
    "MarkEligibilityStaleOnSourceDataChangeCommand",
    "RecordEventGateEvaluationCommand",
    "RetractEligibilityAssessmentCommand",
    "EventFormDisplayConfigReader",
    "EventFormDisplayConfigSnapshot",
    "EventFormDisplayLabelRenderer",
    "EventFormDisplayLabelValidationError",
    "EventFormDisplayTemplatePreview",
    "EventAttestationPolicySnapshot",
    "CrfPageLifecycleActionState",
    "StudyEventFormBindingReader",
    "assign_randomization_slot_for_subject",
    "build_eligibility_transition_facts",
    "build_randomization_transition_facts",
    "enroll_subject_after_eligibility_gate",
    "finalize_subject_eligibility_assessment",
    "recover_failed_subject_eligibility_assessment",
    "mark_subject_eligibility_stale_on_source_data_change",
    "record_event_gate_evaluation",
    "get_current_subject_treatment",
    "get_crf_page_lifecycle_action_state",
    "get_crf_page_lifecycle_steps",
    "get_subject_identifier_policy",
    "get_subject_treatment_timeline",
    "list_event_attestation_policies_for_event",
    "list_event_gate_evaluation_history_for_subject",
    "list_subject_export_field_groups",
    "randomize_subject",
    "retract_subject_eligibility_assessment",
    "study_site_belongs_to_study",
]

# Confirmed protocol file mappings are consumed through this read boundary.
from apps.study.application.services.file_import_reader import StudyFileImportReader  # noqa: E402,F401
