from django.utils.translation import gettext as _

from apps.datacapture.application.services.check_field_validation_rules import DataCaptureFieldValidationRulesService
from apps.datacapture.infrastructure.repositories import DjangoDataCapturePageRepository


def get_current_validation_failures_for_acknowledgement(
    *, subject_id, visit_id, crf_template_id, event_form_binding_id=None, repository=None,
):
    repository = repository or DjangoDataCapturePageRepository()
    entry = repository.get_current_entry(subject_id=subject_id, visit_id=visit_id,
        crf_template_id=crf_template_id, event_form_binding_id=event_form_binding_id)
    if entry is None:
        raise ValueError(_("The form data is no longer available. Reload before acknowledging."))
    return DataCaptureFieldValidationRulesService(repository).check_field_validation_rules(
        crf_template_id=crf_template_id, payload_data=entry.data,
        subject_id=subject_id, visit_id=visit_id, include_hard=True, evaluate_all=True,
    ).failures
