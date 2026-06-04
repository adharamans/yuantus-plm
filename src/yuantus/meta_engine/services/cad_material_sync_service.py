"""
CAD Material Sync — shared profile compose/validate/match/create primitives.

Phase 1 extraction from plugins/yuantus-cad-material-sync/main.py. These operate on
already-resolved profile dicts (load_profiles / governance / versioning / config stay
in the plugin). The plugin re-exports every name below so existing routes and the
importlib-based regression test keep referencing them as module attributes.
"""
import re
from string import Formatter
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import String, cast

from yuantus.meta_engine.models.item import Item


DEFAULT_MATCH_STRATEGIES: List[List[str]] = [
    ["item_number"],
    ["drawing_no"],
    ["material_code"],
    ["material_category", "material", "specification"],
    ["material", "specification"],
]


UNIT_FACTORS_TO_MM = {
    "mm": 1.0,
    "millimeter": 1.0,
    "millimeters": 1.0,
    "毫米": 1.0,
    "cm": 10.0,
    "centimeter": 10.0,
    "centimeters": 10.0,
    "厘米": 10.0,
    "m": 1000.0,
    "meter": 1000.0,
    "meters": 1000.0,
    "米": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    "\"": 25.4,
}


NUMBER_WITH_UNIT_RE = re.compile(
    r"^\s*(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<unit>[A-Za-z\"毫米厘米米]+)?\s*$"
)


class UnitConversionError(ValueError):
    pass


def _json_text(expr):
    if hasattr(expr, "as_string"):
        return expr.as_string()
    if hasattr(expr, "astext"):
        return expr.astext
    return cast(expr, String)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _field_error(field: str, code: str, message: str) -> Dict[str, Any]:
    return {"field": field, "code": code, "message": message}


def _condition_values_equal(left: Any, right: Any) -> bool:
    if left == right:
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        return str(left).strip().lower() == str(right).strip().lower()
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left).strip().lower() == str(right).strip().lower()


def _condition_value_in(actual: Any, expected_values: Any) -> bool:
    if isinstance(expected_values, (list, tuple, set)):
        candidates = expected_values
    else:
        candidates = [expected_values]
    return any(_condition_values_equal(actual, expected) for expected in candidates)


def _condition_matches(condition: Any, values: Dict[str, Any]) -> bool:
    if not condition:
        return True
    if isinstance(condition, list):
        return all(_condition_matches(entry, values) for entry in condition)
    if not isinstance(condition, dict):
        return True

    if "all" in condition:
        return all(_condition_matches(entry, values) for entry in condition.get("all") or [])
    if "any" in condition:
        return any(_condition_matches(entry, values) for entry in condition.get("any") or [])
    if "none" in condition:
        return not any(_condition_matches(entry, values) for entry in condition.get("none") or [])

    field_name = str(condition.get("field") or condition.get("name") or "").strip()
    if not field_name:
        return True
    actual = (values or {}).get(field_name)

    if "exists" in condition:
        exists = not _is_blank(actual)
        return exists if bool(condition.get("exists")) else not exists
    if "missing" in condition:
        missing = _is_blank(actual)
        return missing if bool(condition.get("missing")) else not missing
    if "blank" in condition:
        blank = _is_blank(actual)
        return blank if bool(condition.get("blank")) else not blank

    operator = str(condition.get("op") or condition.get("operator") or "").strip().lower()
    if "equals" in condition:
        return _condition_values_equal(actual, condition.get("equals"))
    if "not_equals" in condition:
        return not _condition_values_equal(actual, condition.get("not_equals"))
    if "in" in condition:
        return _condition_value_in(actual, condition.get("in"))
    if "not_in" in condition:
        return not _condition_value_in(actual, condition.get("not_in"))
    if "contains" in condition:
        expected = condition.get("contains")
        if isinstance(actual, (list, tuple, set)):
            return _condition_value_in(expected, actual)
        return str(expected) in str(actual or "")
    if "regex" in condition:
        return bool(re.search(str(condition.get("regex") or ""), str(actual or "")))

    if operator in {"eq", "equals", "=", "=="}:
        return _condition_values_equal(actual, condition.get("value"))
    if operator in {"ne", "not_equals", "!=", "<>"}:
        return not _condition_values_equal(actual, condition.get("value"))
    if operator == "in":
        return _condition_value_in(actual, condition.get("value"))
    if operator in {"not_in", "nin"}:
        return not _condition_value_in(actual, condition.get("value"))
    return True


def _field_active(field: Dict[str, Any], values: Dict[str, Any]) -> bool:
    return _condition_matches(
        field.get("when") or field.get("condition") or field.get("visible_when"),
        values,
    )


def _field_required(field: Dict[str, Any], values: Dict[str, Any]) -> bool:
    if bool(field.get("required")):
        return True
    if "required_when" in field:
        return _condition_matches(field.get("required_when"), values)
    return False


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return ("%f" % value).rstrip("0").rstrip(".")


def _normalize_unit(unit: Optional[Any]) -> Optional[str]:
    if unit is None:
        return None
    text = str(unit).strip()
    return text.lower() if text else None


def _unit_factor(unit: Optional[Any]) -> Optional[float]:
    normalized = _normalize_unit(unit)
    if not normalized:
        return None
    return UNIT_FACTORS_TO_MM.get(normalized)


def _convert_unit(value: float, from_unit: Optional[Any], to_unit: Optional[Any]) -> float:
    source = _normalize_unit(from_unit)
    target = _normalize_unit(to_unit)
    if not source or not target or source == target:
        return value

    source_factor = _unit_factor(source)
    target_factor = _unit_factor(target)
    if source_factor is None or target_factor is None:
        raise UnitConversionError(f"Unsupported unit conversion: {source} -> {target}")
    return value * source_factor / target_factor


def _parse_number_with_unit(raw: Any) -> Tuple[Optional[float], Optional[str]]:
    if isinstance(raw, (int, float)):
        return float(raw), None
    text = str(raw).strip()
    if not text:
        return None, None
    match = NUMBER_WITH_UNIT_RE.match(text)
    if not match:
        raise ValueError("expected number")
    return float(match.group("value")), match.group("unit")


def _coerce_field_value(field: Dict[str, Any], raw: Any) -> Any:
    field_type = str(field.get("type") or field.get("data_type") or "string").lower()
    if raw is None:
        return None
    if field_type in {"number", "float"}:
        raw_unit = None
        raw_value = raw
        if isinstance(raw, dict):
            raw_value = raw.get("value")
            raw_unit = raw.get("unit") or raw.get("input_unit")
        value, parsed_unit = _parse_number_with_unit(raw_value)
        if value is None:
            return None
        source_unit = parsed_unit or raw_unit or field.get("input_unit") or field.get("cad_unit")
        target_unit = field.get("unit")
        return _convert_unit(value, source_unit, target_unit)
    if field_type == "integer":
        if isinstance(raw, int):
            return raw
        text = str(raw).strip()
        if not text:
            return None
        return int(text)
    if field_type == "boolean":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
        raise ValueError("expected boolean")
    if field_type in {"json", "object"}:
        return raw
    text = str(raw).strip()
    return text or None


def validate_profile_values(
    profile: Dict[str, Any], values: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    normalized = dict(values or {})
    errors: List[Dict[str, Any]] = []

    for field in profile.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        raw = normalized.get(name)
        active = _field_active(field, normalized)
        required = active and _field_required(field, normalized)
        if not active and _is_blank(raw):
            normalized.pop(name, None)
            continue
        if _is_blank(raw) and "default" in field:
            raw = field.get("default")
        if _is_blank(raw):
            if required:
                errors.append(_field_error(name, "required", f"{name} is required"))
            normalized.pop(name, None)
            continue
        try:
            value = _coerce_field_value(field, raw)
        except UnitConversionError as exc:
            errors.append(
                _field_error(
                    name,
                    "invalid_unit",
                    str(exc),
                )
            )
            continue
        except Exception:
            expected_type = str(field.get("type") or field.get("data_type") or "string")
            error_code = {
                "number": "invalid_number",
                "float": "invalid_number",
                "integer": "invalid_integer",
                "boolean": "invalid_boolean",
            }.get(expected_type, "invalid_type")
            errors.append(
                _field_error(
                    name,
                    error_code,
                    f"{name} expects {expected_type}",
                )
            )
            continue

        if "enum" in field and value not in (field.get("enum") or []):
            errors.append(_field_error(name, "invalid_enum", f"{name} is not allowed"))
        if isinstance(value, (int, float)):
            min_value = field.get("min")
            max_value = field.get("max")
            if min_value is not None and value < float(min_value):
                errors.append(_field_error(name, "min", f"{name} is below minimum"))
            if max_value is not None and value > float(max_value):
                errors.append(_field_error(name, "max", f"{name} is above maximum"))
        normalized[name] = value

    return normalized, errors


def _template_fields(template: str) -> List[str]:
    fields: List[str] = []
    for _literal, field_name, _format_spec, _conversion in Formatter().parse(template):
        if field_name:
            fields.append(field_name)
    return fields


def _field_map(profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(field.get("name") or "").strip(): field
        for field in profile.get("fields") or []
        if isinstance(field, dict) and str(field.get("name") or "").strip()
    }


def _display_value(value: Any, field: Optional[Dict[str, Any]] = None) -> str:
    if isinstance(value, float):
        display_value = value
        if field:
            display_value = _convert_unit(
                display_value,
                field.get("unit"),
                field.get("display_unit"),
            )
        display_format = (field or {}).get("display_format") if field else None
        if display_format:
            text = format(display_value, str(display_format))
        elif field and "display_precision" in field:
            precision = int(field.get("display_precision") or 0)
            text = f"{display_value:.{precision}f}"
            if field.get("trim_zeros", True):
                text = text.rstrip("0").rstrip(".")
        else:
            text = _format_number(display_value)
        suffix = str((field or {}).get("display_suffix") or "")
        return f"{text}{suffix}"
    if isinstance(value, int):
        suffix = str((field or {}).get("display_suffix") or "")
        return f"{value}{suffix}"
    return str(value)


def render_template(
    template: str,
    values: Dict[str, Any],
    fields_by_name: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    replacements: Dict[str, str] = {}
    for name in _template_fields(template):
        if name not in values or _is_blank(values.get(name)):
            raise ValueError(f"Missing template field: {name}")
        replacements[name] = _display_value(
            values[name],
            (fields_by_name or {}).get(name),
        )
    return template.format_map(replacements)


def compose_profile(
    profile: Dict[str, Any], values: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], List[str]]:
    selector = profile.get("selector") or {}
    validation_values: Dict[str, Any] = {}
    if isinstance(selector, dict):
        validation_values.update(selector)
    validation_values.update(values or {})
    normalized, errors = validate_profile_values(profile, validation_values)
    warnings: List[str] = []
    composed: Dict[str, Any] = {}

    compose = profile.get("compose") or {}
    target = str(compose.get("target") or "specification").strip()
    template = str(compose.get("template") or "").strip()
    if target and template:
        try:
            input_target = normalized.get(target)
            composed[target] = render_template(template, normalized, _field_map(profile))
            if (
                not _is_blank(input_target)
                and str(input_target) != str(composed[target])
            ):
                warnings.append(
                    f"derived_field_mismatch:{target}: input value "
                    f"{input_target!r} was replaced by {composed[target]!r}"
                )
            normalized[target] = composed[target]
        except ValueError as exc:
            errors.append(_field_error(target, "compose_failed", str(exc)))
    elif target:
        warnings.append("Profile has no compose template")

    if isinstance(selector, dict):
        for key, value in selector.items():
            normalized.setdefault(key, value)

    return normalized, composed, errors, warnings


def _match_strategies(profile: Dict[str, Any]) -> List[List[str]]:
    matching = profile.get("matching") or {}
    raw_strategies = (
        matching.get("strategies")
        if isinstance(matching, dict)
        else None
    )
    raw_strategies = raw_strategies or profile.get("match_strategies")
    if not raw_strategies:
        return DEFAULT_MATCH_STRATEGIES

    strategies: List[List[str]] = []
    for raw in raw_strategies:
        if isinstance(raw, str):
            fields = [raw]
        elif isinstance(raw, dict):
            fields = raw.get("fields") or []
        else:
            fields = raw
        normalized = [
            str(field).strip()
            for field in (fields or [])
            if str(field).strip()
        ]
        if normalized:
            strategies.append(normalized)
    return strategies or DEFAULT_MATCH_STRATEGIES


def _query_matching_items(
    db,
    *,
    item_type: str,
    criteria: List[Tuple[str, Any]],
    limit: int = 20,
) -> List[Any]:
    query = db.query(Item).filter(Item.item_type_id == item_type)
    for key, value in criteria:
        query = query.filter(_json_text(Item.properties[key]) == str(value))
    return list(query.limit(limit).all())


def _find_matching_items(db, profile: Dict[str, Any], values: Dict[str, Any]) -> List[Any]:
    if db is None:
        return []
    item_type = str(profile.get("item_type") or "Part")
    try:
        for strategy in _match_strategies(profile):
            criteria = [
                (field, (values or {}).get(field))
                for field in strategy
                if not _is_blank((values or {}).get(field))
            ]
            if len(criteria) != len(strategy):
                continue
            matches = _query_matching_items(
                db,
                item_type=item_type,
                criteria=criteria,
            )
            if matches:
                return matches
        return []
    except Exception:
        return []


def _user_identity(user: Any) -> Tuple[Optional[str], List[str]]:
    identity = str(getattr(user, "id", "") or "") or None
    roles = list(getattr(user, "roles", None) or [])
    if getattr(user, "is_superuser", False) and "superuser" not in roles:
        roles.append("superuser")
    return identity, roles


def _apply_item_update(db, item: Item, updates: Dict[str, Any], user: Any) -> None:
    if not updates:
        return
    from yuantus.meta_engine.schemas.aml import AMLAction, GenericItem
    from yuantus.meta_engine.services.engine import AMLEngine

    identity, roles = _user_identity(user)
    engine = AMLEngine(db, identity_id=identity, roles=roles)
    engine.apply(
        GenericItem(
            id=item.id,
            type=item.item_type_id,
            action=AMLAction.update,
            properties=updates,
        )
    )
    db.commit()


def _apply_item_create(
    db,
    profile: Dict[str, Any],
    properties: Dict[str, Any],
    user: Any,
) -> Optional[str]:
    from yuantus.meta_engine.schemas.aml import AMLAction, GenericItem
    from yuantus.meta_engine.services.engine import AMLEngine

    identity, roles = _user_identity(user)
    engine = AMLEngine(db, identity_id=identity, roles=roles)
    result = engine.apply(
        GenericItem(
            type=str(profile.get("item_type") or "Part"),
            action=AMLAction.add,
            properties=properties,
        )
    )
    db.commit()
    return result.get("id") if isinstance(result, dict) else None
