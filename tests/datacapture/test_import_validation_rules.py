import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.datacapture.application.services.import_validation import ImportValidationService
from apps.reconcile.application import ReconcileDataQueryWriteService
from apps.reconcile.infrastructure.repositories.dataquery_write import DjangoReconcileDataQueryWriteRepository


def rule(rule_id=41, mode="QUERY", expression="num($val) > 10", **kwargs):
    return dict(id=rule_id, field_template_id=4, rule_type="CUSTOM_EXPRESSION", mode=mode,
                severity="error", expression=expression, message="Result exceeds range", **kwargs)


class ImportValidationRulesTests(SimpleTestCase):
    def setUp(self):
        self.capture = Mock()
        self.capture.get_page_state_by_scope.return_value = SimpleNamespace(id=12, data_version=3)
        self.capture.get_subject_cross_form_validation_context.return_value = {}
        self.capture.list_form_field_validation_rules.return_value = {"RESULT": [rule()]}
        self.queries = []
        self.repository = Mock()
        self.repository.list_field_key_to_id.return_value = {"RESULT": 4}
        self.repository.create_validation_run.return_value = SimpleNamespace(pk=81)
        self.repository.bulk_create_soft_validation_issues.return_value = 0
        self.repository.list_active_system_validation_queries.side_effect = lambda **kwargs: [
            dict(query) for query in self.queries if query["source"] == "system" and query["status"] != "closed"
        ]
        self.repository.create_validation_open_query.side_effect = self.open_query
        self.repository.close_passing_system_validation_query.side_effect = self.close_query
        self.service = ImportValidationService(repository=self.capture,
            reconcile=ReconcileDataQueryWriteService(repository=self.repository))
        audit_patch = patch("apps.audit.public.AuditContextAdapter")
        self.audit = audit_patch.start().return_value
        self.addCleanup(audit_patch.stop)
        self.audit.record_event.return_value = SimpleNamespace(pk=91)

    def open_query(self, **kwargs):
        query_id = len(self.queries) + 1
        self.queries.append(dict(id=query_id, source="system", status="open",
            field_template_id=kwargs["field_template_id"], validation_rule_id=kwargs["validation_rule_id"]))
        return SimpleNamespace(pk=query_id)

    def close_query(self, *, dataquery_id, **kwargs):
        query = next(item for item in self.queries if item["id"] == dataquery_id)
        query["status"] = "closed"
        return True

    def validate(self, value):
        self.capture.get_page_entry_by_id.return_value = SimpleNamespace(data=json.dumps({"RESULT": value}))
        return self.service.validate(forms=[dict(form_id=8, binding_id=2, entry_id=99, form_code="LAB",
            data=json.dumps({"RESULT": value}))], subject_id=10, visit_id=20, actor_user_id=7)

    def test_out_of_range_import_opens_query_with_current_draft_and_audit(self):
        result = self.validate("12")
        self.assertEqual(result[0]["query_count"], 1)
        created = self.repository.create_validation_open_query.call_args.kwargs
        self.assertEqual((created["page_state_id"], created["field_template_id"], created["validation_rule_id"],
                          created["page_entry_id"]), (12, 4, 41, 99))
        self.assertEqual(self.repository.create_validation_run.call_args.kwargs["source"], "FILE_IMPORT")
        self.assertEqual(self.repository.create_validation_run.call_args.kwargs["related_audit_event_id"], 91)
        self.capture.list_form_field_validation_rules.assert_called_once_with(crf_template_id=8, include_hard=True)
        audit_events = [call.kwargs for call in self.audit.record_event.call_args_list]
        self.assertEqual([item["action"] for item in audit_events], ["file_import_validation", "query_open"])
        self.assertEqual(audit_events[0]["after_data"]["failures"][0]["failed_value"], "12")
        self.assertEqual(audit_events[1]["after_data"]["value"], "12")

    def test_still_failing_stays_open_then_correction_closes_only_matching_system_rule(self):
        self.validate("12")
        self.queries.extend([
            dict(id=2, source="manual", status="open", field_template_id=4, validation_rule_id=41),
            dict(id=3, source="system", status="open", field_template_id=4, validation_rule_id=999),
        ])
        failing = self.validate("13")[0]
        self.assertEqual((failing["query_count"], failing["closed_query_count"]), (0, 0))
        self.repository.close_passing_system_validation_query.assert_not_called()
        corrected = self.validate("5")[0]
        self.assertEqual(corrected["closed_query_count"], 1)
        self.assertEqual([query["status"] for query in self.queries], ["closed", "open", "open"])
        self.assertEqual(self.repository.close_passing_system_validation_query.call_args.kwargs["dataquery_id"], 1)
        close_audit = self.audit.record_event.call_args.kwargs
        self.assertEqual(close_audit["action"], "query_close")
        self.assertEqual(close_audit["after_data"]["values"], {"RESULT": "5"})
        thread = self.repository.create_query_thread_message.call_args.kwargs
        self.assertEqual((thread["source"], thread["message_type"]), ("system", "status_change"))

    def test_all_rules_evaluated_after_first_failure_and_unknown_rules_never_pass(self):
        unsupported = dict(rule(44), rule_type="UNSUPPORTED")
        self.capture.list_form_field_validation_rules.return_value = {
            "RESULT": [rule(41), rule(42, expression="num($val) > 11"), rule(43, expression=""), unsupported],
        }
        self.queries.extend(dict(id=index, source="system", status="open", field_template_id=4,
                                 validation_rule_id=rule_id) for index, rule_id in enumerate((42, 43, 44), start=1))
        self.validate("12")
        validation_audit = self.audit.record_event.call_args_list[0].kwargs["after_data"]
        self.assertEqual([failure["rule_id"] for failure in validation_audit["failures"]], [41, 42])
        self.assertEqual(validation_audit["passed_rules"], [])
        self.repository.close_passing_system_validation_query.assert_not_called()

    def test_hard_and_soft_failures_keep_draft_value_version_and_validation_run_for_snapshots(self):
        self.capture.list_form_field_validation_rules.return_value = {"RESULT": [rule(41, "HARD"), rule(42, "SOFT")]}
        self.validate("12")
        snapshot = self.repository.bulk_create_soft_validation_issues.call_args.kwargs
        self.assertEqual([item["mode"] for item in snapshot["items"]], ["HARD", "SOFT"])
        self.assertEqual([item["failed_value"] for item in snapshot["items"]], ["12", "12"])
        self.assertEqual(snapshot["evaluated_values_by_field_template_id"], {4: "12"})
        self.assertEqual((snapshot["page_entry_id"], snapshot["data_version"], snapshot["validation_run_id"],
                          snapshot["related_audit_event_id"]), (99, 3, 81, 91))
        self.assertEqual(snapshot["passed_rule_ids"], set())
        self.validate("5")
        snapshot = self.repository.bulk_create_soft_validation_issues.call_args.kwargs
        self.assertEqual(snapshot["items"], [])
        self.assertEqual(snapshot["passed_rule_ids"], {41, 42})
        self.repository.create_validation_open_query.assert_not_called()

    def test_cross_form_expression_uses_new_imported_draft_not_old_submitted_value(self):
        self.capture.get_page_entry_by_id.side_effect = [
            SimpleNamespace(data='{"RESULT": "12"}'), SimpleNamespace(data='{"LIMIT": "10"}'),
        ]
        self.capture.list_form_field_validation_rules.return_value = {
            "RESULT": [rule(expression="num($val) > num($form.OTHER.LIMIT)")],
        }
        self.capture.get_subject_cross_form_validation_context.return_value = {
            "OTHER": {"is_repeatable": False, "rows": [{"LIMIT": "20"}]},
        }
        result = self.service.validate(forms=[
            dict(form_id=8, binding_id=2, entry_id=99, form_code="LAB", data='{"RESULT": "12"}'),
            dict(form_id=9, binding_id=3, entry_id=100, form_code="OTHER", data='{"LIMIT": "10"}'),
        ], subject_id=10, visit_id=20, actor_user_id=7)
        self.assertEqual(result[0]["query_count"], 1)
        self.assertEqual(self.repository.create_validation_open_query.call_args_list[0].kwargs["page_entry_id"], 99)

    def test_evaluates_persisted_value_not_pre_save_payload(self):
        self.capture.get_page_entry_by_id.return_value = SimpleNamespace(data='{"RESULT": "12"}')
        result = self.service.validate(forms=[dict(form_id=8, binding_id=2, entry_id=99, form_code="LAB",
            data='{"RESULT": "5"}')], subject_id=10, visit_id=20, actor_user_id=7)
        self.assertEqual(result[0]["query_count"], 1)
        self.capture.get_page_entry_by_id.assert_called_once_with(page_entry_id=99, subject_id=10, visit_id=20,
            crf_template_id=8, event_form_binding_id=2)
        audit = self.audit.record_event.call_args_list[0].kwargs["after_data"]
        self.assertEqual(audit["values"], {"RESULT": "12"})

    def test_missing_saved_entry_aborts_without_validation_or_query_writes(self):
        self.capture.get_page_entry_by_id.return_value = None
        with self.assertRaises(ValueError):
            self.service.validate(forms=[dict(form_id=8, binding_id=2, entry_id=99, form_code="LAB",
                data='{"RESULT": "5"}')], subject_id=10, visit_id=20, actor_user_id=7)
        self.repository.create_validation_run.assert_not_called()
        self.repository.create_validation_open_query.assert_not_called()
        self.audit.record_event.assert_not_called()


class ImportValidationQueryScopeTests(SimpleTestCase):
    def test_read_and_close_repository_boundaries_exclude_manual_nonvalidation_and_terminal_queries(self):
        with patch("apps.reconcile.infrastructure.repositories.dataquery_write.ReconcileDataQuery") as model:
            model.objects.filter.return_value.values.return_value = []
            DjangoReconcileDataQueryWriteRepository.list_active_system_validation_queries(page_state_id=12)
            read_filter = model.objects.filter.call_args.kwargs
            DjangoReconcileDataQueryWriteRepository.close_passing_system_validation_query(
                dataquery_id=1, page_state_id=12, actor_user_id=7, now=timezone.now())
            write_filter = model.objects.filter.call_args.kwargs
        for scope in (read_filter, write_filter):
            self.assertEqual(scope["source"], "system")
            self.assertEqual(scope["query_type"], "validation")
            self.assertEqual(scope["page_state_id"], 12)
            self.assertFalse(scope["deleted"])
            self.assertEqual(set(scope["status__in"]), {"open", "answered", "resolved"})
        self.assertEqual(write_filter["pk"], 1)


class ImportValidationSnapshotTests(SimpleTestCase):
    def test_only_explicitly_passed_rule_gets_pass_snapshot_and_correction(self):
        repository = DjangoReconcileDataQueryWriteRepository()
        issues = [SimpleNamespace(pk=rule_id + 100, rule_id=rule_id, field_instance_id=None,
                  rule=SimpleNamespace(field_template_id=4), status="ACKNOWLEDGEMENT_REQUIRED",
                  message="Range", severity="warning", save=Mock()) for rule_id in (41, 42)]
        signatures = {repository._validation_issue_signature(rule_id=item.rule_id, field_template_id=4,
                      field_instance_id=None): item for item in issues}
        with patch.object(repository.__class__, "_list_reusable_validation_issues_by_signature", return_value=signatures), patch(
            "apps.reconcile.infrastructure.repositories.dataquery_write.ReconcileValidationIssueSnapshot.objects.create",
        ) as snapshots:
            repository.bulk_create_soft_validation_issues(page_state_id=12, items=[], actor_user_id=7,
                now=timezone.now(), validation_run_id=81, evaluated_values_by_field_template_id={4: "5"},
                data_version=3, related_audit_event_id=91, page_entry_id=99, passed_rule_ids={41})
        snapshots.assert_called_once()
        snapshot = snapshots.call_args.kwargs
        self.assertEqual((snapshot["validation_issue_id"], snapshot["result"], snapshot["evaluated_values_json"]),
                         (141, "PASS", "5"))
        self.assertEqual((snapshot["validation_run_id"], snapshot["data_version"], snapshot["related_audit_event_id"]),
                         (81, 3, 91))
        self.assertEqual([item.status for item in issues], ["CORRECTED", "ACKNOWLEDGEMENT_REQUIRED"])
        issues[1].save.assert_not_called()

    def test_failure_snapshot_uses_imported_draft_field_entry_and_value(self):
        repository = DjangoReconcileDataQueryWriteRepository()
        with patch.object(repository.__class__, "_list_reusable_validation_issues_by_signature", return_value={}), patch.object(
            repository.__class__, "get_current_field_entry_id", return_value=401,
        ) as field_entry, patch(
            "apps.reconcile.infrastructure.repositories.dataquery_write.ReconcileValidationIssue.objects.create",
            return_value=SimpleNamespace(pk=141, rule_id=41),
        ) as issue, patch(
            "apps.reconcile.infrastructure.repositories.dataquery_write.ReconcileValidationIssueSnapshot.objects.create",
        ) as snapshots:
            repository.bulk_create_soft_validation_issues(page_state_id=12,
                items=[dict(rule_id=41, field_template_id=4, mode="HARD", severity="error", message="Range", failed_value="12")],
                actor_user_id=7, now=timezone.now(), validation_run_id=81,
                evaluated_values_by_field_template_id={4: "12"}, data_version=3,
                related_audit_event_id=91, page_entry_id=99, passed_rule_ids=set())
        field_entry.assert_called_once_with(page_state_id=12, field_template_id=4, page_entry_id=99)
        self.assertEqual(issue.call_args.kwargs["field_instance_id"], 401)
        self.assertEqual(issue.call_args.kwargs["mode"], "HARD")
        snapshot = snapshots.call_args.kwargs
        self.assertEqual((snapshot["result"], snapshot["evaluated_values_json"], snapshot["related_audit_event_id"]),
                         ("FAIL", "12", 91))
