"""Explicit, schema-driven scalar conversion for file imports."""
import json
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext as _


def choice_options(field):
    raw = (field.get("ui_config") or {}).get("options") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [{"label": chunk.split("=", 1)[0], "value": chunk.split("=", 1)[1]}
                   for chunk in raw.split() if "=" in chunk]
    if isinstance(raw, dict) and "source" in raw:
        raw = raw.get("static", []) if raw.get("source") == "static" else []
    if isinstance(raw, dict):
        raw = [{"value": key, "label": value} for key, value in raw.items()]
    return [{"value": str(item["value"]), "label": str(item.get("label", item["value"]))}
            for item in raw if isinstance(item, dict) and "value" in item]


def convert_value(value, field, override=None):
    text = str(value).strip()
    control = str((field.get("ui_config") or {}).get("control_type") or "").upper()
    raw_options = (field.get("ui_config") or {}).get("options")
    if isinstance(raw_options, str) and raw_options.lstrip().startswith("{"):
        try:
            raw_options = json.loads(raw_options)
        except ValueError as exc:
            raise ValueError(_("The choice list configuration is invalid.")) from exc
    if isinstance(raw_options, dict) and raw_options.get("source") not in {None, "static"}:
        raise ValueError(_("This field uses a dynamic list. Select its value directly in the eCRF."))
    if control in {"MULTI_SELECT", "MULTISELECT", "CHECKBOX_GROUP", "CHECKBOXES", "CHECKBOX_LIST"}:
        raise ValueError(_("Enter this multiple-choice field directly in the eCRF to specify all selected values."))
    options = choice_options(field)
    if options:
        if control == "CHECKBOX" and len(options) > 1:
            raise ValueError(_("Enter this multiple-choice field directly in the eCRF to specify all selected values."))
        if override is not None:
            if str(override) not in {item["value"] for item in options}:
                raise ValueError(_("The confirmed value is not in the allowed choice list."))
            return str(override)
        matches = {item["value"] for item in options if text.casefold() in (item["value"].casefold(), item["label"].casefold())}
        if len(matches) != 1:
            raise ValueError(_("No eCRF choice matches. Select and confirm the target value."))
        return matches.pop()
    if control in {"SELECT", "RADIO", "DROPDOWN", "SELECT_SINGLE", "SELECT2", "RADIO_BUTTON_LIST"}:
        raise ValueError(_("No valid choice list was found for this field."))
    if override is not None:
        raise ValueError(_("Only values from an allowed choice list can be explicitly resolved."))
    control = str((field.get("ui_config") or {}).get("control_type") or "").upper()
    if control in {"LOOKUP", "AUTOCOMPLETE", "FILE", "SIGNATURE", "LABEL_ONLY"}:
        raise ValueError(_("This field does not support importing values from Excel."))
    kind = str(field.get("data_type") or "TEXT").upper()
    if kind == "CODELIST":
        raise ValueError(_("No valid code list was found for this field."))
    if kind in {"INTEGER", "INT", "DECIMAL", "FLOAT", "NUMBER", "NUMERIC"}:
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(_("Enter a valid number without a unit.")) from exc
        if not number.is_finite() or (kind in {"INTEGER", "INT"} and number != number.to_integral_value()):
            raise ValueError(_("The number is invalid for this field type."))
        if abs(number.adjusted()) > 1000 or len(text) > 1000:
            raise ValueError(_("The number exceeds the supported length limit."))
        return format(number, "f")
    if kind in {"DATE", "DATETIME"}:
        try:
            if kind == "DATE":
                if "T" in text:
                    parsed = datetime.fromisoformat(text)
                    if parsed.time().isoformat() != "00:00:00":
                        raise ValueError(_("A nonzero time component cannot be discarded automatically."))
                    return parsed.date().isoformat()
                return date.fromisoformat(text).isoformat()
            return datetime.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise ValueError(_("Dates must use the ISO YYYY-MM-DD format.")) from exc
    if kind == "TIME":
        try:
            return time.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise ValueError(_("Times must use the HH:MM[:SS] format.")) from exc
    if kind in {"BOOLEAN", "BOOL"}:
        if text.casefold() not in {"true", "false", "1", "0"}:
            raise ValueError(_("Boolean values must be true/false or 1/0."))
        return "1" if text.casefold() in {"true", "1"} else "0"
    maximum = field.get("text_max_length")
    if maximum and len(text) > int(maximum):
        raise ValueError(_("The value exceeds the maximum length of this field."))
    return text


def equivalent(old, new, field):
    if old is None or str(old).strip() == "":
        return False
    try:
        left = convert_value(old, field)
        if str(field.get("data_type") or "").upper() in {"INTEGER", "INT", "DECIMAL", "FLOAT", "NUMBER", "NUMERIC"} and not choice_options(field):
            return Decimal(left) == Decimal(new)
        return left == new
    except ValueError:
        return str(old) == str(new)
