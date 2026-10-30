import json

from django.test import SimpleTestCase, override_settings

from apps.datacapture.application.services.file_import_values import convert_value
from tests.datacapture import test_mapped_file_import as mapped

OPTIONS = [{"value": "0", "label": "Âm tính"}, {"value": "1", "label": "Dương tính"}]
CHOICE = {"data_type": "INTEGER", "ui_config": {"control_type": "RADIO", "options": OPTIONS}}


@override_settings(LANGUAGE_CODE="en")
class CombinedFileImportTests(SimpleTestCase):
    def test_target_schema_selects_numeric_or_qualitative_component(self):
        for source, number, choice in (
            ("0.56 (Âm tính)", "0.56", "0"),
            ("0.9(Dương tính)", "0.9", "1"),
            (" 0 ( Âm tính ) ", "0", "0"),
            ("1.2 (0)", "1.2", "0"),
        ):
            with self.subTest(source=source):
                self.assertEqual(convert_value(source, {"data_type": "DECIMAL"}), number)
                self.assertEqual(convert_value(source, CHOICE), choice)
                self.assertEqual(convert_value(source, {"data_type": "TEXT"}), source)

    def test_scalar_values_and_full_choice_labels_keep_existing_behavior(self):
        self.assertEqual(convert_value("0.56", {"data_type": "DECIMAL"}), "0.56")
        self.assertEqual(convert_value("Âm tính", CHOICE), "0")
        field = {"ui_config": {"options": OPTIONS + [{"value": "full", "label": "0.56 (Âm tính)"}]}}
        self.assertEqual(convert_value("0.56 (Âm tính)", field), "full")

    def test_invalid_combined_values_are_not_partially_imported(self):
        for source in ("0.56 ()", "0.56 (Âm tính", "0.56 (Âm tính) extra", "abc (Âm tính)", "NaN (Âm tính)"):
            for field in ({"data_type": "DECIMAL"}, CHOICE):
                with self.subTest(source=source, field=field), self.assertRaises(ValueError):
                    convert_value(source, field)
        with self.assertRaises(ValueError):
            convert_value("0.56 (Âm tính)", {"data_type": "INTEGER"})

    def test_unknown_and_ambiguous_qualitative_values_require_resolution(self):
        with self.assertRaises(ValueError):
            convert_value("0.56 (Unknown)", CHOICE)
        ambiguous = {"ui_config": {"options": OPTIONS + [{"value": "other", "label": "Âm tính"}]}}
        with self.assertRaises(ValueError):
            convert_value("0.56 (Âm tính)", ambiguous)
        self.assertEqual(convert_value("0.56 (Unknown)", CHOICE, override="0"), "0")
        with self.assertRaises(ValueError):
            convert_value("0.56 (Unknown)", CHOICE, override="invalid")

    def test_preview_and_apply_one_column_to_numeric_and_choice_fields(self):
        fixture = mapped.MappedImportTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        qualitative = dict(fixture.field, id="5", field_key="HIV_STATUS", **CHOICE)
        fixture.crf.list_template_fields_with_ui_config.side_effect = lambda **kwargs: [fixture.field, qualitative]
        targets = fixture.config["confirmation"]["mappings"][0]["targets"]
        targets.append(dict(targets[0], field_id=5, field_key="HIV_STATUS"))
        fixture.reader.catalog.return_value["bindings"][0]["fields"].append({"id": 5, "key": "HIV_STATUS"})
        source = "0.56 (Âm tính)"
        preview = fixture.preview(content=mapped.workbook(source))
        self.assertTrue(preview["can_apply"])
        fixture.saver.save.assert_not_called()
        self.assertEqual([item["new_value"] for item in preview["items"][:2]], ["0.56", "0"])
        self.assertTrue(all(item["source_value"] == source for item in preview["items"][:2]))
        self.assertEqual(fixture.apply(preview)["applied_fields"], 2)
        self.assertEqual(json.loads(fixture.saver.save.call_args.args[0].data),
                         {"RESULT": "0.56", "HIV_STATUS": "0", "UNRELATED": "keep"})
        changes = fixture.audit.record_event.call_args.kwargs["after_data"]["changes"]
        self.assertTrue(all(change["source_value"] == source for change in changes))
