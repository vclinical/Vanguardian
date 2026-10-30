from apps.datacapture.application import (
    DataCapturePageStateEventTransitionService,
    DataCapturePageStateNotFoundError,
    DataCaptureSaveSubmitPageService,
    DeleteDraftPageCommand,
    SavePageCommand,
    SubmitPageCommand,
    TriggerPageStateEventTransitionCommand,
)
from apps.datacapture.application.services.event_attestation import DataCaptureEventAttestationService
from apps.datacapture.application.services.fact_snapshot import DataCaptureFactSnapshotService
from apps.datacapture.application.services.form_instances import (
    DataCaptureFormInstanceDTO,
    DataCaptureFormInstanceService,
)
from apps.datacapture.application.services.page_entry_read import DataCapturePageEntryReadService
from apps.datacapture.application.services.page_state_audit_history import (
    DataCapturePageStateAuditHistoryService,
)
from apps.datacapture.application.services.page_state_read import DataCapturePageStateReadService
from apps.datacapture.application.services.page_state_write import DataCapturePageStateWriteService
from apps.datacapture.application.services.subject_export import (
    SubjectExportDataService,
)


class DataCaptureFactMappingConfigAdapter:
    def __init__(self, fact_mapping_config_service=None):
        from apps.datacapture.application import DataCaptureFactMappingConfigService

        self.fact_mapping_config_service = (
            fact_mapping_config_service or DataCaptureFactMappingConfigService()
        )

    def upsert_fact_mapping(self, **kwargs):
        return self.fact_mapping_config_service.upsert_fact_mapping(**kwargs)


def trigger_event_transition_for_page_state(
    *,
    page_state_id: int,
    actor_user_id: int | None = None,
    trigger_source: str = "datacapture",
):
    command = TriggerPageStateEventTransitionCommand(
        page_state_id=page_state_id,
        actor_user_id=actor_user_id,
        trigger_source=trigger_source,
    )
    return DataCapturePageStateEventTransitionService().execute(command)


def read_fact_snapshot_for_page_state(*, page_state_id: int):
    return DataCaptureFactSnapshotService().read_for_page_state(page_state_id=page_state_id)


def read_fact_snapshot_for_event_instance(*, event_instance_id: int):
    return DataCaptureFactSnapshotService().read_for_event_instance(event_instance_id=event_instance_id)


def evaluate_facts_for_event_instance(*, event_instance_id: int):
    from apps.datacapture.application.services.fact_evaluation import (
        DataCaptureFactEvaluationService,
    )

    return DataCaptureFactEvaluationService().evaluate_for_event_instance(
        event_instance_id=event_instance_id,
    )


def save_page_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    data: str,
    actor_user_id: int | None = None,
    event_form_binding_id: int | None = None,
):
    return DataCaptureSaveSubmitPageService().save(
        SavePageCommand(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            data=data,
            actor_user_id=actor_user_id,
            event_form_binding_id=event_form_binding_id,
        )
    )


def submit_page_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    data: str,
    actor_user_id: int | None = None,
    event_form_binding_id: int | None = None,
):
    return DataCaptureSaveSubmitPageService().submit(
        SubmitPageCommand(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            data=data,
            actor_user_id=actor_user_id,
            event_form_binding_id=event_form_binding_id,
        )
    )


def delete_latest_draft_page_entry_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    actor_user_id: int | None = None,
    event_form_binding_id: int | None = None,
):
    return DataCaptureSaveSubmitPageService().delete_latest_draft(
        DeleteDraftPageCommand(
            subject_id=subject_id,
            visit_id=visit_id,
            crf_template_id=crf_template_id,
            actor_user_id=actor_user_id,
            event_form_binding_id=event_form_binding_id,
        )
    )


def get_page_state_status_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int | None,
    crf_template_id: int,
    event_form_binding_id: int | None = None,
) -> str:
    """Return ``PageState.status`` for the scope, or empty string if none."""
    if visit_id is None:
        return ""
    return DataCapturePageStateReadService().get_page_state_status(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        event_form_binding_id=event_form_binding_id,
    )


def build_pending_period_data_snapshot(
    *,
    subject_id: int,
    event_instances: tuple[dict, ...],
) -> dict:
    from apps.datacapture.application.services.pending_period_data import (
        PendingPeriodDataSnapshotService,
    )

    return PendingPeriodDataSnapshotService().build_snapshot(
        subject_id=subject_id,
        event_instances=event_instances,
    )


def get_page_state_id_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int | None,
    crf_template_id: int,
    event_form_binding_id: int | None = None,
) -> int | None:
    if visit_id is None:
        return None
    return DataCapturePageStateReadService().get_page_state_id_for_scope(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        event_form_binding_id=event_form_binding_id,
    )


def get_page_state_contexts(*, page_state_ids: list[int] | tuple[int, ...]) -> dict:
    return DataCapturePageStateReadService().get_page_state_contexts(page_state_ids=page_state_ids)


def list_page_state_contexts_for_study_site(*, study_id: int, site_id: int | None = None) -> dict:
    return DataCapturePageStateReadService().list_page_state_contexts_for_study_site(
        study_id=study_id,
        site_id=site_id,
    )


def list_page_state_transition_history_for_subject(
    *,
    subject_id: int,
    limit: int = 200,
    search: str = "",
    field_name: str = "",
) -> list[dict]:
    return DataCapturePageStateAuditHistoryService().list_for_subject(
        subject_id=subject_id,
        limit=limit,
        search=search,
        field_name=field_name,
    )


def read_subject_export_values(
    *,
    study_id: int,
    site_id: int,
    subject_ids: tuple[int, ...],
    field_specs: tuple[dict, ...],
) -> dict:
    return SubjectExportDataService().read_values(
        study_id=study_id,
        site_id=site_id,
        subject_ids=subject_ids,
        field_specs=field_specs,
    )


def get_latest_stable_page_state_id_for_event_instance(*, event_instance_id: int) -> int | None:
    return DataCapturePageStateReadService().get_latest_stable_page_state_id_for_event_instance(
        event_instance_id=event_instance_id,
    )


def get_latest_submitted_or_stable_page_state_id_for_event_instance(*, event_instance_id: int) -> int | None:
    return DataCapturePageStateReadService().get_latest_submitted_or_stable_page_state_id_for_event_instance(
        event_instance_id=event_instance_id,
    )


def event_instance_has_data(*, event_instance_id: int) -> bool:
    return DataCapturePageStateReadService().event_instance_has_data(
        event_instance_id=event_instance_id,
    )


def get_event_attestation_panel_for_event_instance(
    *,
    event_instance_id: int,
    actor_user_id: int | None = None,
    actor_is_superuser: bool = False,
    language_code: str | None = None,
) -> dict:
    return DataCaptureEventAttestationService().get_panel(
        event_instance_id=event_instance_id,
        actor_user_id=actor_user_id,
        actor_is_superuser=actor_is_superuser,
        language_code=language_code,
    )


def attest_event_for_policy(
    *,
    event_instance_id: int,
    attestation_policy_id: int,
    actor_user_id: int,
    actor_is_superuser: bool = False,
    language_code: str | None = None,
    confirmation_accepted: bool = False,
    expected_study_id: int | None = None,
    expected_subject_id: int | None = None,
) -> dict:
    return DataCaptureEventAttestationService().attest_event_for_policy(
        event_instance_id=event_instance_id,
        attestation_policy_id=attestation_policy_id,
        actor_user_id=actor_user_id,
        actor_is_superuser=actor_is_superuser,
        language_code=language_code,
        confirmation_accepted=confirmation_accepted,
        expected_study_id=expected_study_id,
        expected_subject_id=expected_subject_id,
    )


def revoke_event_attestation(
    *,
    event_attestation_id: int,
    actor_user_id: int,
    actor_is_superuser: bool = False,
    reason_text: str,
    expected_study_id: int | None = None,
    expected_subject_id: int | None = None,
) -> dict:
    return DataCaptureEventAttestationService().revoke_event_attestation(
        event_attestation_id=event_attestation_id,
        actor_user_id=actor_user_id,
        actor_is_superuser=actor_is_superuser,
        reason_text=reason_text,
        expected_study_id=expected_study_id,
        expected_subject_id=expected_subject_id,
    )


def invalidate_event_attestations_for_event_instance(
    *,
    event_instance_id: int,
    change_type: str,
    actor_user_id: int | None = None,
    reason_text: str = "",
) -> int:
    return DataCaptureEventAttestationService().invalidate_active_attestations_for_event(
        event_instance_id=event_instance_id,
        change_type=change_type,
        actor_user_id=actor_user_id,
        reason_text=reason_text,
    )


def has_current_event_certification_attestation(*, event_instance_id: int) -> bool:
    return DataCaptureEventAttestationService().has_current_active_certification(
        event_instance_id=event_instance_id,
    )


def get_page_state_final_data_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int | None,
    crf_template_id: int,
    event_form_binding_id: int | None = None,
) -> dict:
    if visit_id is None:
        return {}
    return DataCapturePageStateReadService().get_page_state_final_data_map(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        event_form_binding_id=event_form_binding_id,
    )


def get_latest_page_entry_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int | None,
    crf_template_id: int,
    event_form_binding_id: int | None = None,
):
    if visit_id is None:
        return None
    return DataCapturePageEntryReadService().get_latest_page_entry(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        event_form_binding_id=event_form_binding_id,
    )


def get_latest_submitted_page_entry_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int | None,
    crf_template_id: int,
    event_form_binding_id: int | None = None,
):
    if visit_id is None:
        return None
    return DataCapturePageEntryReadService().get_latest_submitted_page_entry(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        event_form_binding_id=event_form_binding_id,
    )


def get_page_entry_for_subject_visit_crf(
    *,
    page_entry_id: int,
    subject_id: int,
    visit_id: int | None,
    crf_template_id: int,
    event_form_binding_id: int | None = None,
):
    if visit_id is None:
        return None
    return DataCapturePageEntryReadService().get_page_entry(
        page_entry_id=page_entry_id,
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        event_form_binding_id=event_form_binding_id,
    )


def create_form_instance_for_event_binding(
    *,
    subject_id: int,
    visit_id: int,
    event_form_binding_id: int,
    actor_user_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> DataCaptureFormInstanceDTO:
    return DataCaptureFormInstanceService().create_form_instance(
        subject_id=subject_id,
        visit_id=visit_id,
        event_form_binding_id=event_form_binding_id,
        actor_user_id=actor_user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def list_form_instances_for_event_instance(
    *,
    visit_id: int,
    language_code: str | None = None,
) -> list[DataCaptureFormInstanceDTO]:
    return DataCaptureFormInstanceService().list_form_instances_for_event_instance(
        visit_id=visit_id,
        language_code=language_code,
    )


def list_form_instances_for_event_instances(
    *,
    visit_ids: tuple[int, ...],
    language_code: str | None = None,
) -> dict[int, list[DataCaptureFormInstanceDTO]]:
    return DataCaptureFormInstanceService().list_form_instances_for_event_instances(
        visit_ids=visit_ids,
        language_code=language_code,
    )


def merge_form_verification_checked_fields_into_page_state_final_data(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    checked_field_template_ids: list[int],
    unverify_reason_text: str | None = None,
    actor_user_id: int | None = None,
    event_form_binding_id: int | None = None,
) -> tuple[bool, str, list[str], list[int]]:
    from apps.datacapture.application.services.page_state_verification_final_data import (
        DataCapturePageStateVerificationFinalDataService,
    )

    return DataCapturePageStateVerificationFinalDataService().merge_checked_field_template_ids(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        checked_field_template_ids=checked_field_template_ids,
        unverify_reason_text=unverify_reason_text,
        actor_user_id=actor_user_id,
        event_form_binding_id=event_form_binding_id,
    )


def get_verified_or_waived_field_template_ids_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int | None,
    crf_template_id: int,
    event_form_binding_id: int | None = None,
) -> set[int]:
    if visit_id is None:
        return set()
    from apps.datacapture.application.services.page_state_verification_final_data import (
        DataCapturePageStateVerificationFinalDataService,
    )

    return DataCapturePageStateVerificationFinalDataService().list_verified_or_waived_field_template_ids(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        event_form_binding_id=event_form_binding_id,
    )


def get_verified_field_template_ids_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int | None,
    crf_template_id: int,
    event_form_binding_id: int | None = None,
) -> set[int]:
    if visit_id is None:
        return set()
    from apps.datacapture.application.services.page_state_verification_final_data import (
        DataCapturePageStateVerificationFinalDataService,
    )

    return DataCapturePageStateVerificationFinalDataService().list_verified_field_template_ids(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        event_form_binding_id=event_form_binding_id,
    )


def is_field_verified_for_page_state(
    *,
    page_state_id: int,
    field_template_id: int,
) -> bool:
    from apps.datacapture.application.services.page_state_verification_final_data import (
        DataCapturePageStateVerificationFinalDataService,
    )

    return DataCapturePageStateVerificationFinalDataService().is_field_verified_for_page_state(
        page_state_id=page_state_id,
        field_template_id=field_template_id,
    )


def reopen_verified_form_verification_page_state(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    reason_text: str | None,
    actor_user_id: int | None = None,
    event_form_binding_id: int | None = None,
) -> str:
    from apps.datacapture.application.services.page_state_verification_final_data import (
        DataCapturePageStateVerificationFinalDataService,
    )

    return DataCapturePageStateVerificationFinalDataService().reopen_verified_page_state(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        reason_text=reason_text,
        actor_user_id=actor_user_id,
        event_form_binding_id=event_form_binding_id,
    )


def finalize_page_data_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    actor_user_id: int | None = None,
    event_form_binding_id: int | None = None,
) -> str:
    from apps.datacapture.application.services.page_state_verification_final_data import (
        DataCapturePageStateVerificationFinalDataService,
    )

    return DataCapturePageStateVerificationFinalDataService().finalize_page_data(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        actor_user_id=actor_user_id,
        event_form_binding_id=event_form_binding_id,
    )


def certify_page_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    actor_user_id: int | None = None,
    event_form_binding_id: int | None = None,
) -> str:
    from apps.datacapture.application.services.page_state_verification_final_data import (
        DataCapturePageStateVerificationFinalDataService,
    )

    return DataCapturePageStateVerificationFinalDataService().certify_page(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        actor_user_id=actor_user_id,
        event_form_binding_id=event_form_binding_id,
    )

def lock_page_for_subject_visit_crf(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    actor_user_id: int | None = None,
    event_form_binding_id: int | None = None,
) -> str:
    from apps.datacapture.application.services.page_state_verification_final_data import (
        DataCapturePageStateVerificationFinalDataService,
    )

    return DataCapturePageStateVerificationFinalDataService().lock_page(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        actor_user_id=actor_user_id,
        event_form_binding_id=event_form_binding_id,
    )


def ensure_draft_page_state_if_not_exists(
    *,
    subject_id: int,
    visit_id: int,
    crf_template_id: int,
    actor_user_id: int | None = None,
    event_form_binding_id: int | None = None,
) -> bool:
    """Create not-started ``PageState`` when missing. ``final_data`` is populated only in stable statuses."""
    return DataCapturePageStateWriteService().ensure_open_if_not_exists(
        subject_id=subject_id,
        visit_id=visit_id,
        crf_template_id=crf_template_id,
        actor_user_id=actor_user_id,
        event_form_binding_id=event_form_binding_id,
    )


def get_current_validation_failures_for_acknowledgement(**scope):
    from apps.datacapture.application.services.acknowledgement_validation import (
        get_current_validation_failures_for_acknowledgement as evaluate,
    )

    return evaluate(**scope)


__all__ = [
    "get_current_validation_failures_for_acknowledgement",
    "DataCaptureFormInstanceDTO",
    "DataCapturePageStateNotFoundError",
    "attest_event_for_policy",
    "create_form_instance_for_event_binding",
    "delete_latest_draft_page_entry_for_subject_visit_crf",
    "ensure_draft_page_state_if_not_exists",
    "event_instance_has_data",
    "evaluate_facts_for_event_instance",
    "finalize_page_data_for_subject_visit_crf",
    "certify_page_for_subject_visit_crf",
    "get_event_attestation_panel_for_event_instance",
    "get_latest_page_entry_for_subject_visit_crf",
    "get_latest_submitted_page_entry_for_subject_visit_crf",
    "get_latest_submitted_or_stable_page_state_id_for_event_instance",
    "get_latest_stable_page_state_id_for_event_instance",
    "get_page_entry_for_subject_visit_crf",
    "get_page_state_final_data_for_subject_visit_crf",
    "get_page_state_id_for_subject_visit_crf",
    "get_page_state_status_for_subject_visit_crf",
    "build_pending_period_data_snapshot",
    "get_verified_field_template_ids_for_subject_visit_crf",
    "get_verified_or_waived_field_template_ids_for_subject_visit_crf",
    "has_current_event_certification_attestation",
    "invalidate_event_attestations_for_event_instance",
    "is_field_verified_for_page_state",
    "list_form_instances_for_event_instance",
    "list_form_instances_for_event_instances",
    "lock_page_for_subject_visit_crf",
    "merge_form_verification_checked_fields_into_page_state_final_data",
    "read_fact_snapshot_for_page_state",
    "read_fact_snapshot_for_event_instance",
    "read_subject_export_values",
    "reopen_verified_form_verification_page_state",
    "revoke_event_attestation",
    "save_page_for_subject_visit_crf",
    "submit_page_for_subject_visit_crf",
    "trigger_event_transition_for_page_state",
]
