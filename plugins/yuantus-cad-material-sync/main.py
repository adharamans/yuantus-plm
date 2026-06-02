from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from string import Formatter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import String, cast

from yuantus.context import get_request_context
from yuantus.integrations.cad_connectors.base import normalize_cad_key
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.services.plugin_config_service import PluginConfigService


PLUGIN_ID = "yuantus-cad-material-sync"

router = APIRouter(
    prefix="/plugins/cad-material-sync",
    tags=["plugins-cad-material-sync"],
)


DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
    "sheet": {
        "profile_id": "sheet",
        "label": "板材",
        "item_type": "Part",
        "selector": {"material_category": "sheet"},
        "fields": [
            {
                "name": "material_category",
                "label": "物料类别",
                "type": "string",
                "default": "sheet",
                "required": True,
                "cad_key": "物料类别",
            },
            {
                "name": "material",
                "label": "材料",
                "type": "string",
                "required": True,
                "cad_key": "材料",
            },
            {
                "name": "length",
                "label": "长",
                "type": "number",
                "required": True,
                "unit": "mm",
                "cad_key": "长",
            },
            {
                "name": "width",
                "label": "宽",
                "type": "number",
                "required": True,
                "unit": "mm",
                "cad_key": "宽",
            },
            {
                "name": "thickness",
                "label": "厚",
                "type": "number",
                "required": True,
                "unit": "mm",
                "cad_key": "厚",
            },
        ],
        "compose": {"target": "specification", "template": "{length}*{width}*{thickness}"},
        "cad_mapping": {
            "item_number": "图号",
            "name": "名称",
            "material": "材料",
            "specification": "规格",
        },
    },
    "tube": {
        "profile_id": "tube",
        "label": "管材",
        "item_type": "Part",
        "selector": {"material_category": "tube"},
        "fields": [
            {
                "name": "material_category",
                "label": "物料类别",
                "type": "string",
                "default": "tube",
                "required": True,
                "cad_key": "物料类别",
            },
            {
                "name": "material",
                "label": "材料",
                "type": "string",
                "required": True,
                "cad_key": "材料",
            },
            {
                "name": "outer_diameter",
                "label": "外径",
                "type": "number",
                "required": True,
                "unit": "mm",
                "cad_key": "外径",
            },
            {
                "name": "wall_thickness",
                "label": "壁厚",
                "type": "number",
                "required": True,
                "unit": "mm",
                "cad_key": "壁厚",
            },
            {
                "name": "length",
                "label": "长度",
                "type": "number",
                "required": True,
                "unit": "mm",
                "cad_key": "长度",
            },
        ],
        "compose": {
            "target": "specification",
            "template": "Φ{outer_diameter}*{wall_thickness}*{length}",
        },
        "cad_mapping": {
            "item_number": "图号",
            "name": "名称",
            "material": "材料",
            "specification": "规格",
        },
    },
    "bar": {
        "profile_id": "bar",
        "label": "棒材",
        "item_type": "Part",
        "selector": {"material_category": "bar"},
        "fields": [
            {
                "name": "material_category",
                "label": "物料类别",
                "type": "string",
                "default": "bar",
                "required": True,
                "cad_key": "物料类别",
            },
            {
                "name": "material",
                "label": "材料",
                "type": "string",
                "required": True,
                "cad_key": "材料",
            },
            {
                "name": "diameter",
                "label": "直径",
                "type": "number",
                "required": True,
                "unit": "mm",
                "cad_key": "直径",
            },
            {
                "name": "length",
                "label": "长度",
                "type": "number",
                "required": True,
                "unit": "mm",
                "cad_key": "长度",
            },
        ],
        "compose": {"target": "specification", "template": "Φ{diameter}*{length}"},
        "cad_mapping": {
            "item_number": "图号",
            "name": "名称",
            "material": "材料",
            "specification": "规格",
        },
    },
    "forging": {
        "profile_id": "forging",
        "label": "锻件",
        "item_type": "Part",
        "selector": {"material_category": "forging"},
        "fields": [
            {
                "name": "material_category",
                "label": "物料类别",
                "type": "string",
                "default": "forging",
                "required": True,
                "cad_key": "物料类别",
            },
            {
                "name": "material",
                "label": "材料",
                "type": "string",
                "required": True,
                "cad_key": "材料",
            },
            {
                "name": "blank_size",
                "label": "毛坯尺寸",
                "type": "string",
                "required": True,
                "cad_key": "毛坯尺寸",
            },
            {
                "name": "heat_treatment",
                "label": "热处理",
                "type": "string",
                "required": False,
                "cad_key": "热处理",
            },
        ],
        "compose": {"target": "specification", "template": "{blank_size}"},
        "cad_mapping": {
            "item_number": "图号",
            "name": "名称",
            "material": "材料",
            "specification": "规格",
            "heat_treatment": "热处理",
        },
    },
}

# finishing/treatment R1 (taskbook #689): `finish` (surface finish / coating) is an
# OPTIONAL attribute on EVERY default profile — not a new table, route, or service.
# Its `cad_key` is packaged back by `cad_field_package`'s field-level mapping, so no
# per-profile `cad_mapping` edit is needed. `finish_standard` stays a
# tenant/profile-configured companion (via `required_when`), and heat treatment stays
# forging-only by default — neither is baked in here.
_FINISH_FIELD = {
    "name": "finish",
    "label": "表面处理",
    "type": "string",
    "required": False,
    "cad_key": "表面处理",
}
for _profile in DEFAULT_PROFILES.values():
    _profile["fields"].append(dict(_FINISH_FIELD))
del _profile

PROFILE_ALIASES = {
    "板材": "sheet",
    "sheet": "sheet",
    "plate": "sheet",
    "管材": "tube",
    "tube": "tube",
    "pipe": "tube",
    "棒材": "bar",
    "bar": "bar",
    "rod": "bar",
    "锻件": "forging",
    "forging": "forging",
    "forge": "forging",
}

DEFAULT_MATCH_STRATEGIES: List[List[str]] = [
    ["item_number"],
    ["drawing_no"],
    ["material_code"],
    ["material_category", "material", "specification"],
    ["material", "specification"],
]

PROFILE_VERSION_CONTROL_KEYS = {
    "active_version",
    "active_versions",
    "default_version",
    "default_versions",
    "versions",
}

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


class ComposeRequest(BaseModel):
    profile_id: str
    values: Dict[str, Any] = Field(default_factory=dict)
    include_cad_fields: bool = True
    cad_system: Optional[str] = None


class ComposeResponse(BaseModel):
    ok: bool
    profile_id: str
    properties: Dict[str, Any] = Field(default_factory=dict)
    composed: Dict[str, Any] = Field(default_factory=dict)
    cad_fields: Dict[str, Any] = Field(default_factory=dict)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ValidateRequest(BaseModel):
    profile_id: str
    values: Dict[str, Any] = Field(default_factory=dict)
    lookup_existing: bool = False
    cad_system: Optional[str] = None


class ValidateResponse(BaseModel):
    ok: bool
    valid: bool
    profile_id: str
    normalized: Dict[str, Any] = Field(default_factory=dict)
    composed: Dict[str, Any] = Field(default_factory=dict)
    cad_fields: Dict[str, Any] = Field(default_factory=dict)
    matched_items: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ConfigPreviewRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)
    profile_id: Optional[str] = None
    values: Dict[str, Any] = Field(default_factory=dict)
    include_profiles: bool = True
    cad_system: Optional[str] = None


class ConfigPreviewResponse(BaseModel):
    ok: bool
    profile_id: Optional[str] = None
    profiles: List[Dict[str, Any]] = Field(default_factory=list)
    profile: Optional[Dict[str, Any]] = None
    preview: Dict[str, Any] = Field(default_factory=dict)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ConfigUpdateRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)
    merge: bool = False


class ConfigStoreResponse(BaseModel):
    ok: bool
    saved: bool = False
    deleted: bool = False
    scope: Dict[str, str] = Field(default_factory=dict)
    config: Dict[str, Any] = Field(default_factory=dict)
    profiles: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ConfigImportRequest(BaseModel):
    bundle: Dict[str, Any] = Field(default_factory=dict)
    merge: bool = False
    dry_run: bool = False


class ConfigBundleResponse(BaseModel):
    ok: bool
    imported: bool = False
    dry_run: bool = False
    scope: Dict[str, str] = Field(default_factory=dict)
    bundle: Dict[str, Any] = Field(default_factory=dict)
    config: Dict[str, Any] = Field(default_factory=dict)
    profiles: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class CadDiffPreviewRequest(BaseModel):
    profile_id: Optional[str] = None
    item_id: Optional[str] = None
    values: Dict[str, Any] = Field(default_factory=dict)
    target_properties: Dict[str, Any] = Field(default_factory=dict)
    target_cad_fields: Dict[str, Any] = Field(default_factory=dict)
    current_cad_fields: Dict[str, Any] = Field(default_factory=dict)
    include_empty: bool = False
    cad_system: Optional[str] = None


class CadDiffPreviewResponse(BaseModel):
    ok: bool
    profile_id: str
    item_id: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)
    current_cad_fields: Dict[str, Any] = Field(default_factory=dict)
    target_cad_fields: Dict[str, Any] = Field(default_factory=dict)
    write_cad_fields: Dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False
    diffs: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, int] = Field(default_factory=dict)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class SyncOutboundRequest(BaseModel):
    profile_id: Optional[str] = None
    item_id: Optional[str] = None
    values: Dict[str, Any] = Field(default_factory=dict)
    include_empty: bool = False
    cad_system: Optional[str] = None


class SyncOutboundResponse(BaseModel):
    ok: bool
    profile_id: str
    item_id: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)
    cad_fields: Dict[str, Any] = Field(default_factory=dict)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class SyncInboundRequest(BaseModel):
    profile_id: Optional[str] = None
    item_id: Optional[str] = None
    lookup_properties: Dict[str, Any] = Field(default_factory=dict)
    values: Dict[str, Any] = Field(default_factory=dict)
    cad_fields: Dict[str, Any] = Field(default_factory=dict)
    overwrite: bool = False
    create_if_missing: bool = False
    dry_run: bool = False
    cad_system: Optional[str] = None


class SyncInboundResponse(BaseModel):
    ok: bool
    action: str
    profile_id: str
    item_id: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)
    updates: Dict[str, Any] = Field(default_factory=dict)
    cad_fields: Dict[str, Any] = Field(default_factory=dict)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    matched_items: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    dry_run: bool = False


def _get_db():
    from yuantus.database import get_db

    yield from get_db()


def _get_identity_db():
    from yuantus.security.auth.database import get_identity_db

    yield from get_identity_db()


def _current_user(
    request: Request,
    identity_db=Depends(_get_identity_db),
    db=Depends(_get_db),
):
    from yuantus.api.dependencies.auth import (
        get_current_user,
        get_current_user_optional,
    )

    user = get_current_user_optional(request, identity_db=identity_db, db=db)
    return get_current_user(user=user)


def _json_text(expr):
    if hasattr(expr, "as_string"):
        return expr.as_string()
    if hasattr(expr, "astext"):
        return expr.astext
    return cast(expr, String)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _clean_profile_id(value: Optional[Any]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return PROFILE_ALIASES.get(text, PROFILE_ALIASES.get(text.lower(), text))


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _profile_entries_from_config(config: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    raw_profiles = (config or {}).get("profiles")
    if isinstance(raw_profiles, dict):
        for key, raw in raw_profiles.items():
            if not isinstance(raw, dict):
                continue
            entry = dict(raw)
            entry.setdefault("profile_id", key)
            yield entry
    elif isinstance(raw_profiles, list):
        for raw in raw_profiles:
            if isinstance(raw, dict):
                yield dict(raw)


def _profile_base_override(override: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in (override or {}).items()
        if key not in PROFILE_VERSION_CONTROL_KEYS
    }


def _profile_version_entries(override: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    raw_versions = (override or {}).get("versions")
    entries: List[Tuple[str, Dict[str, Any]]] = []
    if isinstance(raw_versions, dict):
        iterable = raw_versions.items()
    elif isinstance(raw_versions, list):
        iterable = []
        for raw in raw_versions:
            if not isinstance(raw, dict):
                continue
            version_key = raw.get("version") or raw.get("profile_version") or raw.get("id")
            iterable.append((version_key, raw))
    else:
        iterable = []

    for raw_key, raw_entry in iterable:
        if not isinstance(raw_entry, dict):
            continue
        version_key = str(
            raw_entry.get("version")
            or raw_entry.get("profile_version")
            or raw_entry.get("id")
            or raw_key
            or ""
        ).strip()
        if not version_key:
            continue
        entry = dict(raw_entry)
        entry.setdefault("profile_version", version_key)
        entries.append((version_key, entry))
    return entries


def _profile_version_enabled(entry: Dict[str, Any]) -> bool:
    if entry.get("enabled") is False or entry.get("disabled") is True:
        return False
    status = str(entry.get("status") or "").strip().lower()
    return status not in {"disabled", "retired", "archived"}


def _list_contains(values: Any, needle: Optional[Any]) -> bool:
    if needle is None:
        return False
    if isinstance(values, (list, tuple, set)):
        candidates = values
    else:
        candidates = [values]
    return any(str(value).strip() == str(needle).strip() for value in candidates)


def _rollout_percent_matches(
    percent: Any,
    *,
    profile_id: str,
    version_key: str,
    tenant_id: Optional[str],
    org_id: Optional[str],
    user_id: Optional[str],
) -> bool:
    if percent is None:
        return True
    try:
        threshold = float(percent)
    except (TypeError, ValueError):
        return False
    if threshold <= 0:
        return False
    if threshold >= 100:
        return True
    bucket_key = f"{profile_id}:{version_key}:{tenant_id or ''}:{org_id or ''}:{user_id or ''}"
    digest = hashlib.sha256(bucket_key.encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 100) < threshold


def _rollout_matches(profile_id: str, version_key: str, entry: Dict[str, Any]) -> bool:
    rollout = entry.get("rollout") or entry.get("gray_release")
    if not isinstance(rollout, dict):
        return False
    if rollout.get("enabled") is False:
        return False

    try:
        ctx = get_request_context()
    except Exception:
        ctx = None
    tenant_id = getattr(ctx, "tenant_id", None)
    org_id = getattr(ctx, "org_id", None)
    user_id = getattr(ctx, "user_id", None)

    if "tenant_id" in rollout and str(rollout.get("tenant_id")) != str(tenant_id):
        return False
    if "tenant_ids" in rollout and not _list_contains(rollout.get("tenant_ids"), tenant_id):
        return False
    if "org_id" in rollout and str(rollout.get("org_id")) != str(org_id):
        return False
    if "org_ids" in rollout and not _list_contains(rollout.get("org_ids"), org_id):
        return False
    if "user_id" in rollout and str(rollout.get("user_id")) != str(user_id):
        return False
    if "user_ids" in rollout and not _list_contains(rollout.get("user_ids"), user_id):
        return False

    percent = (
        rollout.get("percent")
        if "percent" in rollout
        else rollout.get("percentage", rollout.get("traffic_percent"))
    )
    if not _rollout_percent_matches(
        percent,
        profile_id=profile_id,
        version_key=version_key,
        tenant_id=tenant_id,
        org_id=org_id,
        user_id=user_id,
    ):
        return False

    has_selector = any(
        key in rollout
        for key in (
            "tenant_id",
            "tenant_ids",
            "org_id",
            "org_ids",
            "user_id",
            "user_ids",
            "percent",
            "percentage",
            "traffic_percent",
        )
    )
    return bool(rollout.get("enabled", has_selector))


def _config_version_lookup(raw: Any, profile_id: str) -> Optional[str]:
    if isinstance(raw, dict):
        value = raw.get(profile_id)
    else:
        value = raw
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _select_profile_version(
    profile_id: str,
    override: Dict[str, Any],
    config: Dict[str, Any],
) -> Tuple[Optional[str], Optional[Dict[str, Any]], List[str]]:
    entries = _profile_version_entries(override)
    version_keys = [version_key for version_key, _entry in entries]
    if not entries:
        return None, None, version_keys

    active_version = _config_version_lookup(
        (config or {}).get("active_versions"),
        profile_id,
    ) or _config_version_lookup((override or {}).get("active_version"), profile_id)
    if active_version:
        for version_key, entry in entries:
            if version_key == active_version and _profile_version_enabled(entry):
                return version_key, entry, version_keys
        return None, None, version_keys

    for version_key, entry in entries:
        if _profile_version_enabled(entry) and _rollout_matches(profile_id, version_key, entry):
            return version_key, entry, version_keys

    default_version = _config_version_lookup(
        (config or {}).get("default_versions"),
        profile_id,
    ) or _config_version_lookup((override or {}).get("default_version"), profile_id)
    if default_version:
        for version_key, entry in entries:
            if version_key == default_version and _profile_version_enabled(entry):
                return version_key, entry, version_keys

    return None, None, version_keys


def _field_label(field: Dict[str, Any], name: str) -> str:
    return str(field.get("label") or field.get("title") or name)


def _default_profile_governance(profile: Dict[str, Any]) -> Dict[str, Any]:
    compose = profile.get("compose") or {}
    target = str(compose.get("target") or "specification").strip()
    template = str(compose.get("template") or "").strip()
    template_sources = _template_fields(template) if template else []

    source_fields: List[Dict[str, Any]] = []
    for field in profile.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name or name == target:
            continue
        source_fields.append(
            {
                "name": name,
                "label": _field_label(field, name),
                "type": str(field.get("type") or field.get("data_type") or "string"),
                "unit": field.get("unit"),
                "cad_keys": _field_cad_keys(field),
                "role": "source_of_truth",
            }
        )

    governance: Dict[str, Any] = {
        "source_fields": source_fields,
        "dynamic_property_templates": {
            "property_name": "{profile_id}_{field_name}",
            "cad_key": "{field_label}",
            "custom_field_prefix": "material_",
            "naming_style": "snake_case",
        },
    }
    if target:
        governance["derived_fields"] = {
            target: {
                "role": "derived_cache",
                "cache": True,
                "source_of_truth": False,
                "sources": template_sources,
                "template": template,
                "recompute_policy": "recompute_from_source_fields",
                "mismatch_warning": "derived_field_mismatch",
            }
        }
    return governance


def _apply_profile_governance(profile: Dict[str, Any]) -> None:
    configured = profile.get("governance") if isinstance(profile.get("governance"), dict) else {}
    profile["governance"] = _deep_merge(_default_profile_governance(profile), configured)


def _config_error(profile_id: str, code: str, message: str) -> Dict[str, Any]:
    return {"profile_id": profile_id, "code": code, "message": message}


def _diagnose_version_config(config: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for override in _profile_entries_from_config(config or {}):
        profile_id = _clean_profile_id(override.get("profile_id"))
        if not profile_id:
            errors.append(_config_error("", "missing_profile_id", "profile_id is required"))
            continue
        version_entries = _profile_version_entries(override)
        version_keys = [version_key for version_key, _entry in version_entries]
        if not version_keys:
            continue

        active_version = _config_version_lookup(
            (config or {}).get("active_versions"),
            profile_id,
        ) or _config_version_lookup(override.get("active_version"), profile_id)
        if active_version and active_version not in version_keys:
            errors.append(
                _config_error(
                    profile_id,
                    "unknown_active_version",
                    f"active_version {active_version!r} is not defined",
                )
            )

        default_version = _config_version_lookup(
            (config or {}).get("default_versions"),
            profile_id,
        ) or _config_version_lookup(override.get("default_version"), profile_id)
        if default_version and default_version not in version_keys:
            warnings.append(
                f"profile:{profile_id}: default_version {default_version!r} is not defined"
            )
    return errors, warnings


def _diagnose_profiles(profiles: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for profile_id, profile in sorted((profiles or {}).items()):
        field_names = {
            str(field.get("name") or "").strip()
            for field in profile.get("fields") or []
            if isinstance(field, dict) and str(field.get("name") or "").strip()
        }
        if not field_names:
            errors.append(_config_error(profile_id, "missing_fields", "profile has no fields"))

        compose = profile.get("compose") or {}
        target = str(compose.get("target") or "specification").strip()
        template = str(compose.get("template") or "").strip()
        template_fields = _template_fields(template) if template else []
        missing_template_fields = [
            field_name for field_name in template_fields if field_name not in field_names
        ]
        for field_name in missing_template_fields:
            errors.append(
                _config_error(
                    profile_id,
                    "unknown_template_field",
                    f"compose template references unknown field {field_name!r}",
                )
            )
        if target and target in field_names:
            warnings.append(
                f"profile:{profile_id}: derived target {target!r} is also declared as a source field"
            )

        cad_key_owner: Dict[str, str] = {}
        for prop_name, cad_spec in (profile.get("cad_mapping") or {}).items():
            for cad_key in _cad_key_entries(cad_spec):
                normalized_key = normalize_cad_key(cad_key)
                owner = cad_key_owner.get(normalized_key)
                if owner and owner != str(prop_name):
                    warnings.append(
                        f"profile:{profile_id}: CAD key {cad_key!r} maps to both "
                        f"{owner!r} and {prop_name!r}"
                    )
                cad_key_owner[normalized_key] = str(prop_name)
        for field in profile.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            if not name:
                errors.append(_config_error(profile_id, "missing_field_name", "field name is required"))
                continue
            for cad_key in _field_cad_keys(field):
                normalized_key = normalize_cad_key(cad_key)
                owner = cad_key_owner.get(normalized_key)
                if owner and owner != name:
                    warnings.append(
                        f"profile:{profile_id}: CAD key {cad_key!r} maps to both "
                        f"{owner!r} and {name!r}"
                    )
                cad_key_owner[normalized_key] = name
    return errors, warnings


def preview_profile_config(
    config: Dict[str, Any],
    *,
    profile_id: Optional[str] = None,
    values: Optional[Dict[str, Any]] = None,
    cad_system: Optional[str] = None,
) -> Dict[str, Any]:
    profiles = load_profiles(config=config or {})
    config_errors, config_warnings = _diagnose_version_config(config or {})
    profile_errors, profile_warnings = _diagnose_profiles(profiles)
    errors = config_errors + profile_errors
    warnings = config_warnings + profile_warnings

    selected_profile: Optional[Dict[str, Any]] = None
    preview: Dict[str, Any] = {}
    cleaned_profile_id = _clean_profile_id(profile_id) if profile_id else None
    if cleaned_profile_id:
        selected_profile = profiles.get(cleaned_profile_id)
        if not selected_profile:
            errors.append(
                _config_error(
                    cleaned_profile_id,
                    "profile_not_found",
                    f"Profile not found: {profile_id}",
                )
            )
    elif profiles:
        selected_profile = profiles[sorted(profiles.keys())[0]]
        cleaned_profile_id = str(selected_profile.get("profile_id") or "")

    if selected_profile and values:
        properties, composed, compose_errors, compose_warnings = compose_profile(
            selected_profile,
            values,
        )
        preview = {
            "properties": properties,
            "composed": composed,
            "cad_fields": cad_field_package(
                selected_profile,
                properties,
                cad_system=cad_system,
            ),
            "errors": compose_errors,
            "warnings": compose_warnings,
        }
        errors.extend(
            _config_error(
                str(selected_profile.get("profile_id") or cleaned_profile_id or ""),
                error.get("code", "compose_error"),
                str(error.get("message") or error),
            )
            for error in compose_errors
        )
        warnings.extend(compose_warnings)

    return {
        "ok": not errors,
        "profile_id": cleaned_profile_id,
        "profiles": [profiles[key] for key in sorted(profiles.keys())],
        "profile": selected_profile,
        "preview": preview,
        "errors": errors,
        "warnings": warnings,
    }


def _load_plugin_config(db) -> Dict[str, Any]:
    try:
        service = PluginConfigService(db)
        ctx = get_request_context()
        record = None
        if ctx.tenant_id and ctx.org_id:
            record = service.get_config(
                plugin_id=PLUGIN_ID,
                tenant_id=ctx.tenant_id,
                org_id=ctx.org_id,
            )
        if record is None and ctx.tenant_id:
            record = service.get_config(
                plugin_id=PLUGIN_ID,
                tenant_id=ctx.tenant_id,
                org_id=None,
            )
        if record is None:
            record = service.get_config(
                plugin_id=PLUGIN_ID,
                tenant_id=None,
                org_id=None,
            )
        config = getattr(record, "config", None)
        return dict(config or {}) if isinstance(config, dict) else {}
    except Exception:
        return {}


def _config_scope() -> Dict[str, str]:
    ctx = get_request_context()

    def _clean(value: Optional[str]) -> str:
        text = str(value).strip() if value is not None else ""
        return text or "default"

    return {
        "tenant_id": _clean(ctx.tenant_id),
        "org_id": _clean(ctx.org_id),
    }


def _current_plugin_config_record(db):
    service = PluginConfigService(db)
    scope = _config_scope()
    return service.get_config(
        plugin_id=PLUGIN_ID,
        tenant_id=scope["tenant_id"],
        org_id=scope["org_id"],
    )


def _is_admin_user(user: Any) -> bool:
    roles = {
        str(role).strip().lower()
        for role in (getattr(user, "roles", None) or [])
        if str(role).strip()
    }
    return bool(getattr(user, "is_superuser", False)) or bool(
        roles.intersection({"admin", "superuser"})
    )


def _require_admin_user(user: Any) -> None:
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Admin role required")


def _user_id_for_config(user: Any) -> Optional[int]:
    raw = getattr(user, "id", None)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _stable_config_hash(config: Dict[str, Any]) -> str:
    payload = json.dumps(config or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_stamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _config_export_bundle(config: Dict[str, Any], scope: Dict[str, str]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "plugin_id": PLUGIN_ID,
        "exported_at": _utc_stamp(),
        "scope": dict(scope or {}),
        "config_hash": _stable_config_hash(config or {}),
        "config": copy.deepcopy(config or {}),
    }


def _config_from_import_bundle(bundle: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if not isinstance(bundle, dict):
        return None, [_config_error("", "invalid_bundle", "bundle must be an object")], warnings

    plugin_id = bundle.get("plugin_id")
    if plugin_id and str(plugin_id) != PLUGIN_ID:
        errors.append(
            _config_error(
                "",
                "plugin_mismatch",
                f"bundle plugin_id {plugin_id!r} does not match {PLUGIN_ID!r}",
            )
        )
    schema_version = bundle.get("schema_version", 1)
    if schema_version != 1:
        errors.append(
            _config_error(
                "",
                "unsupported_schema_version",
                f"Unsupported config bundle schema_version {schema_version!r}",
            )
        )

    config = bundle.get("config")
    if not isinstance(config, dict):
        errors.append(_config_error("", "missing_config", "bundle.config must be an object"))
        return None, errors, warnings

    expected_hash = bundle.get("config_hash")
    actual_hash = _stable_config_hash(config)
    if expected_hash and str(expected_hash) != actual_hash:
        errors.append(
            _config_error(
                "",
                "config_hash_mismatch",
                "bundle.config_hash does not match bundle.config",
            )
        )
    if not expected_hash:
        warnings.append("bundle has no config_hash")

    return dict(config), errors, warnings


def load_profiles(db=None, config: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    profiles = copy.deepcopy(DEFAULT_PROFILES)
    effective_config = config if config is not None else (_load_plugin_config(db) if db is not None else {})
    for override in _profile_entries_from_config(effective_config):
        profile_id = _clean_profile_id(override.get("profile_id"))
        if not profile_id:
            continue
        base_override = _profile_base_override(override)
        base_override["profile_id"] = profile_id
        merged = _deep_merge(profiles.get(profile_id, {}), base_override)

        version_key, version_override, version_keys = _select_profile_version(
            profile_id,
            override,
            effective_config,
        )
        if version_keys:
            merged["available_versions"] = version_keys
        if version_key and version_override:
            version_body = _profile_base_override(version_override)
            version_body["profile_id"] = profile_id
            version_body["profile_version"] = version_key
            merged = _deep_merge(merged, version_body)
        profiles[profile_id] = merged
    for profile in profiles.values():
        profile.setdefault(
            "matching",
            {"strategies": copy.deepcopy(DEFAULT_MATCH_STRATEGIES)},
        )
        _apply_profile_governance(profile)
    return profiles


def _get_profile(profiles: Dict[str, Dict[str, Any]], profile_id: Optional[str]) -> Dict[str, Any]:
    cleaned = _clean_profile_id(profile_id)
    if not cleaned:
        raise HTTPException(status_code=400, detail="profile_id is required")
    profile = profiles.get(cleaned)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile not found: {profile_id}")
    return profile


def infer_profile_id(values: Dict[str, Any], default_profile_id: str = "sheet") -> str:
    for key in ("profile_id", "material_profile", "material_category", "category"):
        cleaned = _clean_profile_id((values or {}).get(key))
        if cleaned:
            return cleaned
    return default_profile_id


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


def _append_cad_key(keys: List[str], raw: Any) -> None:
    if raw is None:
        return
    if isinstance(raw, dict):
        for key in (
            "primary",
            "default",
            "cad_key",
            "write",
            "name",
            "aliases",
            "cad_aliases",
            "cad_keys",
            "keys",
            "read",
            "read_aliases",
            "autocad",
            "zwcad",
            "solidworks",
            "inventor",
            "nx",
            "creo",
        ):
            _append_cad_key(keys, raw.get(key))
        for key in (
            "by_connector",
            "by_cad",
            "by_cad_system",
            "cad_systems",
            "connectors",
        ):
            nested = raw.get(key)
            if isinstance(nested, dict):
                for value in nested.values():
                    _append_cad_key(keys, value)
        return
    if isinstance(raw, (list, tuple, set)):
        for entry in raw:
            _append_cad_key(keys, entry)
        return

    text = str(raw).strip()
    if text and text not in keys:
        keys.append(text)


CAD_SYSTEM_ALIASES = {
    "auto_cad": "autocad",
    "auto-cad": "autocad",
    "autocad": "autocad",
    "dwg": "autocad",
    "zwcad": "zwcad",
    "zwsw": "zwcad",
    "solidworks": "solidworks",
    "solid_works": "solidworks",
    "solid-works": "solidworks",
    "sw": "solidworks",
    "inventor": "inventor",
    "nx": "nx",
    "creo": "creo",
}


def _normalize_cad_system(cad_system: Optional[Any]) -> Optional[str]:
    if cad_system is None:
        return None
    text = str(cad_system).strip().lower()
    if not text:
        return None
    return CAD_SYSTEM_ALIASES.get(text, text)


def _cad_system_key_variants(cad_system: Optional[Any]) -> List[str]:
    normalized = _normalize_cad_system(cad_system)
    if not normalized:
        return []
    variants = [normalized]
    if normalized == "solidworks":
        variants.extend(["solid_works", "solid-works", "sw"])
    elif normalized == "autocad":
        variants.extend(["auto_cad", "auto-cad", "dwg"])
    return variants


def _append_cad_system_key(keys: List[str], raw: Any, cad_system: Optional[Any]) -> None:
    if not isinstance(raw, dict):
        return
    variants = _cad_system_key_variants(cad_system)
    for key in variants:
        _append_cad_key(keys, raw.get(key))
    for key in (
        "by_connector",
        "by_cad",
        "by_cad_system",
        "cad_systems",
        "connectors",
    ):
        nested = raw.get(key)
        if isinstance(nested, dict):
            for variant in variants:
                _append_cad_key(keys, nested.get(variant))


def _cad_system_key_entries(raw: Any, cad_system: Optional[Any]) -> List[str]:
    keys: List[str] = []
    _append_cad_system_key(keys, raw, cad_system)
    return keys


def _cad_key_entries(raw: Any) -> List[str]:
    keys: List[str] = []
    _append_cad_key(keys, raw)
    return keys


def _field_cad_keys(
    field: Dict[str, Any],
    *,
    cad_system: Optional[Any] = None,
) -> List[str]:
    keys = _field_cad_system_keys(field, cad_system=cad_system)
    for key in (
        "cad_key",
        "cad_keys",
        "cad_aliases",
        "cad_key_aliases",
        "cad_key_by_connector",
        "cad_keys_by_connector",
        "cad_system_keys",
        "connector_keys",
    ):
        _append_cad_key(keys, field.get(key))
    return keys


def _field_cad_system_keys(
    field: Dict[str, Any],
    *,
    cad_system: Optional[Any] = None,
) -> List[str]:
    keys: List[str] = []
    for key in (
        "cad_key_by_connector",
        "cad_keys_by_connector",
        "cad_system_keys",
        "connector_keys",
        "cad_key",
        "cad_keys",
        "cad_aliases",
        "cad_key_aliases",
    ):
        _append_cad_system_key(keys, field.get(key), cad_system)
    _append_cad_system_key(keys, field, cad_system)
    return keys


def _primary_cad_key(raw: Any, *, cad_system: Optional[Any] = None) -> Optional[str]:
    keys = _cad_system_key_entries(raw, cad_system)
    _append_cad_key(keys, raw)
    return keys[0] if keys else None


def _cad_reverse_mapping(profile: Dict[str, Any]) -> Dict[str, str]:
    reverse: Dict[str, str] = {}
    for prop_name, cad_spec in (profile.get("cad_mapping") or {}).items():
        for cad_key in _cad_key_entries(cad_spec):
            reverse[normalize_cad_key(cad_key)] = str(prop_name)
    for field in profile.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if name:
            for cad_key in _field_cad_keys(field):
                reverse[normalize_cad_key(cad_key)] = name
    return reverse


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


def cad_field_package(
    profile: Dict[str, Any],
    properties: Dict[str, Any],
    *,
    include_empty: bool = False,
    cad_system: Optional[str] = None,
) -> Dict[str, Any]:
    package: Dict[str, Any] = {}
    mapping: Dict[str, str] = {}
    mapping_has_cad_system_key: Dict[str, bool] = {}
    for prop_name, cad_spec in (profile.get("cad_mapping") or {}).items():
        prop_key = str(prop_name)
        cad_system_keys = _cad_system_key_entries(cad_spec, cad_system)
        cad_key = cad_system_keys[0] if cad_system_keys else _primary_cad_key(cad_spec)
        if cad_key:
            mapping[prop_key] = cad_key
            mapping_has_cad_system_key[prop_key] = bool(cad_system_keys)
    for field in profile.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        cad_system_keys = _field_cad_system_keys(field, cad_system=cad_system)
        cad_keys = _field_cad_keys(field, cad_system=cad_system)
        cad_key = cad_keys[0] if cad_keys else None
        if name and cad_key:
            if name not in mapping or (
                cad_system_keys and not mapping_has_cad_system_key.get(name, False)
            ):
                mapping[name] = cad_key
                mapping_has_cad_system_key[name] = bool(cad_system_keys)

    for prop_name, cad_key in mapping.items():
        if not cad_key:
            continue
        value = (properties or {}).get(prop_name)
        if not include_empty and _is_blank(value):
            continue
        package[str(cad_key)] = value
    return package


def cad_fields_to_properties(profile: Dict[str, Any], cad_fields: Dict[str, Any]) -> Dict[str, Any]:
    if not cad_fields:
        return {}

    reverse = _cad_reverse_mapping(profile)

    properties: Dict[str, Any] = {}
    known_field_names = {
        str(field.get("name") or "").strip()
        for field in profile.get("fields") or []
        if isinstance(field, dict)
    }
    known_field_names.update((profile.get("cad_mapping") or {}).keys())
    for raw_key, value in cad_fields.items():
        key = str(raw_key).strip()
        prop_name = reverse.get(normalize_cad_key(key))
        if not prop_name and key in known_field_names:
            prop_name = key
        if prop_name:
            properties[prop_name] = value
    return properties


def _cad_values_equal(left: Any, right: Any) -> bool:
    if _is_blank(left) and _is_blank(right):
        return True
    if left == right:
        return True
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def _cad_diff_status(current: Any, target: Any) -> str:
    if _cad_values_equal(current, target):
        return "unchanged"
    if _is_blank(current) and not _is_blank(target):
        return "added"
    if not _is_blank(current) and _is_blank(target):
        return "cleared"
    return "changed"


def build_cad_field_diff(
    profile: Dict[str, Any],
    *,
    current_cad_fields: Dict[str, Any],
    target_cad_fields: Dict[str, Any],
    cad_system: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, Any]]:
    current_properties = cad_fields_to_properties(profile, current_cad_fields or {})
    current_package = cad_field_package(
        profile,
        current_properties,
        include_empty=True,
        cad_system=cad_system,
    )
    raw_current_by_key = {
        normalize_cad_key(str(key)): value
        for key, value in (current_cad_fields or {}).items()
    }
    reverse = _cad_reverse_mapping(profile)

    diffs: List[Dict[str, Any]] = []
    summary = {"added": 0, "changed": 0, "cleared": 0, "unchanged": 0}
    for cad_key in sorted((target_cad_fields or {}).keys(), key=str):
        target_value = (target_cad_fields or {}).get(cad_key)
        normalized_key = normalize_cad_key(str(cad_key))
        current_value = current_package.get(
            cad_key,
            raw_current_by_key.get(normalized_key),
        )
        status = _cad_diff_status(current_value, target_value)
        summary[status] += 1
        diffs.append(
            {
                "cad_key": str(cad_key),
                "property": reverse.get(normalized_key),
                "current": current_value,
                "target": target_value,
                "status": status,
            }
        )

    return diffs, summary, current_package


def cad_write_fields_from_diffs(diffs: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    write_fields: Dict[str, Any] = {}
    for diff in diffs or []:
        if not isinstance(diff, dict):
            continue
        if diff.get("status") in {"added", "changed", "cleared"}:
            cad_key = str(diff.get("cad_key") or "").strip()
            if cad_key:
                write_fields[cad_key] = diff.get("target")
    return write_fields


def _item_payload(item: Any) -> Dict[str, Any]:
    props = dict(getattr(item, "properties", None) or {})
    summary = {
        key: props.get(key)
        for key in (
            "item_number",
            "drawing_no",
            "material_code",
            "material_category",
            "material",
            "specification",
            "name",
        )
        if props.get(key) is not None
    }
    return {
        "id": getattr(item, "id", None),
        "item_type_id": getattr(item, "item_type_id", None),
        "state": getattr(item, "state", None),
        "properties": summary,
    }


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


def _build_updates(
    current: Dict[str, Any],
    incoming: Dict[str, Any],
    *,
    overwrite: bool,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    updates: Dict[str, Any] = {}
    conflicts: List[Dict[str, Any]] = []
    for key, value in (incoming or {}).items():
        if _is_blank(value):
            continue
        current_value = (current or {}).get(key)
        if _is_blank(current_value):
            updates[key] = value
        elif current_value == value:
            continue
        elif overwrite:
            updates[key] = value
        else:
            conflicts.append(
                {"field": key, "current": current_value, "incoming": value}
            )
    return updates, conflicts


def _model_field_was_set(model: BaseModel, field_name: str) -> bool:
    fields_set = getattr(model, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(model, "__fields_set__", set())
    return field_name in fields_set


def _coerce_optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return None


def _profile_default_overwrite(
    profile: Dict[str, Any],
) -> Tuple[bool, Optional[str], Optional[str]]:
    profile_id = str(profile.get("profile_id") or "")
    section = profile.get("sync_defaults")
    if not isinstance(section, dict) or "overwrite" not in section:
        return False, None, None
    parsed = _coerce_optional_bool(section.get("overwrite"))
    source = "sync_defaults.overwrite"
    if parsed is None:
        return (
            False,
            source,
            f"profile:{profile_id}: {source} must be boolean",
        )
    return parsed, source, None


def _effective_inbound_overwrite(
    req: SyncInboundRequest,
    profile: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    if _model_field_was_set(req, "overwrite"):
        return bool(req.overwrite), []

    default_overwrite, source, warning = _profile_default_overwrite(profile)
    warnings: List[str] = []
    if warning:
        warnings.append(warning)
    if default_overwrite and source:
        warnings.append(f"profile_default_overwrite_applied:{source}")
    return default_overwrite, warnings


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


@router.get("/profiles")
def list_profiles(
    db=Depends(_get_db),
    _user: Any = Depends(_current_user),
) -> Dict[str, Any]:
    profiles = load_profiles(db)
    return {
        "ok": True,
        "profiles": [profiles[key] for key in sorted(profiles.keys())],
    }


@router.get("/profiles/{profile_id}")
def get_profile(
    profile_id: str,
    db=Depends(_get_db),
    _user: Any = Depends(_current_user),
) -> Dict[str, Any]:
    profiles = load_profiles(db)
    return {"ok": True, "profile": _get_profile(profiles, profile_id)}


@router.get("/config", response_model=ConfigStoreResponse)
def get_material_profile_config(
    db=Depends(_get_db),
    _user: Any = Depends(_current_user),
) -> ConfigStoreResponse:
    record = _current_plugin_config_record(db)
    config = dict(getattr(record, "config", None) or {})
    profiles = load_profiles(config=config)
    return ConfigStoreResponse(
        ok=True,
        scope=_config_scope(),
        config=config,
        profiles=[profiles[key] for key in sorted(profiles.keys())],
    )


@router.put("/config", response_model=ConfigStoreResponse)
def update_material_profile_config(
    req: ConfigUpdateRequest,
    db=Depends(_get_db),
    current_user: Any = Depends(_current_user),
) -> ConfigStoreResponse:
    _require_admin_user(current_user)
    record = _current_plugin_config_record(db)
    existing_config = dict(getattr(record, "config", None) or {})
    next_config = (
        _deep_merge(existing_config, req.config)
        if req.merge and existing_config
        else dict(req.config or {})
    )
    preview = preview_profile_config(next_config)
    if preview["errors"]:
        return ConfigStoreResponse(
            ok=False,
            saved=False,
            scope=_config_scope(),
            config=next_config,
            profiles=preview["profiles"],
            errors=preview["errors"],
            warnings=preview["warnings"],
        )

    scope = _config_scope()
    service = PluginConfigService(db)
    saved = service.upsert_config(
        plugin_id=PLUGIN_ID,
        config=next_config,
        tenant_id=scope["tenant_id"],
        org_id=scope["org_id"],
        user_id=_user_id_for_config(current_user),
        merge=False,
    )
    saved_config = dict(getattr(saved, "config", None) or {})
    profiles = load_profiles(config=saved_config)
    return ConfigStoreResponse(
        ok=True,
        saved=True,
        scope=scope,
        config=saved_config,
        profiles=[profiles[key] for key in sorted(profiles.keys())],
        warnings=preview["warnings"],
    )


@router.delete("/config", response_model=ConfigStoreResponse)
def delete_material_profile_config(
    db=Depends(_get_db),
    current_user: Any = Depends(_current_user),
) -> ConfigStoreResponse:
    _require_admin_user(current_user)
    scope = _config_scope()
    service = PluginConfigService(db)
    deleted = service.delete_config(
        plugin_id=PLUGIN_ID,
        tenant_id=scope["tenant_id"],
        org_id=scope["org_id"],
    )
    return ConfigStoreResponse(
        ok=True,
        deleted=deleted,
        scope=scope,
        config={},
        profiles=[profile for _key, profile in sorted(load_profiles(config={}).items())],
    )


@router.get("/config/export", response_model=ConfigBundleResponse)
def export_material_profile_config(
    db=Depends(_get_db),
    _user: Any = Depends(_current_user),
) -> ConfigBundleResponse:
    record = _current_plugin_config_record(db)
    config = dict(getattr(record, "config", None) or {})
    scope = _config_scope()
    profiles = load_profiles(config=config)
    return ConfigBundleResponse(
        ok=True,
        scope=scope,
        bundle=_config_export_bundle(config, scope),
        config=config,
        profiles=[profiles[key] for key in sorted(profiles.keys())],
    )


@router.post("/config/import", response_model=ConfigBundleResponse)
def import_material_profile_config(
    req: ConfigImportRequest,
    db=Depends(_get_db),
    current_user: Any = Depends(_current_user),
) -> ConfigBundleResponse:
    _require_admin_user(current_user)
    imported_config, bundle_errors, bundle_warnings = _config_from_import_bundle(req.bundle)
    existing = _current_plugin_config_record(db)
    existing_config = dict(getattr(existing, "config", None) or {})
    next_config = (
        _deep_merge(existing_config, imported_config or {})
        if req.merge and imported_config is not None
        else dict(imported_config or {})
    )
    preview = preview_profile_config(next_config)
    errors = bundle_errors + preview["errors"]
    warnings = bundle_warnings + preview["warnings"]
    scope = _config_scope()
    if errors:
        return ConfigBundleResponse(
            ok=False,
            imported=False,
            dry_run=req.dry_run,
            scope=scope,
            bundle=req.bundle,
            config=next_config,
            profiles=preview["profiles"],
            errors=errors,
            warnings=warnings,
        )

    if not req.dry_run:
        service = PluginConfigService(db)
        service.upsert_config(
            plugin_id=PLUGIN_ID,
            config=next_config,
            tenant_id=scope["tenant_id"],
            org_id=scope["org_id"],
            user_id=_user_id_for_config(current_user),
            merge=False,
        )

    profiles = load_profiles(config=next_config)
    return ConfigBundleResponse(
        ok=True,
        imported=not req.dry_run,
        dry_run=req.dry_run,
        scope=scope,
        bundle=_config_export_bundle(next_config, scope),
        config=next_config,
        profiles=[profiles[key] for key in sorted(profiles.keys())],
        warnings=warnings,
    )


@router.post("/config/preview", response_model=ConfigPreviewResponse)
def preview_material_profile_config(
    req: ConfigPreviewRequest,
    _db=Depends(_get_db),
    _user: Any = Depends(_current_user),
) -> ConfigPreviewResponse:
    result = preview_profile_config(
        req.config,
        profile_id=req.profile_id,
        values=req.values,
        cad_system=req.cad_system,
    )
    if not req.include_profiles:
        result["profiles"] = []
    return ConfigPreviewResponse(**result)


@router.post("/compose", response_model=ComposeResponse)
def compose_material_spec(
    req: ComposeRequest,
    db=Depends(_get_db),
    _user: Any = Depends(_current_user),
) -> ComposeResponse:
    profile = _get_profile(load_profiles(db), req.profile_id)
    properties, composed, errors, warnings = compose_profile(profile, req.values)
    cad_fields = (
        cad_field_package(profile, properties, cad_system=req.cad_system)
        if req.include_cad_fields
        else {}
    )
    return ComposeResponse(
        ok=not errors,
        profile_id=str(profile["profile_id"]),
        properties=properties,
        composed=composed,
        cad_fields=cad_fields,
        errors=errors,
        warnings=warnings,
    )


@router.post("/diff/preview", response_model=CadDiffPreviewResponse)
def preview_cad_field_diff(
    req: CadDiffPreviewRequest,
    db=Depends(_get_db),
    _user: Any = Depends(_current_user),
) -> CadDiffPreviewResponse:
    profiles = load_profiles(db)
    target_values = dict(req.target_properties or {})
    target_values.update(req.values or {})
    if req.item_id:
        item = db.get(Item, req.item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        target_values = dict(getattr(item, "properties", None) or {})
        target_values.update(req.target_properties or {})
        target_values.update(req.values or {})

    default_profile_id = str(
        (_get_profile(profiles, "sheet") or {}).get("profile_id") or "sheet"
    )
    profile_id = req.profile_id or infer_profile_id(target_values, default_profile_id)
    profile = _get_profile(profiles, profile_id)

    has_structured_target = bool(req.item_id or req.target_properties or req.values)
    if has_structured_target and req.target_cad_fields:
        target_values.update(cad_fields_to_properties(profile, req.target_cad_fields))

    if has_structured_target:
        properties, _composed, errors, warnings = compose_profile(profile, target_values)
        target_cad_fields = cad_field_package(
            profile,
            properties,
            include_empty=req.include_empty,
            cad_system=req.cad_system,
        )
        target_cad_fields.update(req.target_cad_fields or {})
    else:
        properties = cad_fields_to_properties(profile, req.target_cad_fields or {})
        errors = []
        warnings = []
        target_cad_fields = dict(req.target_cad_fields or {})

    diffs, summary, current_package = build_cad_field_diff(
        profile,
        current_cad_fields=req.current_cad_fields,
        target_cad_fields=target_cad_fields,
        cad_system=req.cad_system,
    )
    write_cad_fields = cad_write_fields_from_diffs(diffs)
    return CadDiffPreviewResponse(
        ok=not errors,
        profile_id=str(profile["profile_id"]),
        item_id=req.item_id,
        properties=properties,
        current_cad_fields=current_package,
        target_cad_fields=target_cad_fields,
        write_cad_fields=write_cad_fields,
        requires_confirmation=bool(write_cad_fields),
        diffs=diffs,
        summary=summary,
        errors=errors,
        warnings=warnings,
    )


@router.post("/validate", response_model=ValidateResponse)
def validate_material_spec(
    req: ValidateRequest,
    db=Depends(_get_db),
    _user: Any = Depends(_current_user),
) -> ValidateResponse:
    profile = _get_profile(load_profiles(db), req.profile_id)
    properties, composed, errors, warnings = compose_profile(profile, req.values)
    matched_items = (
        [_item_payload(item) for item in _find_matching_items(db, profile, properties)]
        if req.lookup_existing
        else []
    )
    return ValidateResponse(
        ok=not errors,
        valid=not errors,
        profile_id=str(profile["profile_id"]),
        normalized=properties,
        composed=composed,
        cad_fields=cad_field_package(profile, properties, cad_system=req.cad_system),
        matched_items=matched_items,
        errors=errors,
        warnings=warnings,
    )


@router.post("/sync/outbound", response_model=SyncOutboundResponse)
def sync_outbound(
    req: SyncOutboundRequest,
    db=Depends(_get_db),
    _user: Any = Depends(_current_user),
) -> SyncOutboundResponse:
    values = dict(req.values or {})
    item_id = req.item_id
    if item_id:
        item = db.get(Item, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        values = dict(getattr(item, "properties", None) or {})
        values.update(req.values or {})

    profiles = load_profiles(db)
    default_profile_id = str(
        (_get_profile(profiles, "sheet") or {}).get("profile_id") or "sheet"
    )
    profile_id = req.profile_id or infer_profile_id(values, default_profile_id)
    profile = _get_profile(profiles, profile_id)
    properties, _composed, errors, warnings = compose_profile(profile, values)
    cad_fields = cad_field_package(
        profile,
        properties,
        include_empty=req.include_empty,
        cad_system=req.cad_system,
    )
    return SyncOutboundResponse(
        ok=not errors,
        profile_id=str(profile["profile_id"]),
        item_id=item_id,
        properties=properties,
        cad_fields=cad_fields,
        errors=errors,
        warnings=warnings,
    )


@router.post("/sync/inbound", response_model=SyncInboundResponse)
def sync_inbound(
    req: SyncInboundRequest,
    db=Depends(_get_db),
    current_user: Any = Depends(_current_user),
) -> SyncInboundResponse:
    incoming = {}
    profiles = load_profiles(db)
    provisional_values = dict(req.values or {})
    provisional_values.update(req.lookup_properties or {})
    provisional_profile_id = req.profile_id or infer_profile_id(provisional_values)
    profile = _get_profile(profiles, provisional_profile_id)

    incoming.update(cad_fields_to_properties(profile, req.cad_fields or {}))
    incoming.update(req.values or {})
    incoming.update(req.lookup_properties or {})
    if not req.profile_id:
        profile = _get_profile(profiles, infer_profile_id(incoming))

    properties, _composed, errors, warnings = compose_profile(profile, incoming)
    effective_overwrite, overwrite_warnings = _effective_inbound_overwrite(req, profile)
    warnings.extend(overwrite_warnings)
    cad_fields = cad_field_package(profile, properties, cad_system=req.cad_system)
    if errors:
        return SyncInboundResponse(
            ok=False,
            action="validation_failed",
            profile_id=str(profile["profile_id"]),
            properties=properties,
            cad_fields=cad_fields,
            errors=errors,
            warnings=warnings,
            dry_run=req.dry_run,
        )

    item = None
    matched_items: List[Dict[str, Any]] = []
    if req.item_id:
        item = db.get(Item, req.item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        matched_items = [_item_payload(item)]
    else:
        matches = _find_matching_items(db, profile, properties)
        matched_items = [_item_payload(candidate) for candidate in matches]
        if len(matches) == 1:
            item = matches[0]
        elif len(matches) > 1:
            return SyncInboundResponse(
                ok=False,
                action="ambiguous_match",
                profile_id=str(profile["profile_id"]),
                properties=properties,
                cad_fields=cad_fields,
                matched_items=matched_items,
                warnings=warnings,
                dry_run=req.dry_run,
            )

    if item:
        updates, conflicts = _build_updates(
            dict(getattr(item, "properties", None) or {}),
            properties,
            overwrite=effective_overwrite,
        )
        if conflicts:
            return SyncInboundResponse(
                ok=False,
                action="conflict",
                profile_id=str(profile["profile_id"]),
                item_id=getattr(item, "id", None),
                properties=properties,
                updates=updates,
                cad_fields=cad_fields,
                conflicts=conflicts,
                matched_items=matched_items,
                warnings=warnings,
                dry_run=req.dry_run,
            )
        if not req.dry_run and updates:
            _apply_item_update(db, item, updates, current_user)
        return SyncInboundResponse(
            ok=True,
            action="updated" if updates else "unchanged",
            profile_id=str(profile["profile_id"]),
            item_id=getattr(item, "id", None),
            properties=properties,
            updates=updates,
            cad_fields=cad_fields,
            matched_items=matched_items,
            warnings=warnings,
            dry_run=req.dry_run,
        )

    if not req.create_if_missing:
        return SyncInboundResponse(
            ok=False,
            action="not_found",
            profile_id=str(profile["profile_id"]),
            properties=properties,
            cad_fields=cad_fields,
            matched_items=matched_items,
            warnings=warnings,
            dry_run=req.dry_run,
        )

    created_item_id = None
    if not req.dry_run:
        created_item_id = _apply_item_create(db, profile, properties, current_user)
    return SyncInboundResponse(
        ok=True,
        action="created",
        profile_id=str(profile["profile_id"]),
        item_id=created_item_id,
        properties=properties,
        updates=properties,
        cad_fields=cad_fields,
        warnings=warnings,
        dry_run=req.dry_run,
    )
