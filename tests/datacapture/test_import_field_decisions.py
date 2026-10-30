import json
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.datacapture.application.services.mapped_file_import import StaleImportPreviewError
from apps.datacapture.presentation.api.views.file_import import FileImportAPIView, FileImportApplyAPIView
from tests.datacapture import test_file_import_api_contract as api
from tests.datacapture import test_mapped_file_import as mapped


class ImportFieldDecisionsTests(SimpleTestCase):
    def setUp(self):
        self.fixture = mapped.MappedImportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.snapshot["data"].update(RESULT="8", OTHER="9")
        f.snapshot["raw_data"].update(RESULT="8", OTHER="9")
        second = dict(f.field, id="5", field_key="OTHER")
        f.crf.list_template_fields_with_ui_config.side_effect = lambda **kwargs: [f.field, second]
        targets = f.config["confirmation"]["mappings"][0]["targets"]
        targets.append(dict(targets[0], field_id=5, field_key="OTHER"))
        f.reader.catalog.return_value["bindings"][0]["fields"].append({"id": 5, "key": "OTHER"})

    def test_mixed_decisions_only_write_selected_fields_and_audit_both(self):
        f = self.fixture
        result = f.apply(f.preview(), field_decisions={"2:4": "ecrf", "2:5": "file"})
        self.assertEqual(result["applied_fields"], 1)
        self.assertEqual(result["kept_fields"], 1)
        self.assertEqual(json.loads(f.saver.save.call_args.args[0].data),
                         {"RESULT": "8", "OTHER": "5", "UNRELATED": "keep"})
        audit = f.audit.record_event.call_args.kwargs["after_data"]
        self.assertEqual(audit["field_decisions"], {"2:4": "ecrf", "2:5": "file"})
        self.assertEqual([item["key"] for item in audit["changes"]], ["2:5"])
        self.assertEqual([item["key"] for item in audit["kept_changes"]], ["2:4"])

    def test_keep_all_does_not_save_or_create_correction(self):
        f = self.fixture
        f.snapshot["entry_status"] = "submitted"
        result = f.apply(f.preview(), field_decisions={"2:4": "ecrf", "2:5": "ecrf"})
        self.assertEqual(result["applied_fields"], 0)
        self.assertEqual(result["kept_fields"], 2)
        self.assertEqual(result["forms"], [])
        f.saver.save.assert_not_called()
        f.audit.record_event.assert_called_once()

    def test_missing_extra_and_invalid_decisions_rejected_before_writes(self):
        f = self.fixture
        preview = f.preview()
        for decisions in (None, {}, [], True, {"2:4": "file"}, {"2:4": "file", "2:5": "bad"},
                          {"2:4": "file", "2:5": "ecrf", "foreign": "file"}, {"2:4": {}, "2:5": "file"}):
            with self.subTest(decisions=decisions), self.assertRaises(ValueError):
                f.apply(preview, field_decisions=decisions)
        f.saver.save.assert_not_called()
        f.audit.record_event.assert_not_called()

    def test_keep_still_checks_stale_preview(self):
        f = self.fixture
        preview = f.preview()
        f.snapshot["data"]["RESULT"] = "changed"
        with self.assertRaises(StaleImportPreviewError):
            f.apply(preview, field_decisions={"2:4": "ecrf", "2:5": "file"})
        f.saver.save.assert_not_called()

    def test_canonical_data_preserves_kept_field_and_repeat_groups(self):
        f = self.fixture
        history = {"kind": "repeatable", "rows": [{"row_key": "original", "row_no": 1, "items": {"NOTE": "keep"}}]}
        f.snapshot["raw_data"] = {"format": "edc.form_data.v1", "groups": {
            "": {"kind": "single", "items": {"RESULT": "8", "OTHER": "9"}}, "HISTORY": history,
        }}
        f.apply(f.preview(), field_decisions={"2:4": "ecrf", "2:5": "file"})
        saved = json.loads(f.saver.save.call_args.args[0].data)
        self.assertEqual(saved["groups"][""]["items"], {"RESULT": "8", "OTHER": "5"})
        self.assertEqual(saved["groups"]["HISTORY"], history)

    def test_api_forwards_only_decisions_not_client_supplied_values(self):
        fixture = api.FileImportApiContractTests()
        fixture.setUp()
        decisions = {"2:4": "ecrf", "2:5": "file"}
        with patch.object(FileImportAPIView, "_scope", return_value=dict(fixture.scope, actor_user_id=44)), patch(
            api.MODULE + ".inspect_import_preview", return_value={"overwrite_count": 2, "forms": {}},
        ), patch(api.MODULE + ".MappedFileImportService") as service:
            service.return_value.apply.return_value = {"applied_fields": 1}
            response = FileImportApplyAPIView().post(fixture.request({
                "token": "signed", "confirmed": True, "field_decisions": decisions,
                "new_value": "tampered", "confirm_overwrite": True,
            }), **fixture.scope)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.return_value.apply.call_args.kwargs["field_decisions"], decisions)
        self.assertNotIn("new_value", service.return_value.apply.call_args.kwargs)
        self.assertNotIn("confirm_overwrite", service.return_value.apply.call_args.kwargs)
