import json
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase
from django.utils.translation import override

from apps.crf.application.services.crf_template_query import CrfTemplateQueryService
from apps.datacapture.application.services.file_import_values import convert_value
from tests.datacapture import test_mapped_file_import as mapped


class BilingualFileImportTests(SimpleTestCase):
    def schema(self, language):
        translations = [SimpleNamespace(language_code=code, options=json.dumps({
            "source": "static", "static": [
                {"value": "0", "label": negative}, {"value": "1", "label": positive},
            ],
        })) for code, negative, positive in (
            ("en", "Negative", "Positive"), ("vi", "Âm tính", "Dương tính"),
        )]
        field = SimpleNamespace(pk=4, field_key="RESULT", label="Result", data_type="STRING",
                                display_order=1, section_template=None,
                                translations=SimpleNamespace(all=lambda: []))
        ui = SimpleNamespace(field_template_id=4, control_type="RADIO", control_layout=None,
                             layout=None, behavior=None, style=None, classes=None,
                             translations=SimpleNamespace(all=lambda: translations))
        repository = Mock()
        repository.list_template_fields_with_related_config.return_value = [field]
        repository.list_field_definitions_by_field_template_ids.return_value = []
        repository.list_field_ui_configs_by_field_template_ids.return_value = [ui]
        with override(language):
            return CrfTemplateQueryService(repository=repository).list_template_fields_with_ui_config(template_id=8)[0]

    def test_schema_and_conversion_accept_both_languages_in_either_ui_language(self):
        for language in ("en", "vi"):
            field = self.schema(language)
            self.assertEqual(set(field["ui_config"]["options_translations"]), {"en", "vi"})
            for source, expected in (("Âm tính", "0"), ("Negative", "0"), ("0", "0"),
                                     ("Dương tính", "1"), ("POSITIVE", "1"), ("1", "1")):
                for value in (source, f"0.56 ({source})"):
                    with self.subTest(language=language, value=value), override(language):
                        self.assertEqual(convert_value(value, field), expected)

    def test_ambiguous_translations_and_unapproved_values_do_not_match(self):
        field = self.schema("en")
        field["ui_config"]["options_translations"]["vi"] = [
            {"value": "1", "label": "Negative"}, {"value": "99", "label": "Unknown"},
        ]
        for source in ("Negative", "0.56 (Negative)", "Unknown", "0.56 (Unknown)"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                convert_value(source, field)

    def test_text_keeps_source_whitespace_language_and_parentheses(self):
        source = " 0.56 (Âm tính)\nPositive \t"
        for kind in ("TEXT", "STRING"):
            self.assertEqual(convert_value(source, {"data_type": kind}), source)
            with self.assertRaises(ValueError):
                convert_value(source, {"data_type": kind, "text_max_length": len(source) - 1})

    def test_preview_apply_accepts_other_language_and_preserves_text(self):
        for language in ("en", "vi"):
            for kind in ("choice", "text"):
                with self.subTest(language=language, kind=kind), override(language):
                    fixture = mapped.MappedImportTests()
                    fixture.setUp()
                    try:
                        fixture.field = self.schema(language) if kind == "choice" else dict(fixture.field, data_type="TEXT")
                        source = " 0.56 (Âm tính) " if language == "en" else " 0.56 (Negative) "
                        expected = "0" if kind == "choice" else source
                        preview = fixture.preview(content=mapped.workbook(source))
                        self.assertTrue(preview["can_apply"])
                        self.assertEqual(preview["items"][0]["new_value"], expected)
                        self.assertEqual(fixture.apply(preview)["applied_fields"], 1)
                        self.assertEqual(json.loads(fixture.saver.save.call_args.args[0].data)["RESULT"], expected)
                    finally:
                        fixture.doCleanups()
