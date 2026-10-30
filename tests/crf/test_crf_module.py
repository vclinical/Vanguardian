from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase

from apps.crf.application.commands import UpsertCrfTemplateCommand
from apps.crf.application.form_builder_orchestration import (
    FormBuilderOrchestrationService,
    SaveFieldAggregateCommand,
)
from apps.crf.application.services import CrfTemplateCommandService
from apps.crf.application.services.crf_template_query import CrfTemplateQueryService
from apps.crf.application.services.field_template_import import CrfFieldTemplateImportService
from apps.crf.application.services.validation_rule_import import CrfValidationRuleImportService
from apps.crf.domain.aggregate import FieldTemplateAggregate
from apps.crf.domain.exceptions import FormBuilderDomainValidationError
from apps.crf.infrastructure.repositories.form_builder import DjangoOrmFormBuilderRepository
from apps.crf.infrastructure.repositories.templates import DjangoCrfTemplateRepository
from apps.crf.models import (
    CrfFieldDefinition,
    CrfFieldUiConfig,
)
from apps.study.presentation.web.tables import CrfTemplateListTable


class CrfTemplateListTableTests(SimpleTestCase):
    def test_render_actions_contains_detail_and_builder_links(self):
        record = SimpleNamespace(pk=17)

        html = CrfTemplateListTable.render_actions(record)

        self.assertIn('/crf/forms/17', html)
        self.assertIn('/crf/forms/17/builder', html)
        self.assertIn('Detail', html)
        self.assertIn('Builder', html)


class CrfTemplateCommandServiceTests(SimpleTestCase):
    def test_upsert_crf_template_sets_bilingual_name_translations(self):
        template_instance = MagicMock()
        repository = MagicMock()
        repository.get_template_for_upsert.return_value = None
        repository.build_template.return_value = template_instance

        service = CrfTemplateCommandService(repository=repository)

        outcome = CrfTemplateCommandService.upsert_crf_template.__wrapped__(
            service,
            UpsertCrfTemplateCommand(
                selected_study_id=5,
                study_id=5,
                code="AE",
                version="v1.0",
                vi_name="Bien co bat loi",
                en_name="Adverse Event",
                actor_user_id=9,
            ),
            now="2026-04-24T10:00:00",
        )

        self.assertEqual(outcome, "created")
        template_instance.set_current_language.assert_any_call("vi", initialize=True)
        template_instance.set_current_language.assert_any_call("en", initialize=True)
        self.assertEqual(template_instance.name, "Adverse Event")
        repository.save_template.assert_called_once_with(template_instance)


class CrfTemplateQueryServiceTests(SimpleTestCase):
    def test_field_schema_includes_all_translation_labels_with_stable_code(self):
        field = SimpleNamespace(
            pk=7, field_key="LAB_BIOCHEM_GPT", label="ALT (GPT)",
            data_type="DECIMAL", display_order=1, section_template=None,
            translations=SimpleNamespace(all=lambda: [
                SimpleNamespace(label="ALT (GPT)"),
                SimpleNamespace(label="Đo hoạt độ ALT (GPT) [Máu]"),
            ]),
        )
        repository = MagicMock()
        repository.list_template_fields_with_related_config.return_value = [field]
        repository.list_field_definitions_by_field_template_ids.return_value = []
        repository.list_field_ui_configs_by_field_template_ids.return_value = []
        result = CrfTemplateQueryService(repository=repository).list_template_fields_with_ui_config(template_id=2)
        self.assertEqual(result[0]["labels"], ["ALT (GPT)", "Đo hoạt độ ALT (GPT) [Máu]"])
        self.assertEqual(result[0]["field_key"], "LAB_BIOCHEM_GPT")

    def test_translated_config_columns_are_not_declared_on_master_models(self):
        field_definition_fields = {field.name for field in CrfFieldDefinition._meta.get_fields()}
        field_ui_config_fields = {field.name for field in CrfFieldUiConfig._meta.get_fields()}

        self.assertFalse({"unit", "codelist", "comments", "pattern_err_msg"} & field_definition_fields)
        self.assertFalse({"text", "options"} & field_ui_config_fields)

    def test_field_definition_query_does_not_select_translated_columns_from_master_table(self):
        repository = DjangoCrfTemplateRepository()

        sql = str(repository.list_field_definitions_by_field_template_ids([1]).query)

        self.assertNotIn("`crf_fielddefinition`.`unit`", sql)
        self.assertNotIn("`crf_fielddefinition`.`codelist`", sql)
        self.assertNotIn("`crf_fielddefinition`.`comments`", sql)
        self.assertNotIn("`crf_fielddefinition`.`pattern_err_msg`", sql)

    def test_field_ui_config_query_does_not_select_translated_columns_from_master_table(self):
        repository = DjangoCrfTemplateRepository()

        sql = str(repository.list_field_ui_configs_by_field_template_ids([1]).query)

        self.assertNotIn("`crf_fielduiconfig`.`text`", sql)
        self.assertNotIn("`crf_fielduiconfig`.`options`", sql)

    def test_translated_related_value_prefers_current_language_then_english(self):
        instance = SimpleNamespace(
            translations=SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(language_code="en", unit="kg"),
                    SimpleNamespace(language_code="vi", unit="kg-vi"),
                ],
            ),
        )

        result = CrfTemplateQueryService._translated_related_value(instance, "vi", "unit")

        self.assertEqual(result, "kg-vi")

    def test_template_fields_payload_includes_number_range_constraints(self):
        field_template = SimpleNamespace(
            pk=7,
            field_key="WEIGHT",
            data_type="NUMBER",
            display_order=1,
            section_template=None,
            translations=SimpleNamespace(all=lambda: []),
        )
        field_definition = SimpleNamespace(
            field_template_id=7,
            data_semantic="",
            range_min=Decimal("0"),
            range_max=Decimal("200"),
            precision=2,
            text_max_length=None,
            text_min_length=None,
            pattern="",
            translations=SimpleNamespace(all=lambda: []),
        )
        repository = MagicMock()
        repository.list_template_fields_with_related_config.return_value = [field_template]
        repository.list_field_definitions_by_field_template_ids.return_value = [field_definition]
        repository.list_field_ui_configs_by_field_template_ids.return_value = []

        payload = CrfTemplateQueryService(repository=repository).list_template_fields_with_ui_config(
            template_id=2,
        )

        self.assertEqual(payload[0]["range_min"], Decimal("0"))
        self.assertEqual(payload[0]["range_max"], Decimal("200"))
        self.assertEqual(payload[0]["precision"], 2)

    @patch(
        "apps.crf.application.services.crf_template_query.get_language",
        return_value="vi",
    )
    def test_export_fields_are_loaded_for_all_templates_in_one_batch(self, _get_language):
        repository = MagicMock()
        repository.list_export_fields_by_template_ids.return_value = [
            SimpleNamespace(
                pk=7,
                crf_template_id=2,
                field_key="WEIGHT",
                ui_config=SimpleNamespace(
                    deleted=False,
                    control_type="RADIO",
                    translations=SimpleNamespace(
                        all=lambda: [
                            SimpleNamespace(
                                language_code="vi",
                                options=(
                                    '{"source":"static","static":['
                                    '{"value":"1","label":"Có"},'
                                    '{"value":"0","label":"Không"}]}'
                                ),
                            ),
                        ]
                    ),
                ),
                translations=SimpleNamespace(
                    all=lambda: [
                        SimpleNamespace(language_code="en", label="Weight"),
                        SimpleNamespace(language_code="vi", label="Cân nặng"),
                    ]
                ),
            ),
            SimpleNamespace(
                pk=8,
                crf_template_id=3,
                field_key="AGE",
                translations=SimpleNamespace(
                    all=lambda: [
                        SimpleNamespace(language_code="en", label="Age"),
                    ]
                ),
            ),
        ]

        payload = CrfTemplateQueryService(
            repository=repository,
        ).list_export_fields_by_template_ids(template_ids=(3, 2, 3))

        repository.list_export_fields_by_template_ids.assert_called_once_with(
            template_ids=(2, 3),
        )
        self.assertEqual(payload[2][0]["label"], "Cân nặng")
        self.assertEqual(payload[2][0]["control_type"], "RADIO")
        self.assertEqual(
            payload[2][0]["choice_labels"],
            {"1": "Có", "0": "Không"},
        )
        self.assertEqual(payload[3][0]["label"], "Age")


class FieldTemplateAggregateTests(SimpleTestCase):
    def test_rejects_calculated_as_data_type(self):
        with self.assertRaisesMessage(FormBuilderDomainValidationError, "data_type must be one of"):
            FieldTemplateAggregate.from_payload(
                field_key="CALC_SCORE",
                data_type="CALCULATED",
                is_active=True,
                display_order=1,
                section_template_id=3,
                label_en="Calculated Score",
                label_vi="Diem tinh toan",
                definition={
                    "sdtm": {"domain": "QS", "variable": "QSSCOR", "role": "Result"},
                    "unit": "",
                    "range_min": "",
                    "range_max": "",
                    "precision": "",
                    "allowed_missing_values": "",
                    "codelist": "",
                    "data_semantic": "",
                    "comments": "",
                    "text_max_length": "",
                    "text_min_length": "",
                    "pattern": "",
                    "pattern_err_msg": "",
                },
                ui_config={
                    "control_type": "calculated_field",
                    "layout": "",
                    "text": "",
                    "behavior": "",
                    "options": "",
                    "style": "",
                },
                validation_rules=[],
                field_keys_in_form=(),
            )

    def test_rejects_duplicate_field_key_within_same_form(self):
        with self.assertRaises(FormBuilderDomainValidationError):
            FieldTemplateAggregate.from_payload(
                field_key="VISIT_DATE",
                data_type="DATE",
                is_active=True,
                display_order=1,
                section_template_id=3,
                label_en="Visit Date",
                label_vi="Ngay tham kham",
                definition={
                    "sdtm": {"domain": "SV", "variable": "SVSTDTC", "role": "Topic"},
                    "unit": "",
                    "range_min": "",
                    "range_max": "",
                    "precision": "",
                    "allowed_missing_values": "",
                    "codelist": "",
                    "data_semantic": "",
                    "comments": "",
                    "text_max_length": "",
                    "text_min_length": "",
                    "pattern": "",
                    "pattern_err_msg": "",
                },
                ui_config={
                    "control_type": "date_picker",
                    "layout": "",
                    "text": "",
                    "behavior": "",
                    "options": "",
                    "style": "",
                },
                validation_rules=[
                    {
                        "rule_type": "CUSTOM_EXPRESSION",
                        "expression": "$val != ''",
                        "severity": "error",
                        "mode": "HARD",
                        "messages": {"en": "Required", "vi": "Bat buoc"},
                    }
                ],
                field_keys_in_form=("visit_date",),
            )


class FormBuilderOrchestrationServiceTests(SimpleTestCase):
    def test_save_field_records_audit_log_for_created_field(self):
        repository = MagicMock()
        audit_service = MagicMock()
        field = SimpleNamespace(
            pk=21,
            field_key="VISIT_DATE",
            data_type="DATE",
            is_active=True,
            display_order=1,
        )

        repository.get_form_by_scope.return_value = SimpleNamespace(pk=7)
        repository.list_field_keys_for_form.return_value = ()
        repository.save_field_aggregate.return_value = ("created", field)

        service = FormBuilderOrchestrationService(
            repository=repository,
            audit_service=audit_service,
        )

        command = SaveFieldAggregateCommand(
            selected_study_id=3,
            study_id=3,
            form_id=7,
            actor_user_id=11,
            ip_address="127.0.0.1",
            user_agent="test-agent",
            field_id=None,
            field_key="VISIT_DATE",
            data_type="DATE",
            is_active=True,
            display_order=1,
            section_template_id=4,
            label_en="Visit Date",
            label_vi="Ngay tham kham",
            definition={
                "sdtm": {"domain": "SV", "variable": "SVSTDTC", "role": "Topic"},
                "unit": "",
                "range_min": "",
                "range_max": "",
                "precision": "",
                "allowed_missing_values": "",
                "codelist": "",
                "data_semantic": "",
                "comments": "",
                "text_max_length": "",
                "text_min_length": "",
                "pattern": "",
                "pattern_err_msg": "",
            },
            ui_config={
                "control_type": "date_picker",
                "layout": "",
                "text": "",
                "behavior": "",
                "options": "",
                "style": "",
            },
            validation_rules=[
                {
                    "rule_type": "CUSTOM_EXPRESSION",
                    "expression": "$val != ''",
                    "severity": "error",
                    "mode": "HARD",
                    "messages": {"en": "Required"},
                }
            ],
        )

        result = FormBuilderOrchestrationService.save_field.__wrapped__(
            service,
            command=command,
        )

        self.assertEqual(result, {"action": "created", "field_id": 21})
        audit_service.record_field_created.assert_called_once()


class DjangoOrmFormBuilderRepositoryTests(SimpleTestCase):
    def test_apply_field_snapshot_sets_bilingual_field_labels(self):
        repository = DjangoOrmFormBuilderRepository()
        field = MagicMock()

        repository._apply_field_snapshot(
            field,
            {
                "field_key": "AE_TERM",
                "data_type": "TEXT",
                "is_active": True,
                "display_order": 2,
                "section_template_id": 7,
                "label_en": "Adverse Event Term",
                "label_vi": "Thuat ngu bien co bat loi",
            },
            now="2026-04-24T11:00:00",
            actor_user_id=8,
        )

        self.assertEqual(field.field_key, "AE_TERM")
        self.assertEqual(field.data_type, "TEXT")
        field.set_current_language.assert_any_call("en", initialize=True)
        field.set_current_language.assert_any_call("vi", initialize=True)
        self.assertEqual(field.label, "Thuat ngu bien co bat loi")

    @patch("apps.crf.infrastructure.repositories.form_builder.CrfFieldValidationRule")
    def test_replace_validation_rules_persists_translated_messages(self, mock_rule_cls):
        existing_rule = MagicMock()
        mock_rule_cls.objects.filter.return_value.prefetch_related.return_value = [existing_rule]

        created_rule = MagicMock()
        mock_rule_cls.return_value = created_rule

        repository = DjangoOrmFormBuilderRepository()
        snapshots = [
            {
                "rule_type": "CUSTOM_EXPRESSION",
                "expression": "$val != ''",
                "severity": "error",
                "mode": "HARD",
                "translations": [
                    {"language_code": "en", "message": "Required"},
                    {"language_code": "vi", "message": "Bat buoc"},
                ],
            }
        ]

        rules = repository._replace_validation_rules(
            field_template_id=12,
            crf_template_id=17,
            study_id=3,
            snapshots=snapshots,
            actor_user_id=5,
            now="2026-04-24T12:00:00",
        )

        existing_rule.delete.assert_called_once()
        mock_rule_cls.assert_called_once()
        rule_kwargs = mock_rule_cls.call_args.kwargs
        self.assertEqual(rule_kwargs["study_id"], 3)
        self.assertEqual(rule_kwargs["crf_template_id"], 17)
        created_rule.set_current_language.assert_any_call("en", initialize=True)
        created_rule.set_current_language.assert_any_call("vi", initialize=True)
        self.assertEqual(created_rule.message, "Bat buoc")
        self.assertEqual(rules, [created_rule])


class CrfFieldTemplateImportServiceTests(SimpleTestCase):
    def test_reset_template_fields_for_import_delegates_to_repository(self):
        repository = MagicMock()
        repository.reset_template_fields_for_import.return_value = 3
        service = CrfFieldTemplateImportService(repository=repository)

        result = service.reset_template_fields_for_import(
            crf_template_id=17,
            actor_user_id=9,
            now="2026-05-26T10:00:00",
        )

        self.assertEqual(result, 3)
        repository.reset_template_fields_for_import.assert_called_once_with(
            crf_template_id=17,
            actor_user_id=9,
            now="2026-05-26T10:00:00",
        )

    def test_upsert_template_field_writes_master_and_translation_snapshots(self):
        repository = MagicMock()
        repository.get_field_template_for_import.return_value = None
        field_template = MagicMock(pk=31)
        definition = MagicMock(pk=41)
        ui_config = MagicMock(pk=51)
        repository.build_field_template.return_value = field_template
        repository.create_field_definition.return_value = definition
        repository.create_field_ui_config.return_value = ui_config
        service = CrfFieldTemplateImportService(repository=repository)
        action, field_template = service.upsert_template_field(
            crf_template_id=17,
            section_template_id=23,
            payload={
                "field_key": "AETERM",
                "label_vi": "Bien co bat loi",
                "label_en": "Adverse Event Term",
                "data_type": "TEXT",
                "display_order": 1,
                "control_type": "TEXT",
                "control_layout": "normal",
                "layout": '{"columns": 6}',
                "behavior": '{"required": true}',
                "style": "font-weight: 600;",
                "classes": "col-span-6",
                "text_vi": "Nhap bien co",
                "text_en": "Enter adverse event",
                "options_vi": "Co|Khong",
                "options_en": "Yes|No",
                "sdtm": '{"domain":"AE"}',
                "range_min": None,
                "range_max": None,
                "precision": None,
                "allowed_missing_values": "NA",
                "data_semantic": "clinical",
                "text_max_length": 255,
                "text_min_length": 1,
                "pattern": None,
                "pattern_err_msg_vi": "Khong hop le",
                "pattern_err_msg_en": "Invalid",
                "unit_vi": "ngay",
                "unit_en": "day",
                "codelist_vi": "Danh sach",
                "codelist_en": "Codelist",
                "comments_vi": "Ghi chu",
                "comments_en": "Comments",
            },
            actor_user_id=9,
            now="2026-05-19T10:00:00",
        )

        self.assertEqual(action, "created")
        repository.build_field_template.assert_called_once_with(
            crf_template_id=17,
            field_key="AETERM",
            created_at="2026-05-19T10:00:00",
            created_by_id=9,
        )
        repository.create_field_template_translation.assert_any_call(
            field_template=field_template,
            language_code="vi",
            label="Bien co bat loi",
        )
        repository.create_field_definition.assert_called_once()
        repository.create_field_definition_translation.assert_any_call(
            definition=definition,
            language_code="en",
            values={
                "unit": "day",
                "codelist": "Codelist",
                "comments": "Comments",
                "pattern_err_msg": "Invalid",
            },
        )
        repository.create_field_ui_config.assert_called_once()
        repository.create_field_ui_config_translation.assert_any_call(
            ui_config=ui_config,
            language_code="vi",
            values={
                "text": "Nhap bien co",
                "options": "Co|Khong",
            },
        )


class CrfValidationRuleImportServiceTests(SimpleTestCase):
    def test_upsert_validation_rule_sets_scope_and_translations(self):
        repository = MagicMock()
        repository.find_validation_rule_for_import.return_value = None
        validation_rule = MagicMock(pk=61)
        repository.build_validation_rule.return_value = validation_rule
        service = CrfValidationRuleImportService(repository=repository)

        action, result_rule = CrfValidationRuleImportService.upsert_validation_rule.__wrapped__(
            service,
            study_id=3,
            crf_template_id=17,
            field_template_id=23,
            rule_type="REQUIRED",
            expression="$val != ''",
            severity="error",
            mode="HARD",
            vi_message="Bat buoc",
            en_message="Required",
            actor_user_id=9,
            now="2026-05-27T10:00:00",
        )

        self.assertEqual(action, "created")
        self.assertEqual(result_rule, validation_rule)
        repository.build_validation_rule.assert_called_once_with(
            field_template_id=23,
            created_at="2026-05-27T10:00:00",
            created_by_id=9,
        )
        self.assertEqual(validation_rule.study_id, 3)
        self.assertEqual(validation_rule.crf_template_id, 17)
        self.assertEqual(validation_rule.rule_type, "REQUIRED")
        validation_rule.set_current_language.assert_any_call("en", initialize=True)
        validation_rule.set_current_language.assert_any_call("vi", initialize=True)
        self.assertEqual(validation_rule.message, "Bat buoc")
        self.assertEqual(repository.save_validation_rule.call_count, 3)


class CrfFormBuilderViewValidationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_duplicate_field_key_error_is_bound_to_field_key(self):
        from apps.crf.presentation.web.forms import CrfFieldCreateForm
        from apps.crf.presentation.web.views import CrfFormBuilderView

        form = CrfFieldCreateForm()
        form.cleaned_data = {}

        CrfFormBuilderView._bind_field_domain_error(
            form,
            FormBuilderDomainValidationError("field_key must be unique in form scope."),
        )

        self.assertIn("field_key", form.errors)
        self.assertIn("Field Key", form.errors["field_key"][0])


class CrfFieldCreateFormChoicesTests(SimpleTestCase):
    def test_calculated_is_not_a_data_type_or_control_type_choice(self):
        from apps.crf.presentation.web.forms import CrfFieldCreateForm

        form = CrfFieldCreateForm()

        data_type_values = {value for value, _label in form.fields["data_type"].choices}
        control_type_values = {value for value, _label in form.fields["control_type"].choices}

        self.assertNotIn("CALCULATED", data_type_values)
        self.assertNotIn("calculated_field", control_type_values)
