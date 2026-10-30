import ast
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.utils import timezone

from apps.core.form_data_document import flatten_form_data_for_export, normalize_form_data

RULE_TYPE_CUSTOM_EXPRESSION = "CUSTOM_EXPRESSION"
RULE_TYPE_REQUIRED = "REQUIRED"
FORM_FIELD_TOKEN_RE = re.compile(r"\$form\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
FORM_ROW_FIELD_TOKEN_RE = re.compile(r"\$form\.([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*)")
CURRENT_FIELD_TOKEN_RE = re.compile(r"\$field\.([A-Za-z_][A-Za-z0-9_]*)")
REPEAT_KEY_RE = re.compile(r"^(?P<base>.+?)__repeat_(?P<repeat_index>\d+)$")


def _num(value) -> float:
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    return float(value)


def _bool(value) -> bool:
    return bool(value)


def _coerce_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError("Date value is empty.")
        try:
            return date.fromisoformat(normalized[:10])
        except ValueError as exc:
            raise ValueError(f"Invalid ISO date value: {value}") from exc
    raise ValueError(f"Unsupported date value: {value!r}")


def _days_between(left, right) -> int:
    return (_coerce_date(left) - _coerce_date(right)).days


SAFE_EXPRESSION_FUNCTIONS = {
    "bool": _bool,
    "days_between": _days_between,
    "num": _num,
}


@dataclass(frozen=True)
class FieldValidationCheckResult:
    has_failures: bool
    failed_field_keys: tuple[str, ...]
    failures: tuple["FieldValidationFailure", ...] = ()


@dataclass(frozen=True)
class FieldValidationRuleCheck:
    id: int | None
    field_template_id: int | None
    rule_type: str
    mode: str
    severity: str
    expression: str
    message: str


@dataclass(frozen=True)
class FieldValidationFailure:
    rule_id: int | None
    field_template_id: int | None
    field_key: str
    rule_type: str
    mode: str
    severity: str
    message: str
    failed_value: Any
    evaluation_context: str | None = None


def _rule_attr(rule: Any, key: str):
    if isinstance(rule, dict):
        return rule.get(key)
    return getattr(rule, key, None)


def _normalize_rule_check(rule) -> FieldValidationRuleCheck:
    if isinstance(rule, str):
        return FieldValidationRuleCheck(
            id=None,
            field_template_id=None,
            rule_type=RULE_TYPE_CUSTOM_EXPRESSION,
            mode="SOFT",
            severity="",
            expression=rule,
            message="",
        )
    rule_id = _rule_attr(rule, "id")
    field_template_id = _rule_attr(rule, "field_template_id")
    rule_type = _rule_attr(rule, "rule_type")
    mode = _rule_attr(rule, "mode")
    severity = _rule_attr(rule, "severity")
    expression = _rule_attr(rule, "expression")
    message = _rule_attr(rule, "message")
    if rule_type is None and isinstance(rule, (tuple, list)) and len(rule) >= 2:
        rule_type, expression = rule[0], rule[1]
    return FieldValidationRuleCheck(
        id=_to_int_or_none(rule_id),
        field_template_id=_to_int_or_none(field_template_id),
        rule_type=str(rule_type or "").strip().upper(),
        mode=str(mode or "").strip().upper(),
        severity=str(severity or "").strip(),
        expression=str(expression or "").strip(),
        message=str(message or "").strip(),
    )


def _to_int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_eval_literal(raw_value):
    if raw_value is None:
        return ""
    if isinstance(raw_value, (dict, list, tuple)):
        return json.dumps(raw_value, ensure_ascii=False)
    return raw_value


def _parse_repeat_key(raw_key: str) -> tuple[str, int]:
    normalized = str(raw_key or "").strip()
    match = REPEAT_KEY_RE.match(normalized)
    if match:
        return match.group("base"), int(match.group("repeat_index"))
    return normalized, 1


def _build_form_rows(payload_map: dict[str, Any]) -> list[dict[str, Any]]:
    row_map: dict[int, dict[str, Any]] = {}
    for raw_key, value in (payload_map or {}).items():
        normalized_key = str(raw_key or "").strip()
        if not normalized_key or normalized_key == "__form_verification__":
            continue
        field_key, repeat_index = _parse_repeat_key(normalized_key)
        row_map.setdefault(repeat_index, {})[field_key] = value
    return [row_map[idx] for idx in sorted(row_map)]


def _extract_referenced_form_codes(rules_by_field_key: dict[str, tuple[dict[str, object], ...]]) -> tuple[str, ...]:
    form_codes: list[str] = []
    seen: set[str] = set()
    for rules in rules_by_field_key.values():
        for rule in rules or ():
            expression = str(_rule_attr(rule, "expression") or "").strip()
            for match in FORM_ROW_FIELD_TOKEN_RE.finditer(expression):
                form_code = match.group(1)
                if form_code not in seen:
                    seen.add(form_code)
                    form_codes.append(form_code)
            for match in FORM_FIELD_TOKEN_RE.finditer(expression):
                form_code = match.group(1)
                if form_code not in seen:
                    seen.add(form_code)
                    form_codes.append(form_code)
    return tuple(form_codes)


def _resolve_form_value(*, form_context: dict[str, dict[str, Any]] | None, form_code: str, field_key: str, repeat_index: int | None):
    context = dict(form_context or {}).get(form_code)
    if not isinstance(context, dict):
        return None
    rows = context.get("rows")
    if not isinstance(rows, list):
        rows = []
    normalized_field_key = str(field_key or "").strip()
    if repeat_index is not None:
        if repeat_index <= 0 or repeat_index > len(rows):
            return None
        row = rows[repeat_index - 1]
        if not isinstance(row, dict):
            return None
        return row.get(normalized_field_key)
    values = [
        row.get(normalized_field_key)
        for row in rows
        if isinstance(row, dict) and normalized_field_key in row
    ]
    if context.get("is_repeatable"):
        return values
    if not values:
        return None
    return values[0]


def _normalize_expression(expression: str, value, payload_map=None, form_context=None):
    normalized = str(expression or "").strip()
    normalized = normalized.replace("&&", " and ").replace("||", " or ")
    normalized = re.sub(r"!\s*(?!=)", " not ", normalized)
    normalized = re.sub(r"\btrue\b", "True", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bfalse\b", "False", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bnull\b", "None", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace("$today", repr(timezone.localdate().isoformat()))
    payload_map = payload_map or {}
    normalized = FORM_ROW_FIELD_TOKEN_RE.sub(
        lambda match: repr(
            _resolve_form_value(
                form_context=form_context,
                form_code=match.group(1),
                field_key=match.group(3),
                repeat_index=int(match.group(2)),
            )
        ),
        normalized,
    )
    normalized = FORM_FIELD_TOKEN_RE.sub(
        lambda match: repr(
            _resolve_form_value(
                form_context=form_context,
                form_code=match.group(1),
                field_key=match.group(2),
                repeat_index=None,
            )
        ),
        normalized,
    )
    normalized = CURRENT_FIELD_TOKEN_RE.sub(
        lambda match: repr(_to_eval_literal(payload_map.get(match.group(1)))),
        normalized,
    )
    value_literal = repr(_to_eval_literal(value))
    return normalized.replace("$val", value_literal)


def _is_safe_expression_ast(expression: str) -> bool:
    try:
        root = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False
    allowed_nodes = (
        ast.Expression,
        ast.BoolOp,
        ast.UnaryOp,
        ast.BinOp,
        ast.Compare,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.And,
        ast.Or,
        ast.Not,
        ast.Eq,
        ast.NotEq,
        ast.Gt,
        ast.GtE,
        ast.Lt,
        ast.LtE,
        ast.In,
        ast.NotIn,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Call,
    )
    for node in ast.walk(root):
        if not isinstance(node, allowed_nodes):
            return False
        if isinstance(node, ast.Call):
            if node.keywords:
                return False
            if not isinstance(node.func, ast.Name) or node.func.id not in SAFE_EXPRESSION_FUNCTIONS:
                return False
    return True


def check_field_err(*, expression: str, value, payload_map=None, form_context=None) -> bool:
    normalized = _normalize_expression(
        expression,
        value,
        payload_map=payload_map,
        form_context=form_context,
    )
    if not normalized:
        return False
    if not _is_safe_expression_ast(normalized):
        return True
    try:
        result = eval(normalized, {"__builtins__": {}}, SAFE_EXPRESSION_FUNCTIONS)  # noqa: S307
    except Exception:
        return True
    return bool(result)


def _is_required_value_empty(value) -> bool:
    return value is None or value == ""


def _check_rule_has_error(*, rule, value, payload_map=None, form_context=None) -> bool:
    rule_check = _normalize_rule_check(rule)
    if rule_check.rule_type == RULE_TYPE_REQUIRED:
        return _is_required_value_empty(value)
    if rule_check.rule_type == RULE_TYPE_CUSTOM_EXPRESSION:
        return check_field_err(
            expression=rule_check.expression,
            value=value,
            payload_map=payload_map,
            form_context=form_context,
        )
    return False


def _field_validation_failure(*, field_key: str, rule, value, payload_map=None, form_context=None) -> FieldValidationFailure | None:
    rule_check = _normalize_rule_check(rule)
    if not _check_rule_has_error(
        rule=rule_check,
        value=value,
        payload_map=payload_map,
        form_context=form_context,
    ):
        return None
    return FieldValidationFailure(
        rule_id=rule_check.id,
        field_template_id=rule_check.field_template_id,
        field_key=field_key,
        rule_type=rule_check.rule_type,
        mode=rule_check.mode,
        severity=rule_check.severity,
        message=rule_check.message,
        failed_value=value,
        evaluation_context=hashlib.sha256(json.dumps({
            "rule_type": rule_check.rule_type, "mode": rule_check.mode, "severity": rule_check.severity,
            "expression": rule_check.expression, "value": value,
            "evaluated_expression": _normalize_expression(rule_check.expression, value,
                payload_map=payload_map, form_context=form_context),
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
    )


class DataCaptureFieldValidationRulesService:
    def __init__(self, repository):
        self.repository = repository

    def check_field_validation_rules(
        self,
        *,
        crf_template_id: int,
        payload_data: str,
        subject_id: int | None = None,
        visit_id: int | None = None,
        include_hard: bool = False,
        evaluate_all: bool = False,
    ) -> FieldValidationCheckResult:
        try:
            payload_map = json.loads(payload_data or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload_map = {}
        if not isinstance(payload_map, dict):
            payload_map = {}
        current_form_payload_map = flatten_form_data_for_export(
            normalize_form_data(payload_map, strict=False),
            repeat_strategy="legacy_repeat_suffix",
        )
        rules_by_field_key = self.repository.list_form_field_validation_rules(
            crf_template_id=crf_template_id, **({"include_hard": True} if include_hard else {}))
        referenced_form_codes = _extract_referenced_form_codes(rules_by_field_key)
        form_context = (
            self.repository.get_subject_cross_form_validation_context(
                subject_id=subject_id,
                form_codes=referenced_form_codes,
                exclude_visit_id=visit_id,
                exclude_crf_template_id=crf_template_id,
            )
            if subject_id is not None and hasattr(self.repository, "get_subject_cross_form_validation_context")
            else {}
        )
        if referenced_form_codes and hasattr(self.repository, "get_form_repeatability_by_codes"):
            repeatability_by_code = self.repository.get_form_repeatability_by_codes(form_codes=referenced_form_codes)
            for form_code in referenced_form_codes:
                form_context.setdefault(
                    form_code,
                    {
                        "is_repeatable": bool(repeatability_by_code.get(form_code)),
                        "rows": [],
                    },
                )
        payload_map = dict(current_form_payload_map)
        failures: list[FieldValidationFailure] = []
        for field_key, rules in rules_by_field_key.items():
            if not rules:
                continue
            field_value = payload_map.get(field_key)
            for rule in rules:
                failure = _field_validation_failure(
                    field_key=field_key,
                    rule=rule,
                    value=field_value,
                    payload_map=payload_map,
                    form_context=form_context,
                )
                if failure is not None:
                    failures.append(failure)
                    if not evaluate_all:
                        break
        failed_field_keys = tuple(sorted({failure.field_key for failure in failures}))
        return FieldValidationCheckResult(
            has_failures=bool(failures),
            failed_field_keys=failed_field_keys,
            failures=tuple(failures),
        )


__all__ = [
    "DataCaptureFieldValidationRulesService",
    "FieldValidationFailure",
    "FieldValidationCheckResult",
    "FieldValidationRuleCheck",
    "check_field_err",
]
