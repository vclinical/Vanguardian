import json

from django.test import SimpleTestCase

from apps.datacapture.application.services.file_import_values import convert_value
from tests.datacapture import test_mapped_file_import as mapped


class CombinedChoiceComponentTests(SimpleTestCase):
    def field(self, control):
        return {"data_type": "STRING", "ui_config": {
            "control_type": control,
            "options": [{"value": "0", "label": "Negative"}, {"value": "1", "label": "Positive"}],
            "options_translations": {"vi": [
                {"value": "0", "label": "Âm tính"}, {"value": "1", "label": "Dương tính"},
            ]},
        }}

    def test_radio_and_checkbox_match_either_component(self):
        for control in ("RADIO", "CHECKBOX"):
            for source, expected in (("0 (Unknown)", "0"), ("1.0 (Unknown)", "1"),
                                     ("0.56 (Âm tính)", "0"), ("0.9 (Positive)", "1"),
                                     ("0 (Negative)", "0"), ("1 (Dương tính)", "1")):
                with self.subTest(control=control, source=source):
                    self.assertEqual(convert_value(source, self.field(control)), expected)

    def test_numeric_component_can_match_option_label(self):
        field = {"ui_config": {"control_type": "RADIO", "options": [{"value": "grade", "label": "0.50"}]}}
        self.assertEqual(convert_value("0.5 (Unknown)", field), "grade")

    def test_conflicting_components_require_confirmation(self):
        for control in ("RADIO", "CHECKBOX"):
            field = self.field(control)
            for source in ("0 (Positive)", "1 (Âm tính)", "2 (Unknown)"):
                with self.subTest(control=control, source=source), self.assertRaises(ValueError):
                    convert_value(source, field)
            self.assertEqual(convert_value("0 (Positive)", field, override="1"), "1")

    def test_numeric_and_freetext_targets_keep_their_rules(self):
        source = " 0.56 (Âm tính)\n"
        self.assertEqual(convert_value(source, {"data_type": "DECIMAL"}), "0.56")
        self.assertEqual(convert_value(source, {"data_type": "TEXT"}), source)

    def test_preview_apply_saves_matched_choice_for_radio_and_checkbox(self):
        for control in ("RADIO", "CHECKBOX"):
            with self.subTest(control=control):
                fixture = mapped.MappedImportTests()
                fixture.setUp()
                try:
                    fixture.field.update(self.field(control))
                    fixture.snapshot["data"]["RESULT"] = "1"
                    fixture.snapshot["raw_data"]["RESULT"] = "1"
                    preview = fixture.preview(content=mapped.workbook("0 (Unknown)"))
                    self.assertTrue(preview["can_apply"])
                    self.assertEqual(preview["items"][0]["new_value"], "0")
                    with self.assertRaises(ValueError):
                        fixture.apply(preview)
                    self.assertEqual(fixture.apply(preview, confirm_overwrite=True)["applied_fields"], 1)
                    self.assertEqual(json.loads(fixture.saver.save.call_args.args[0].data)["RESULT"], "0")
                finally:
                    fixture.doCleanups()
