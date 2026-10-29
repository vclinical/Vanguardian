import json

from django.utils.translation import get_language

from apps.crf.application.exceptions import (
    CrfTemplateAmbiguousError,
    CrfTemplateNotFoundError,
)
from apps.crf.infrastructure.repositories import DjangoCrfTemplateRepository


class CrfTemplateQueryService:
    repository_class = DjangoCrfTemplateRepository

    def __init__(self, repository=None):
        self.repository = repository or self.repository_class()

    def get_crf_template_model(self):
        return self.repository.get_crf_template_model()

    def list_study_templates_for_listing(self, *, study_id):
        return self.repository.list_study_templates_for_listing(study_id=study_id)

    def list_study_crf_navigation(self, *, study_id):
        current_language = self._normalize_language_code(get_language())
        crf_templates = list(self.repository.list_study_crf_navigation_templates(study_id=study_id))

        payload = []
        for crf_template in crf_templates:
            template_name = self._translated_value(crf_template, current_language, "name", default=crf_template.code)
            payload.append(
                {
                    "id": str(crf_template.pk),
                    "code": crf_template.code,
                    "name": template_name,
                    "version": crf_template.version,
                    "pages": [
                        {
                            "id": f"crf-{crf_template.pk}-main",
                            "code": crf_template.code,
                            "title": template_name,
                            "order": 1,
                        }
                    ],
                }
            )

        return payload

    def list_study_templates_by_code(self, *, study_id, code):
        return list(self.repository.list_study_templates_by_code(study_id=study_id, code=code))

    def resolve_unique_template_by_code_version(self, *, study_id, code, version):
        queryset = self.repository.find_unique_template_by_code_version(
            study_id=study_id,
            code=code,
            version=version,
        )

        count = queryset.count()
        if count == 0:
            raise CrfTemplateNotFoundError(
                f"CRF template with code '{code}' and version '{version}' was not found for study '{study_id}'."
            )
        if count > 1:
            raise CrfTemplateAmbiguousError(
                f"CRF template code '{code}' and version '{version}' is ambiguous for study '{study_id}'."
            )
        return queryset.first()

    def resolve_unique_template_by_code(self, *, study_id, code, case_insensitive=False):
        queryset = self.repository.find_unique_template_by_code(
            study_id=study_id,
            code=code,
            case_insensitive=case_insensitive,
        )

        count = queryset.count()
        if count == 0:
            raise CrfTemplateNotFoundError(
                f"CRF template with code '{code}' was not found for study '{study_id}'."
            )
        if count > 1:
            raise CrfTemplateAmbiguousError(
                f"CRF template code '{code}' is ambiguous for study '{study_id}'."
            )
        return queryset.first()

    def list_template_fields_with_ui_config(self, *, template_id):
        current_language = self._normalize_language_code(get_language())
        field_templates = list(self.repository.list_template_fields_with_related_config(template_id=template_id))
        if not field_templates:
            return []

        field_template_ids = [field_template.pk for field_template in field_templates]
        field_definition_map = {
            field_definition.field_template_id: field_definition
            for field_definition in self.repository.list_field_definitions_by_field_template_ids(field_template_ids)
        }
        ui_config_map = {
            ui_config.field_template_id: ui_config
            for ui_config in self.repository.list_field_ui_configs_by_field_template_ids(field_template_ids)
        }

        payload = []
        for field_template in field_templates:
            field_definition = field_definition_map.get(field_template.pk)
            ui_config = ui_config_map.get(field_template.pk)
            field_definition_unit = self._translated_related_value(
                field_definition,
                current_language,
                "unit",
            )
            field_definition_codelist = self._translated_related_value(
                field_definition,
                current_language,
                "codelist",
            )
            field_definition_comments = self._translated_related_value(
                field_definition,
                current_language,
                "comments",
            )
            field_definition_pattern_err_msg = self._translated_related_value(
                field_definition,
                current_language,
                "pattern_err_msg",
            )
            ui_config_text = self._translated_related_value(
                ui_config,
                current_language,
                "text",
            )
            ui_config_options = self._translated_related_value(
                ui_config,
                current_language,
                "options",
            )
            field_label = self._translated_value(
                field_template,
                current_language,
                "label",
                default=field_template.field_key,
            )

            section_template = field_template.section_template
            section_payload = None
            if section_template is not None:
                layout_config = getattr(section_template, "layout_config", None)
                layout_payload = None
                if layout_config is not None and not layout_config.deleted:
                    layout_payload = {
                        "layout_type": layout_config.layout_type,
                        "column_count": layout_config.column_count,
                        "label_position": layout_config.label_position,
                        "density": layout_config.density,
                        "section_style": layout_config.section_style,
                        "is_collapsible": layout_config.is_collapsible,
                        "is_expanded_by_default": layout_config.is_expanded_by_default,
                        "show_section_header": layout_config.show_section_header,
                        "show_border": layout_config.show_border,
                        "show_background": layout_config.show_background,
                        "custom_css_class": layout_config.custom_css_class,
                        "custom_layout_schema": layout_config.custom_layout_schema,
                    }
                section_payload = {
                    "id": str(section_template.pk),
                    "code": section_template.section_code,
                    "name": self._translated_value(
                        section_template,
                        current_language,
                        "section_name",
                        default=section_template.section_code,
                    ),
                    "display_order": section_template.display_order,
                    "is_required": section_template.is_required,
                    "is_repeatable": section_template.is_repeatable,
                    "min_repeats": section_template.min_repeats,
                    "max_repeats": section_template.max_repeats,
                    "layout_config": layout_payload,
                }

            payload.append(
                {
                    "id": str(field_template.pk),
                    "field_key": field_template.field_key,
                    "label": field_label,
                    "labels": [translation.label for translation in field_template.translations.all() if translation.label],
                    "data_type": field_template.data_type,
                    "display_order": field_template.display_order,
                    "section_template": section_payload,
                    "data_semantic": field_definition.data_semantic if field_definition else None,
                    "comments": field_definition_comments,
                    "unit": field_definition_unit,
                    "range_min": field_definition.range_min if field_definition else None,
                    "range_max": field_definition.range_max if field_definition else None,
                    "precision": field_definition.precision if field_definition else None,
                    "codelist": field_definition_codelist,
                    "text_max_length": field_definition.text_max_length if field_definition else None,
                    "text_min_length": field_definition.text_min_length if field_definition else None,
                    "pattern": field_definition.pattern if field_definition else None,
                    "pattern_err_msg": field_definition_pattern_err_msg,
                    "ui_config": {
                        "control_type": ui_config.control_type if ui_config else None,
                        "control_layout": ui_config.control_layout if ui_config else None,
                        "layout": ui_config.layout if ui_config else None,
                        "text": ui_config_text,
                        "behavior": ui_config.behavior if ui_config else None,
                        "options": ui_config_options,
                        "style": ui_config.style if ui_config else None,
                        "classes": ui_config.classes if ui_config else None,
                    },
                }
            )

        return payload

    def list_export_fields_by_template_ids(self, *, template_ids, language_code=None):
        normalized_template_ids = tuple(sorted({int(template_id) for template_id in template_ids}))
        fields_by_template_id = {template_id: [] for template_id in normalized_template_ids}
        if not normalized_template_ids:
            return fields_by_template_id

        current_language = self._normalize_language_code(
            language_code if language_code is not None else get_language() or "en",
        )
        field_templates = self.repository.list_export_fields_by_template_ids(
            template_ids=normalized_template_ids,
        )
        for field_template in field_templates:
            template_id = int(field_template.crf_template_id)
            ui_config = getattr(field_template, "ui_config", None)
            fields_by_template_id[template_id].append(
                {
                    "id": str(field_template.pk),
                    "field_key": field_template.field_key,
                    "label": self._export_field_label(
                        field_template,
                        current_language,
                    ),
                    "control_type": (
                        str(ui_config.control_type)
                        if ui_config is not None and not ui_config.deleted
                        else ""
                    ),
                    "choice_labels": self._export_choice_labels(
                        ui_config,
                        current_language,
                    ),
                }
            )
        return fields_by_template_id

    @classmethod
    def _export_choice_labels(cls, ui_config, language_code) -> dict[str, str]:
        if ui_config is None or ui_config.deleted:
            return {}
        control_type = str(ui_config.control_type or "").strip().upper()
        if control_type not in {"RADIO", "CHECKBOX"}:
            return {}
        raw_options = cls._translated_related_value(
            ui_config,
            language_code,
            "options",
        )
        return {
            str(option.get("value") or "").strip(): str(
                option.get("label") or ""
            ).strip()
            for option in cls._normalize_export_choice_options(raw_options)
            if str(option.get("value") or "").strip()
            and str(option.get("label") or "").strip()
        }

    @classmethod
    def _normalize_export_choice_options(cls, raw_options) -> list[dict]:
        if not raw_options:
            return []
        parsed = raw_options
        if isinstance(raw_options, str):
            normalized = raw_options.strip()
            if normalized.startswith(("[", "{")):
                try:
                    parsed = json.loads(normalized)
                except json.JSONDecodeError:
                    return []
            else:
                return cls._export_choice_options_from_text(normalized)
        if isinstance(parsed, dict):
            if str(parsed.get("source") or "static").strip().lower() != "static":
                return []
            parsed = parsed.get("static") or []
        if not isinstance(parsed, list):
            return []
        return [option for option in parsed if isinstance(option, dict)]

    @staticmethod
    def _export_choice_options_from_text(raw_options: str) -> list[dict]:
        chunks = [
            chunk.strip()
            for chunk in raw_options.replace("\r", "\n")
            .replace("|", "\n")
            .replace(";", "\n")
            .split("\n")
            if chunk.strip()
        ]
        options = []
        for chunk in chunks:
            if "=" in chunk:
                label, value = chunk.split("=", 1)
                options.append({"value": value.strip(), "label": label.strip()})
            else:
                options.append({"value": chunk, "label": chunk})
        return options

    @staticmethod
    def _export_field_label(field_template, language_code):
        translations = list(field_template.translations.all())
        for translation in translations:
            if translation.language_code == language_code and translation.label:
                return translation.label
        if language_code == "en":
            return field_template.field_key
        for translation in translations:
            if translation.language_code == "en" and translation.label:
                return translation.label
        for translation in translations:
            if translation.label:
                return translation.label
        return field_template.field_key

    @staticmethod
    def _translated_related_value(instance, language_code, field_name, default=None):
        if instance is None:
            return default
        translations = list(getattr(instance, "translations", []).all())
        if not translations:
            return default
        for translation in translations:
            if translation.language_code == language_code:
                value = getattr(translation, field_name, default)
                return value if value not in (None, "") else default
        for translation in translations:
            if translation.language_code == "en":
                value = getattr(translation, field_name, default)
                return value if value not in (None, "") else default
        value = getattr(translations[0], field_name, default)
        return value if value not in (None, "") else default

    @staticmethod
    def _translated_value(instance, language_code, field_name, default=""):
        if hasattr(instance, "safe_translation_getter"):
            value = instance.safe_translation_getter(
                field_name,
                default=default,
                language_code=language_code,
                any_language=False,
            )
            if value not in (None, ""):
                return value
            # English UI should not silently fall back to another language when no EN row exists.
            if language_code == "en":
                available = list(instance.get_available_languages())
                if available and "en" not in available:
                    return default
            fallback = instance.safe_translation_getter(
                field_name,
                default=default,
                any_language=True,
            )
            return fallback or default
        return getattr(instance, field_name, default) or default

    @staticmethod
    def _normalize_language_code(language_code):
        normalized = (language_code or "en").strip().lower()
        return normalized.split("-", 1)[0]


__all__ = ["CrfTemplateQueryService"]
