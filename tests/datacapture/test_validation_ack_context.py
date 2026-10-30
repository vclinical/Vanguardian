from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.datacapture.application.services.check_field_validation_rules import _field_validation_failure
from apps.reconcile.infrastructure.repositories.dataquery_write import DjangoReconcileDataQueryWriteRepository


class ValidationAcknowledgementContextTests(SimpleTestCase):
    def test_fingerprint_tracks_referenced_values_and_rule_but_not_unrelated_fields(self):
        rule = dict(id=1, field_template_id=2, rule_type="CUSTOM_EXPRESSION", mode="SOFT", severity="MAJOR",
                    expression="num($val) > num($field.LIMIT) and num($form.LAB.ALT) > 0", message="Out of range")
        def context(value="405", limit="390", alt="52", unrelated="a", expression=None):
            failure = _field_validation_failure(field_key="QT", rule=rule | ({"expression": expression} if expression else {}),
                value=value, payload_map={"LIMIT": limit, "COMMENT": unrelated},
                form_context={"LAB": {"is_repeatable": False, "rows": [{"ALT": alt}]}})
            return failure.evaluation_context
        original = context()
        self.assertEqual(original, context(unrelated="changed"))
        for changed in (context(value="410"), context(limit="391"), context(alt="53"),
                        context(expression=rule["expression"] + " and True")):
            self.assertNotEqual(original, changed)

    def test_only_current_acknowledged_issue_with_matching_context_is_preserved(self):
        issue = SimpleNamespace(pk=301, status="ACKNOWLEDGED")
        module = "apps.reconcile.infrastructure.repositories.dataquery_write.ReconcileValidationIssueSnapshot"
        with patch(module) as snapshots:
            latest = snapshots.objects.filter.return_value.order_by.return_value.values_list.return_value
            latest.first.return_value = {"value": "405", "_validation_context": "same"}
            self.assertTrue(DjangoReconcileDataQueryWriteRepository._acknowledgement_matches(issue, "same"))
            self.assertFalse(DjangoReconcileDataQueryWriteRepository._acknowledgement_matches(issue, "changed"))
            latest.first.return_value = "405"
            self.assertFalse(DjangoReconcileDataQueryWriteRepository._acknowledgement_matches(issue, "same"))
            issue.status = "ACKNOWLEDGEMENT_REQUIRED"
            self.assertFalse(DjangoReconcileDataQueryWriteRepository._acknowledgement_matches(issue, "same"))
