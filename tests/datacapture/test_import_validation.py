import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from tests.datacapture import test_mapped_file_import as mapped


class ImportValidationOrchestrationTests(SimpleTestCase):
    def setUp(self):
        self.fixture = mapped.MappedImportTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.validation = Mock()
        self.fixture.service.validation = self.validation

    def test_validation_runs_once_after_all_form_saves_before_import_audit(self):
        f = self.fixture
        targets = f.config["confirmation"]["mappings"][0]["targets"]
        targets.append(dict(targets[0], binding_id=3, form_id=9))
        f.reader.catalog.return_value["bindings"].append({
            "id": 3, "form_id": 9, "visit_id": 30, "study_version": "1", "fields": [{"id": 4, "key": "RESULT"}],
        })
        calls = []

        def save(command):
            calls.append(("save", command.crf_template_id))
            return SimpleNamespace(entry_id=command.crf_template_id + 100)

        f.saver.save.side_effect = save
        self.validation.validate.side_effect = lambda **kwargs: calls.append(("validate", kwargs))
        f.audit.record_event.side_effect = lambda **kwargs: calls.append(("audit", kwargs))
        preview = f.preview()
        self.validation.validate.assert_not_called()
        f.apply(preview)
        self.assertEqual([kind for kind, _ in calls], ["save", "save", "validate", "audit"])
        self.validation.validate.assert_called_once()
        validation = self.validation.validate.call_args.kwargs
        self.assertEqual({key: validation[key] for key in ("subject_id", "visit_id", "actor_user_id")},
                         {"subject_id": 10, "visit_id": 20, "actor_user_id": 7})
        self.assertEqual([(form["form_id"], form["binding_id"], form["entry_id"]) for form in validation["forms"]],
                         [(8, 2, 108), (9, 3, 109)])
        for saved in f.saver.save.call_args_list:
            self.assertEqual(json.loads(saved.args[0].data), {"UNRELATED": "keep", "RESULT": "5"})

    def test_keep_all_does_not_run_validation(self):
        f = self.fixture
        f.snapshot["data"]["RESULT"] = "8"
        f.snapshot["raw_data"]["RESULT"] = "8"
        result = f.apply(f.preview(), field_decisions={"2:4": "ecrf"})
        self.assertEqual(result["applied_fields"], 0)
        f.saver.save.assert_not_called()
        self.validation.validate.assert_not_called()

    def test_validation_failure_propagates_through_atomic_scope_and_does_not_audit_success(self):
        f = self.fixture
        saved = []
        transaction_events = []

        @contextmanager
        def atomic(*args):
            transaction_events.append("begin")
            try:
                yield
            except Exception:
                saved.clear()
                transaction_events.append("rollback")
                raise
            else:
                transaction_events.append("commit")

        def save(command):
            saved.append(command)
            return SimpleNamespace(entry_id=99)

        f.capture.atomic_scope = atomic
        f.saver.save.side_effect = save
        self.validation.validate.side_effect = RuntimeError("validation unavailable")
        with self.assertRaisesMessage(RuntimeError, "validation unavailable"):
            f.apply(f.preview())
        self.assertEqual(transaction_events, ["begin", "rollback"])
        self.assertEqual(saved, [])
        f.audit.record_event.assert_not_called()
