"""Evaluate imported drafts using the same rule evaluator as interactive capture."""
import json

from django.utils.translation import gettext as _

from apps.core.form_data_document import flatten_form_data_for_export, normalize_form_data
from apps.datacapture.application.services.check_field_validation_rules import (
    RULE_TYPE_CUSTOM_EXPRESSION,
    RULE_TYPE_REQUIRED,
    _extract_referenced_form_codes,
    _field_validation_failure,
    _normalize_rule_check,
)
from apps.datacapture.infrastructure.repositories import DjangoDataCapturePageRepository
from apps.reconcile.application import ReconcileDataQueryWriteService


class ImportValidationService:
    def __init__(self, repository=None, reconcile=None):
        self.repository = repository or DjangoDataCapturePageRepository()
        self.reconcile = reconcile or ReconcileDataQueryWriteService()

    def validate(self, *, forms, subject_id, visit_id, actor_user_id):
        # All imported forms have been saved before evaluation. Overlay their draft
        # values because normal cross-form context reads the submitted snapshot.
        imported_context = {}
        for form in forms:
            entry = self.repository.get_page_entry_by_id(
                page_entry_id=form["entry_id"], subject_id=subject_id, visit_id=visit_id,
                crf_template_id=form["form_id"], event_form_binding_id=form["binding_id"],
            )
            if entry is None:
                raise ValueError(_("The imported draft is no longer available for validation."))
            values = flatten_form_data_for_export(normalize_form_data(json.loads(entry.data), strict=False))
            imported_context[form["form_code"]] = {"is_repeatable": False, "rows": [values]}
        results = []
        for form in forms:
            page_state = self.repository.get_page_state_by_scope(
                subject_id=subject_id, visit_id=visit_id, crf_template_id=form["form_id"],
                event_form_binding_id=form["binding_id"],
            )
            rules = self.repository.list_form_field_validation_rules(crf_template_id=form["form_id"], include_hard=True)
            context = self.repository.get_subject_cross_form_validation_context(
                subject_id=subject_id, form_codes=_extract_referenced_form_codes(rules),
                exclude_visit_id=visit_id, exclude_crf_template_id=form["form_id"],
            )
            context.update(imported_context)
            values = imported_context[form["form_code"]]["rows"][0]
            failures, passed_rules = [], []
            for field_key, field_rules in rules.items():
                for rule in field_rules:
                    check = _normalize_rule_check(rule)
                    if check.rule_type not in {RULE_TYPE_REQUIRED, RULE_TYPE_CUSTOM_EXPRESSION}:
                        continue
                    if check.rule_type == RULE_TYPE_CUSTOM_EXPRESSION and (
                        not check.expression.strip() or field_key not in values
                    ):
                        continue
                    failure = _field_validation_failure(
                        field_key=field_key, rule=rule, value=values.get(field_key),
                        payload_map=values, form_context=context,
                    )
                    if failure is not None:
                        failures.append(failure)
                    elif check.id is not None:
                        passed_rules.append({"rule_id": check.id, "field_template_id": check.field_template_id,
                                             "field_key": field_key})
            results.append(self.reconcile.reconcile_import_validation(
                page_state_id=page_state.id, crf_template_id=form["form_id"], entry_id=form["entry_id"],
                data_version=int(page_state.data_version or 0), failures=failures, passed_rules=passed_rules,
                evaluated_values_json=values, actor_user_id=actor_user_id,
            ))
        return results
