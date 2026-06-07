from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session
    from yuantus.meta_engine.models.file import FileContainer, ItemFile
    from yuantus.meta_engine.models.item import Item
else:
    Session = Any

from yuantus.context import get_request_context
from yuantus.meta_engine.services.file_service import FileService

router = APIRouter(prefix="/plugins/pack-and-go", tags=["plugins-pack-and-go"])
logger = logging.getLogger(__name__)


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

_DEFAULT_FILE_ROLES = (
    "native_cad",
    "attachment",
    "printout",
    "geometry",
    "drawing",
)
_DEFAULT_DOCUMENT_TYPES = ("2d", "3d", "pr", "other")
_MANIFEST_CSV_COLUMNS = (
    "file_id",
    "filename",
    "output_filename",
    "file_role",
    "document_type",
    "cad_format",
    "size",
    "path_in_package",
    "source_item_id",
    "source_item_number",
    # A4-R1: WP1.3 drawing staleness (blank for non-drawing rows).
    "needs_update",
    "staleness_reason",
    "source_batch_id",
    "model_import_batch_id",
)
_MANIFEST_CSV_ALLOWED_COLUMNS = {
    *list(_MANIFEST_CSV_COLUMNS),
    "file_extension",
    "source_item_type",
    "source_item_state",
    "item_revision",
    "internal_ref",
    "document_version",
    "source_version_id",
}
_EXPORT_TYPE_OPTIONS = ("all", "2d", "3d", "pdf", "2dpdf", "3dpdf", "3d2d")
_EXPORT_TYPE_PRESETS = {
    "all": {
        "file_roles": _DEFAULT_FILE_ROLES,
        "document_types": _DEFAULT_DOCUMENT_TYPES,
        "include_printouts": True,
        "include_geometry": True,
    },
    "2d": {
        "file_roles": ("native_cad", "attachment", "drawing"),
        "document_types": ("2d",),
        "include_printouts": False,
        "include_geometry": False,
    },
    "3d": {
        "file_roles": ("native_cad", "attachment"),
        "document_types": ("3d",),
        "include_printouts": False,
        "include_geometry": True,
    },
    "pdf": {
        "file_roles": ("printout",),
        "document_types": _DEFAULT_DOCUMENT_TYPES,
        "include_printouts": True,
        "include_geometry": False,
    },
    "2dpdf": {
        "file_roles": ("native_cad", "attachment", "drawing"),
        "document_types": ("2d",),
        "include_printouts": True,
        "include_geometry": False,
    },
    "3dpdf": {
        "file_roles": ("native_cad", "attachment"),
        "document_types": ("3d",),
        "include_printouts": True,
        "include_geometry": True,
    },
    "3d2d": {
        "file_roles": ("native_cad", "attachment", "drawing"),
        "document_types": ("3d", "2d"),
        "include_printouts": False,
        "include_geometry": True,
    },
}
_FILENAME_MODES = ("original", "item_number", "item_number_rev", "internal_ref")
_PATH_STRATEGIES = (
    "item_role",
    "item",
    "role",
    "flat",
    "document_type",
    "item_document_type",
    "role_document_type",
    "item_role_document_type",
)
_COLLISION_STRATEGIES = ("append_id", "append_counter", "error")
_FILE_SCOPES = ("item", "version")
_BOM_FLAT_FORMATS = ("csv", "jsonl")
_BOM_FLAT_COLUMNS = (
    "level",
    "parent_id",
    "parent_item_number",
    "child_id",
    "child_item_number",
    "relationship_id",
    "relationship_type",
    "quantity",
    "uom",
    "find_num",
    "refdes",
    "path",
)
_FILENAME_TEMPLATE_FIELDS = (
    "original_name",
    "original_stem",
    "original_ext",
    "item_number",
    "item_id",
    "item_type",
    "item_state",
    "revision",
    "internal_ref",
    "file_role",
    "document_type",
    "cad_format",
    "file_id",
    "index",
)


class PackAndGoRequest(BaseModel):
    item_id: str = Field(..., description="Root item id")
    depth: int = Field(default=-1, description="BOM depth (-1 for full)")
    relationship_types: Optional[List[str]] = Field(
        default=None,
        description="Relationship ItemType ids to traverse (default: all)",
    )
    export_type: Optional[str] = Field(
        default=None,
        description="Preset export type (all|2d|3d|pdf|2dpdf|3dpdf|3d2d)",
    )
    filename_mode: Optional[str] = Field(
        default=None,
        description="Filename mode (original|item_number|item_number_rev|internal_ref)",
    )
    filename_template: Optional[str] = Field(
        default=None,
        description=(
            "Filename template using placeholders like {item_number}, {revision}, "
            "{file_role}, {original_ext}"
        ),
    )
    path_strategy: Optional[str] = Field(
        default=None,
        description=(
            "Path strategy (item_role|item|role|flat|document_type|item_document_type|"
            "role_document_type|item_role_document_type)"
        ),
    )
    collision_strategy: Optional[str] = Field(
        default=None,
        description="Collision strategy (append_id|append_counter|error)",
    )
    file_scope: Optional[str] = Field(
        default=None,
        description="File scope (item|version)",
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Manifest-first preview: compute the plan + manifest (incl. drawing "
            "staleness + version-lock summary) and return them synchronously, "
            "WITHOUT building the zip / writing cache / producing a download "
            "(takes precedence over async_flag)."
        ),
    )
    file_roles: Optional[List[str]] = None
    document_types: Optional[List[str]] = None
    include_item_types: Optional[List[str]] = None
    exclude_item_types: Optional[List[str]] = None
    include_item_ids: Optional[List[str]] = None
    exclude_item_ids: Optional[List[str]] = None
    allowed_states: Optional[List[str]] = None
    blocked_states: Optional[List[str]] = None
    allowed_extensions: Optional[List[str]] = None
    blocked_extensions: Optional[List[str]] = None
    include_previews: bool = Field(default=False)
    include_printouts: bool = Field(default=True)
    include_geometry: bool = Field(default=True)
    include_bom_tree: bool = Field(
        default=False, description="Include BOM tree JSON in the package"
    )
    bom_tree_filename: Optional[str] = Field(
        default=None, description="Filename for BOM tree JSON"
    )
    include_manifest_csv: bool = Field(
        default=False, description="Include manifest CSV in the package"
    )
    manifest_csv_filename: Optional[str] = Field(
        default=None, description="Filename for manifest CSV"
    )
    manifest_csv_columns: Optional[List[str]] = Field(
        default=None, description="Manifest CSV column ordering/selection"
    )
    include_bom_flat: bool = Field(
        default=False, description="Include a flat BOM export in the package"
    )
    bom_flat_format: Optional[str] = Field(
        default="csv", description="Flat BOM format (csv|jsonl)"
    )
    bom_flat_filename: Optional[str] = Field(
        default=None, description="Filename for flat BOM export"
    )
    bom_flat_columns: Optional[List[str]] = Field(
        default=None, description="Flat BOM column ordering/selection for CSV"
    )
    async_flag: bool = Field(default=False, alias="async")
    require_locked_versions: bool = Field(
        default=False,
        description=(
            "When True, the plugin enforces source-item version-lock via "
            "the merged version-lock contracts; HTTP 409 if any source "
            "item is unlocked or mismatched. `stale` (owned + non-current) "
            "does NOT block. When False (default), no enforcement is "
            "performed; the manifest still carries `version_lock_summary` "
            "for report-only inspection (one indexed `ItemVersion` lookup "
            "per export). Default-OFF never raises."
        ),
    )


class PackAndGoJobResponse(BaseModel):
    ok: bool
    job_id: str
    status_url: str


@dataclass
class PackAndGoFile:
    file_id: str
    filename: str
    output_filename: str
    file_role: str
    document_type: Optional[str]
    cad_format: Optional[str]
    file_extension: Optional[str]
    document_version: Optional[str]
    size: int
    path_in_package: str
    source_item_id: str
    source_item_number: str
    source_item_type: Optional[str]
    source_item_state: Optional[str]
    item_revision: Optional[str]
    internal_ref: Optional[str]
    source_version_id: Optional[str]
    source_path: str
    # A4-R1: WP1.3 drawing staleness, read off the (file-scope-correct) link.
    # Populated on drawing-role entries only; None elsewhere.
    needs_update: Optional[bool] = None
    staleness_reason: Optional[str] = None
    source_batch_id: Optional[str] = None
    model_import_batch_id: Optional[str] = None


@dataclass
class PackAndGoResult:
    zip_path: Path
    zip_name: str
    manifest: Dict[str, Any]
    file_count: int
    total_bytes: int


class BundleVersionLockError(Exception):
    """Raised inside ``build_pack_and_go_package`` when
    ``require_locked_versions=True`` and the bundle is NOT fully
    version-locked.

    Intentionally **not** a ``ValueError`` subclass — the existing sync
    route maps ``ValueError`` to HTTP 404, and we must surface this as
    HTTP 409 with machine-readable id lists. The route catches this
    class before the generic ``except ValueError`` block.

    ``stale`` items are never carried here — per the merged
    ``evaluate_bundle_version_locks`` semantics they count as locked
    and never fail the gate.
    """

    def __init__(
        self,
        *,
        unlocked: Sequence[str],
        mismatched: Sequence[str],
    ) -> None:
        self.unlocked: List[str] = list(unlocked)
        self.mismatched: List[str] = list(mismatched)
        message = (
            "bundle has version-lock violations: "
            f"{len(self.unlocked)} unlocked (ids={self.unlocked}) + "
            f"{len(self.mismatched)} mismatched (ids={self.mismatched})"
        )
        super().__init__(message)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _sanitize_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned or "item"


def _normalize_export_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = re.sub(r"[\s_+\-]+", "", value.strip().lower())
    if normalized in _EXPORT_TYPE_PRESETS:
        return normalized
    allowed = ", ".join(_EXPORT_TYPE_OPTIONS)
    raise ValueError(f"export_type must be one of: {allowed}")


def _normalize_filename_mode(value: Optional[str]) -> str:
    if not value:
        return "original"
    normalized = re.sub(r"[\s_+\-]+", "", value.strip().lower())
    mapping = {
        "original": "original",
        "filename": "original",
        "file": "original",
        "itemnumber": "item_number",
        "itemnumberrev": "item_number_rev",
        "itemnumberrevision": "item_number_rev",
        "internalref": "internal_ref",
        "internalreference": "internal_ref",
    }
    if normalized in mapping:
        return mapping[normalized]
    allowed = ", ".join(_FILENAME_MODES)
    raise ValueError(f"filename_mode must be one of: {allowed}")


def _normalize_path_strategy(value: Optional[str]) -> str:
    if not value:
        return "item_role"
    normalized = re.sub(r"[\s_+\-]+", "", value.strip().lower())
    mapping = {
        "itemrole": "item_role",
        "item": "item",
        "role": "role",
        "flat": "flat",
        "root": "flat",
        "documenttype": "document_type",
        "doctype": "document_type",
        "itemdocumenttype": "item_document_type",
        "itemdoctype": "item_document_type",
        "roledocumenttype": "role_document_type",
        "roledoctype": "role_document_type",
        "itemroledocumenttype": "item_role_document_type",
        "itemroledoctype": "item_role_document_type",
    }
    if normalized in mapping:
        return mapping[normalized]
    allowed = ", ".join(_PATH_STRATEGIES)
    raise ValueError(f"path_strategy must be one of: {allowed}")


def _normalize_collision_strategy(value: Optional[str]) -> str:
    if not value:
        return "append_id"
    normalized = re.sub(r"[\s_+\-]+", "", value.strip().lower())
    mapping = {
        "appendid": "append_id",
        "appendcounter": "append_counter",
        "counter": "append_counter",
        "error": "error",
        "fail": "error",
    }
    if normalized in mapping:
        return mapping[normalized]
    allowed = ", ".join(_COLLISION_STRATEGIES)
    raise ValueError(f"collision_strategy must be one of: {allowed}")


def _normalize_file_scope(value: Optional[str]) -> str:
    if not value:
        return "item"
    normalized = re.sub(r"[\s_+\-]+", "", value.strip().lower())
    mapping = {
        "item": "item",
        "items": "item",
        "itemfiles": "item",
        "version": "version",
        "currentversion": "version",
        "versionfiles": "version",
    }
    if normalized in mapping:
        return mapping[normalized]
    allowed = ", ".join(_FILE_SCOPES)
    raise ValueError(f"file_scope must be one of: {allowed}")


def _resolve_export_preset(
    *,
    export_type: Optional[str],
    file_roles: Optional[Sequence[str]],
    document_types: Optional[Sequence[str]],
    include_printouts: bool,
    include_geometry: bool,
    fields_set: set[str],
) -> Tuple[Optional[List[str]], Optional[List[str]], bool, bool, Optional[str]]:
    normalized = _normalize_export_type(export_type)
    if not normalized:
        return (
            list(file_roles) if file_roles is not None else None,
            list(document_types) if document_types is not None else None,
            include_printouts,
            include_geometry,
            None,
        )
    preset = _EXPORT_TYPE_PRESETS[normalized]
    if "file_roles" not in fields_set:
        file_roles = list(preset["file_roles"])
    if "document_types" not in fields_set:
        document_types = list(preset["document_types"])
    if "include_printouts" not in fields_set:
        include_printouts = preset["include_printouts"]
    if "include_geometry" not in fields_set:
        include_geometry = preset["include_geometry"]
    return (
        list(file_roles) if file_roles is not None else None,
        list(document_types) if document_types is not None else None,
        include_printouts,
        include_geometry,
        normalized,
    )


def _model_fields_set(model: BaseModel) -> set[str]:
    fields = getattr(model, "model_fields_set", None)
    if fields is not None:
        return set(fields)
    return set(getattr(model, "__fields_set__", set()))


def _normalize_file_roles(
    file_roles: Optional[Sequence[str]],
    *,
    include_previews: bool,
    include_printouts: bool,
    include_geometry: bool,
) -> List[str]:
    roles = [r.strip().lower() for r in (file_roles or _DEFAULT_FILE_ROLES) if r]
    role_set = set(roles)
    if include_previews:
        role_set.add("preview")
    if include_printouts:
        role_set.add("printout")
    if include_geometry:
        role_set.add("geometry")
    return sorted(role_set)


def _normalize_document_types(document_types: Optional[Sequence[str]]) -> List[str]:
    types = [t.strip().lower() for t in (document_types or _DEFAULT_DOCUMENT_TYPES) if t]
    return sorted(set(types))


def _normalize_manifest_csv_columns(columns: Optional[Sequence[str]]) -> List[str]:
    if not columns:
        return list(_MANIFEST_CSV_COLUMNS)
    normalized = [c.strip() for c in columns if c and c.strip()]
    if not normalized:
        return list(_MANIFEST_CSV_COLUMNS)
    unknown = [c for c in normalized if c not in _MANIFEST_CSV_ALLOWED_COLUMNS]
    if unknown:
        raise ValueError(f"Unknown manifest_csv_columns: {', '.join(unknown)}")
    return normalized


def _normalize_bom_flat_format(value: Optional[str]) -> str:
    normalized = (value or "csv").strip().lower()
    if normalized in _BOM_FLAT_FORMATS:
        return normalized
    allowed = ", ".join(_BOM_FLAT_FORMATS)
    raise ValueError(f"bom_flat_format must be one of: {allowed}")


def _normalize_bom_flat_columns(columns: Optional[Sequence[str]]) -> List[str]:
    if not columns:
        return list(_BOM_FLAT_COLUMNS)
    normalized = [c.strip() for c in columns if c and c.strip()]
    if not normalized:
        return list(_BOM_FLAT_COLUMNS)
    unknown = [c for c in normalized if c not in _BOM_FLAT_COLUMNS]
    if unknown:
        raise ValueError(f"Unknown bom_flat_columns: {', '.join(unknown)}")
    return normalized


def _resolve_item_revision(
    item: Optional[Item],
    version_by_id: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    if not item:
        return None
    props = item.properties or {}
    for key in ("revision", "rev", "version"):
        value = props.get(key)
        if value:
            return str(value).strip()
    if version_by_id:
        version_id = getattr(item, "current_version_id", None)
        if version_id:
            revision = version_by_id.get(version_id)
            if revision:
                return str(revision).strip()
    return None


def _resolve_internal_ref(item: Optional[Item]) -> Optional[str]:
    if not item:
        return None
    props = item.properties or {}
    for key in (
        "internal_ref",
        "internal_reference",
        "default_code",
        "internal_number",
        "number",
    ):
        value = props.get(key)
        if value:
            return str(value).strip()
    return None


def _validate_filename_template(template: str) -> None:
    fields = set(re.findall(r"{([A-Za-z0-9_]+)}", template))
    unknown = sorted(fields - set(_FILENAME_TEMPLATE_FIELDS))
    if unknown:
        allowed = ", ".join(_FILENAME_TEMPLATE_FIELDS)
        raise ValueError(
            f"filename_template has unknown fields: {', '.join(unknown)} "
            f"(allowed: {allowed})"
        )


def _build_output_filename(
    original_name: str,
    *,
    filename_mode: str,
    filename_template: Optional[str],
    item_number: str,
    item_id: str,
    item_type: Optional[str],
    item_state: Optional[str],
    internal_ref: Optional[str],
    revision: Optional[str],
    file_role: str,
    document_type: Optional[str],
    cad_format: Optional[str],
    file_id: str,
    index: int,
) -> str:
    original = Path(original_name).name
    if filename_template:
        _validate_filename_template(filename_template)
        stem = Path(original).stem
        ext = Path(original).suffix
        context = {
            "original_name": _sanitize_component(original),
            "original_stem": _sanitize_component(stem),
            "original_ext": _sanitize_component(ext) if ext else "",
            "item_number": _sanitize_component(item_number),
            "item_id": _sanitize_component(item_id),
            "item_type": _sanitize_component(item_type or ""),
            "item_state": _sanitize_component(item_state or ""),
            "revision": _sanitize_component(revision or ""),
            "internal_ref": _sanitize_component(internal_ref or ""),
            "file_role": _sanitize_component(file_role),
            "document_type": _sanitize_component(document_type or ""),
            "cad_format": _sanitize_component(cad_format or ""),
            "file_id": _sanitize_component(file_id),
            "index": str(index),
        }
        formatted = filename_template.format_map(context).strip()
        safe_name = _sanitize_component(formatted)
        if not safe_name:
            safe_name = _sanitize_component(item_number or stem or file_id)
        if not Path(safe_name).suffix and ext:
            safe_name = f"{safe_name}{ext}"
        return safe_name

    if filename_mode == "original":
        return original
    suffix = Path(original).suffix
    if filename_mode == "item_number":
        base = item_number
    elif filename_mode == "item_number_rev":
        base = item_number
        if revision:
            base = f"{base}_{revision}"
    elif filename_mode == "internal_ref":
        base = internal_ref or item_number
    else:
        base = item_number
    safe_base = _sanitize_component(base)
    return f"{safe_base}{suffix}" if suffix else safe_base


def _build_package_path(
    item_number: str,
    file_role: str,
    filename: str,
    *,
    path_strategy: str,
    document_type: Optional[str],
) -> str:
    safe_item = _sanitize_component(item_number)
    safe_role = _sanitize_component(file_role)
    safe_doc = _sanitize_component(document_type or "other")
    safe_name = Path(filename).name
    if path_strategy == "item":
        return f"{safe_item}/{safe_name}"
    if path_strategy == "role":
        return f"{safe_role}/{safe_name}"
    if path_strategy == "flat":
        return safe_name
    if path_strategy == "document_type":
        return f"{safe_doc}/{safe_name}"
    if path_strategy == "item_document_type":
        return f"{safe_item}/{safe_doc}/{safe_name}"
    if path_strategy == "role_document_type":
        return f"{safe_role}/{safe_doc}/{safe_name}"
    if path_strategy == "item_role_document_type":
        return f"{safe_item}/{safe_role}/{safe_doc}/{safe_name}"
    return f"{safe_item}/{safe_role}/{safe_name}"


def _ensure_unique_path(
    path: str,
    *,
    file_id: str,
    used_paths: set[str],
    strategy: str,
) -> str:
    if path not in used_paths:
        return path
    if strategy == "error":
        raise HTTPException(status_code=409, detail=f"Path collision: {path}")
    base, ext = os.path.splitext(path)
    if strategy == "append_counter":
        counter = 1
        candidate = f"{base}_{counter}{ext}"
        while candidate in used_paths:
            counter += 1
            candidate = f"{base}_{counter}{ext}"
        return candidate
    suffix = file_id[:8]
    candidate = f"{base}_{suffix}{ext}"
    counter = 1
    while candidate in used_paths:
        candidate = f"{base}_{suffix}_{counter}{ext}"
        counter += 1
    return candidate


def _safe_filename(value: Optional[str], default: str) -> str:
    if value:
        name = Path(value).name
        cleaned = _sanitize_component(name)
        if cleaned:
            return cleaned
    return default


def _resolve_item_number(item: Item) -> str:
    props = item.properties or {}
    return (
        props.get("item_number")
        or props.get("part_number")
        or props.get("doc_number")
        or item.id
    )


def _collect_item_ids(tree: Dict[str, Any]) -> List[str]:
    ids: List[str] = []

    def _walk(node: Dict[str, Any]) -> None:
        node_id = node.get("id")
        if node_id and node_id not in ids:
            ids.append(node_id)
        for child_entry in node.get("children", []) or []:
            child = child_entry.get("child") or {}
            _walk(child)

    _walk(tree)
    return ids


def _normalize_set(
    values: Optional[Sequence[str]], *, lower: bool = True
) -> set[str]:
    if not values:
        return set()
    normalized: set[str] = set()
    for value in values:
        if not value:
            continue
        cleaned = str(value).strip()
        if not cleaned:
            continue
        normalized.add(cleaned.lower() if lower else cleaned)
    return normalized


def _map_item_versions(
    items: Sequence[Any], *, eligible_item_set: Optional[set[str]] = None
) -> Tuple[Dict[str, str], Dict[str, str]]:
    version_by_item: Dict[str, str] = {}
    for item in items:
        item_id = getattr(item, "id", None)
        if not item_id:
            continue
        if eligible_item_set and item_id not in eligible_item_set:
            continue
        version_id = getattr(item, "current_version_id", None)
        if version_id:
            version_by_item[item_id] = str(version_id)
    item_by_version = {
        version_id: item_id for item_id, version_id in version_by_item.items()
    }
    return version_by_item, item_by_version


def _should_include_item(
    item: Optional[Item],
    *,
    include_item_ids: set[str],
    exclude_item_ids: set[str],
    include_item_types: set[str],
    exclude_item_types: set[str],
    allowed_states: set[str],
    blocked_states: set[str],
) -> bool:
    if not item:
        return False
    if include_item_ids and item.id not in include_item_ids:
        return False
    if exclude_item_ids and item.id in exclude_item_ids:
        return False
    item_type = (item.item_type_id or "").lower()
    if include_item_types and item_type not in include_item_types:
        return False
    if exclude_item_types and item_type in exclude_item_types:
        return False
    state = (item.state or "").lower()
    if allowed_states and state not in allowed_states:
        return False
    if blocked_states and state in blocked_states:
        return False
    return True


def _resolve_file_extension(file: FileContainer) -> str:
    ext = ""
    getter = getattr(file, "get_extension", None)
    if callable(getter):
        ext = getter() or ""
    if not ext and getattr(file, "filename", None):
        ext = Path(file.filename).suffix.lstrip(".")
    return ext.lower()


def _flatten_bom_tree(tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []

    def _node_label(node: Dict[str, Any]) -> str:
        return node.get("item_number") or node.get("name") or node.get("id") or ""

    def _walk(node: Dict[str, Any], path: List[str]) -> None:
        parent_id = node.get("id")
        parent_label = _node_label(node)
        parent_path = path + [parent_label] if parent_label else path

        for child_entry in node.get("children", []) or []:
            rel = child_entry.get("relationship") or {}
            props = rel.get("properties") or {}
            child = child_entry.get("child") or {}
            child_id = child.get("id") or rel.get("related_id")
            child_label = _node_label(child)
            child_path = parent_path + ([child_label] if child_label else [])

            quantity = props.get("quantity")
            if quantity is None:
                quantity = props.get("qty")

            entries.append(
                {
                    "level": len(parent_path),
                    "parent_id": parent_id,
                    "parent_item_number": parent_label or parent_id,
                    "child_id": child_id,
                    "child_item_number": child_label or child_id,
                    "relationship_id": rel.get("id"),
                    "relationship_type": rel.get("item_type_id"),
                    "quantity": quantity,
                    "uom": props.get("uom"),
                    "find_num": props.get("find_num"),
                    "refdes": props.get("refdes"),
                    "path": "/".join([p for p in child_path if p]),
                }
            )

            if child:
                _walk(child, parent_path)

    _walk(tree, [])
    return entries


def _build_bom_flat_csv(
    entries: Sequence[Dict[str, Any]],
    columns: Sequence[str],
) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(list(columns))
    for entry in entries:
        row: List[Any] = []
        for column in columns:
            value = entry.get(column, "")
            row.append("" if value is None else value)
        writer.writerow(row)
    return output.getvalue()


def _build_bom_flat_jsonl(entries: Sequence[Dict[str, Any]]) -> str:
    return "\n".join(
        [json.dumps(entry, ensure_ascii=True) for entry in entries]
    )


def _build_manifest_csv(
    entries: Sequence[Dict[str, Any]],
    columns: Sequence[str] = _MANIFEST_CSV_COLUMNS,
) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(list(columns))
    for entry in entries:
        row: List[Any] = []
        for column in columns:
            value = entry.get(column, "")
            row.append("" if value is None else value)
        writer.writerow(row)
    return output.getvalue()


def _build_cache_key(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _sorted_values(values: Optional[Sequence[str]]) -> List[str]:
    if not values:
        return []
    cleaned = [str(v).strip() for v in values if v and str(v).strip()]
    return sorted(cleaned)


def _normalized_values(values: Optional[Sequence[str]]) -> List[str]:
    if not values:
        return []
    return [str(v).strip() for v in values if v and str(v).strip()]


def _build_cache_payload(
    *,
    item_id: str,
    depth: int,
    export_type: Optional[str],
    file_roles: Sequence[str],
    document_types: Sequence[str],
    include_previews: bool,
    include_printouts: bool,
    include_geometry: bool,
    filename_mode: str,
    filename_template: Optional[str],
    path_strategy: str,
    collision_strategy: str,
    file_scope: str,
    include_bom_tree: bool,
    bom_tree_filename: Optional[str],
    include_manifest_csv: bool,
    manifest_csv_filename: Optional[str],
    manifest_csv_columns: Optional[Sequence[str]],
    include_bom_flat: bool,
    bom_flat_format: Optional[str],
    bom_flat_filename: Optional[str],
    bom_flat_columns: Optional[Sequence[str]],
    relationship_types: Optional[Sequence[str]],
    include_item_types: Optional[Sequence[str]],
    exclude_item_types: Optional[Sequence[str]],
    include_item_ids: Optional[Sequence[str]],
    exclude_item_ids: Optional[Sequence[str]],
    allowed_states: Optional[Sequence[str]],
    blocked_states: Optional[Sequence[str]],
    allowed_extensions: Optional[Sequence[str]],
    blocked_extensions: Optional[Sequence[str]],
    require_locked_versions: bool = False,
) -> Dict[str, Any]:
    return {
        "item_id": item_id,
        "depth": depth,
        "export_type": export_type or "",
        "file_roles": _sorted_values(file_roles),
        "document_types": _sorted_values(document_types),
        "include_previews": bool(include_previews),
        "include_printouts": bool(include_printouts),
        "include_geometry": bool(include_geometry),
        "filename_mode": filename_mode,
        "filename_template": filename_template or "",
        "path_strategy": path_strategy,
        "collision_strategy": collision_strategy,
        "file_scope": file_scope,
        "include_bom_tree": bool(include_bom_tree),
        "bom_tree_filename": _safe_filename(bom_tree_filename, "") if include_bom_tree else "",
        "include_manifest_csv": bool(include_manifest_csv),
        "manifest_csv_filename": _safe_filename(manifest_csv_filename, "") if include_manifest_csv else "",
        "manifest_csv_columns": _normalized_values(manifest_csv_columns),
        "include_bom_flat": bool(include_bom_flat),
        "bom_flat_format": bom_flat_format or "",
        "bom_flat_filename": _safe_filename(bom_flat_filename, "") if include_bom_flat else "",
        "bom_flat_columns": _normalized_values(bom_flat_columns),
        "relationship_types": _sorted_values(relationship_types),
        "include_item_types": _sorted_values(include_item_types),
        "exclude_item_types": _sorted_values(exclude_item_types),
        "include_item_ids": _sorted_values(include_item_ids),
        "exclude_item_ids": _sorted_values(exclude_item_ids),
        "allowed_states": _sorted_values(allowed_states),
        "blocked_states": _sorted_values(blocked_states),
        "allowed_extensions": _sorted_values(allowed_extensions),
        "blocked_extensions": _sorted_values(blocked_extensions),
        "require_locked_versions": bool(require_locked_versions),
    }


def _load_cached_manifest(zip_path: Path) -> Optional[Dict[str, Any]]:
    try:
        with zipfile.ZipFile(zip_path, "r") as zipf:
            with zipf.open("manifest.json") as handle:
                return json.loads(handle.read().decode("utf-8"))
    except Exception:
        return None


def _evaluate_bundle_version_lock(
    session: Any,
    *,
    file_links: Sequence[Dict[str, Any]],
    require_locked_versions: bool = False,
) -> Any:
    """Evaluate this bundle's source-item version-lock state — pure
    composition over the merged 3a resolver and the merged
    `evaluate_bundle_version_locks` (#570 / #588).

    Dedupes by source ``item_id`` (one descriptor per source item,
    even if multiple files come from it). Always returns a
    ``BundleLockReport`` so the caller can write
    ``version_lock_summary`` to the manifest. Raises
    ``BundleVersionLockError`` ONLY when
    ``require_locked_versions=True`` and the report is not ``ok`` —
    stale items count as locked and never fail the gate (matches the
    merged contract).
    """

    from yuantus.meta_engine.services.pack_and_go_db_resolver_contract import (
        ItemVersionRow,
        WorkorderDocLinkRow,
        resolve_bundle_document_descriptors,
    )
    from yuantus.meta_engine.services.pack_and_go_version_lock_contract import (
        evaluate_bundle_version_locks,
    )
    from yuantus.meta_engine.version.models import ItemVersion

    version_lock_pairs: List[
        Tuple[WorkorderDocLinkRow, Optional[ItemVersionRow]]
    ] = []
    seen_source_item_ids: set[str] = set()
    version_ids_needed = {
        str(link["source_version_id"])
        for link in file_links
        if link.get("source_version_id")
    }
    version_row_lookup: Dict[str, Any] = {}
    if version_ids_needed:
        loaded_versions = (
            session.query(ItemVersion)
            .filter(ItemVersion.id.in_(version_ids_needed))
            .all()
        )
        version_row_lookup = {str(v.id): v for v in loaded_versions}
    for link in file_links:
        link_item_id = link.get("item_id")
        if not link_item_id or link_item_id in seen_source_item_ids:
            continue
        seen_source_item_ids.add(link_item_id)
        source_version_id = link.get("source_version_id")
        link_row = WorkorderDocLinkRow(
            document_item_id=str(link_item_id),
            document_version_id=(
                str(source_version_id) if source_version_id else None
            ),
        )
        version_row: Optional[ItemVersionRow] = None
        if source_version_id:
            loaded = version_row_lookup.get(str(source_version_id))
            if loaded is not None:
                version_row = ItemVersionRow(
                    id=str(loaded.id),
                    item_id=str(loaded.item_id),
                    is_current=loaded.is_current,
                )
        version_lock_pairs.append((link_row, version_row))
    descriptors = resolve_bundle_document_descriptors(version_lock_pairs)
    report = evaluate_bundle_version_locks(descriptors)
    if require_locked_versions and not report.ok:
        raise BundleVersionLockError(
            unlocked=report.unlocked,
            mismatched=report.mismatched,
        )
    return report


def _version_lock_link_for_included_file(
    link: Dict[str, Any],
    source_item: Any,
) -> Dict[str, Any]:
    """Build the version-lock input for a file that will enter the bundle.

    Item-scope pack-and-go links do not carry a version id from the
    ItemFile row, but the final manifest backfills the source item's
    current version. Use the same effective version here so
    ``version_lock_summary`` reflects the actual bundle content rather
    than the raw candidate link shape.
    """

    source_item_id = str(getattr(source_item, "id", None) or link.get("item_id") or "")
    source_version_id = (
        link.get("source_version_id")
        or getattr(source_item, "current_version_id", None)
    )
    return {
        "item_id": source_item_id,
        "source_version_id": str(source_version_id) if source_version_id else None,
    }


def _resolve_source_path(
    file_service: FileService,
    file: FileContainer,
    temp_dir: Path,
    download: bool = True,
) -> Tuple[Optional[str], Optional[Path]]:
    local_path = file_service.get_local_path(file.system_path)
    if local_path and os.path.exists(local_path):
        return local_path, None

    # Manifest-first dry-run: never fetch a remote blob just to build a preview. On a local-cache miss
    # there is no source path; the caller falls back to file.file_size for the size (no download, no temp).
    if not download:
        return None, None

    temp_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename).suffix or ""
    temp_path = temp_dir / f"{file.id}{suffix}"
    with open(temp_path, "wb") as handle:
        file_service.download_file(file.system_path, handle)
    return str(temp_path), temp_path


def _prune_old_outputs(output_dir: Path, retention_minutes: int) -> None:
    if retention_minutes <= 0:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=retention_minutes)
    for entry in output_dir.glob("pack_and_go_*.zip"):
        try:
            stat = entry.stat()
        except OSError:
            continue
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            try:
                entry.unlink()
            except OSError:
                continue


def build_pack_and_go_package(
    session: Session,
    *,
    item_id: str,
    depth: int,
    file_roles: Sequence[str],
    document_types: Sequence[str],
    include_previews: bool,
    include_printouts: bool,
    include_geometry: bool,
    export_type: Optional[str] = None,
    filename_mode: str = "original",
    filename_template: Optional[str] = None,
    path_strategy: str = "item_role",
    collision_strategy: str = "append_id",
    file_scope: str = "item",
    dry_run: bool = False,
    include_item_types: Optional[Sequence[str]] = None,
    exclude_item_types: Optional[Sequence[str]] = None,
    include_item_ids: Optional[Sequence[str]] = None,
    exclude_item_ids: Optional[Sequence[str]] = None,
    allowed_states: Optional[Sequence[str]] = None,
    blocked_states: Optional[Sequence[str]] = None,
    allowed_extensions: Optional[Sequence[str]] = None,
    blocked_extensions: Optional[Sequence[str]] = None,
    include_bom_tree: bool = False,
    bom_tree_filename: Optional[str] = None,
    include_manifest_csv: bool = False,
    manifest_csv_filename: Optional[str] = None,
    manifest_csv_columns: Optional[Sequence[str]] = None,
    include_bom_flat: bool = False,
    bom_flat_format: Optional[str] = None,
    bom_flat_filename: Optional[str] = None,
    bom_flat_columns: Optional[Sequence[str]] = None,
    relationship_types: Optional[Sequence[str]] = None,
    cache_key: Optional[str] = None,
    cache_enabled: bool = False,
    cache_ttl_minutes: int = 0,
    context: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[
        Callable[[str, int, int, Optional[str], Optional[Dict[str, Any]]], None]
    ] = None,
    output_dir: Path,
    file_service: Optional[FileService] = None,
    require_locked_versions: bool = False,
) -> "PackAndGoResult | Dict[str, Any]":
    # dry_run returns the manifest dict (no zip); the normal path returns PackAndGoResult.
    from sqlalchemy.orm import joinedload
    from yuantus.meta_engine.models.file import ItemFile
    from yuantus.meta_engine.models.item import Item
    from yuantus.meta_engine.services.bom_service import BOMService
    from yuantus.meta_engine.version.models import ItemVersion, VersionFile

    start_time = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)

    cache_hit = False
    cache_path: Optional[Path] = None
    if cache_enabled and cache_key:
        cache_path = output_dir / f"pack_and_go_{cache_key}.zip"
        if cache_path.exists():
            expired = False
            if cache_ttl_minutes > 0:
                try:
                    mtime = datetime.fromtimestamp(
                        cache_path.stat().st_mtime, tz=timezone.utc
                    )
                    cutoff = datetime.now(timezone.utc) - timedelta(
                        minutes=cache_ttl_minutes
                    )
                    expired = mtime < cutoff
                except OSError:
                    expired = True
            if not expired:
                cached_manifest = _load_cached_manifest(cache_path)
                if cached_manifest:
                    cached_manifest = dict(cached_manifest)
                    cached_manifest["cache_hit"] = True
                    cached_manifest["cache_key"] = cache_key
                    file_count = int(
                        cached_manifest.get("file_count")
                        or len(cached_manifest.get("files") or [])
                    )
                    total_bytes = int(cached_manifest.get("total_bytes") or 0)
                    root_number = cached_manifest.get("root_item_number") or item_id
                    zip_name = (
                        cached_manifest.get("zip_name")
                        or f"pack_and_go_{_sanitize_component(root_number)}_{cache_key}.zip"
                    )
                    logger.info(
                        "pack-and-go cache hit",
                        extra={
                            "event": "packgo_cache_hit",
                            "item_id": item_id,
                            "cache_key": cache_key,
                            "file_count": file_count,
                        },
                    )
                    return PackAndGoResult(
                        zip_path=cache_path,
                        zip_name=zip_name,
                        manifest=cached_manifest,
                        file_count=file_count,
                        total_bytes=total_bytes,
                    )
            else:
                try:
                    cache_path.unlink()
                except OSError:
                    pass

    logger.info(
        "pack-and-go build start",
        extra={
            "event": "packgo_build_start",
            "item_id": item_id,
            "depth": depth,
            "file_scope": file_scope,
            "export_type": export_type,
        },
    )

    bom_service = BOMService(session)
    bom_tree = bom_service.get_bom_structure(
        item_id,
        levels=depth,
        relationship_types=list(relationship_types) if relationship_types else None,
    )

    file_service = file_service or FileService()
    item_ids = _collect_item_ids(bom_tree)

    include_item_ids_set = _normalize_set(include_item_ids, lower=False)
    exclude_item_ids_set = _normalize_set(exclude_item_ids, lower=False)
    include_item_types_set = _normalize_set(include_item_types)
    exclude_item_types_set = _normalize_set(exclude_item_types)
    allowed_states_set = _normalize_set(allowed_states)
    blocked_states_set = _normalize_set(blocked_states)
    allowed_ext_set = _normalize_set(allowed_extensions)
    blocked_ext_set = _normalize_set(blocked_extensions)

    if include_item_ids_set:
        item_ids = [entry for entry in item_ids if entry in include_item_ids_set]
    if exclude_item_ids_set:
        item_ids = [entry for entry in item_ids if entry not in exclude_item_ids_set]
    if item_id not in item_ids:
        item_ids.append(item_id)

    items = session.query(Item).filter(Item.id.in_(item_ids)).all()
    item_by_id = {item.id: item for item in items}

    eligible_item_ids: List[str] = []
    skipped_item_ids: List[str] = []
    for item in items:
        if _should_include_item(
            item,
            include_item_ids=include_item_ids_set,
            exclude_item_ids=exclude_item_ids_set,
            include_item_types=include_item_types_set,
            exclude_item_types=exclude_item_types_set,
            allowed_states=allowed_states_set,
            blocked_states=blocked_states_set,
        ):
            eligible_item_ids.append(item.id)
        else:
            skipped_item_ids.append(item.id)

    eligible_item_set = set(eligible_item_ids)

    version_by_id: Dict[str, str] = {}
    current_version_ids = {
        item.current_version_id for item in items if item.current_version_id
    }
    if current_version_ids:
        versions = (
            session.query(ItemVersion)
            .filter(ItemVersion.id.in_(current_version_ids))
            .all()
        )
        version_by_id = {
            version.id: version.revision for version in versions if version.revision
        }

    normalized_roles = _normalize_file_roles(
        file_roles,
        include_previews=include_previews,
        include_printouts=include_printouts,
        include_geometry=include_geometry,
    )
    normalized_types = _normalize_document_types(document_types)
    manifest_columns = (
        _normalize_manifest_csv_columns(manifest_csv_columns)
        if include_manifest_csv
        else None
    )

    bom_flat_name = None
    bom_flat_payload: Optional[str] = None
    bom_flat_columns_norm: Optional[List[str]] = None
    bom_flat_format_norm: Optional[str] = None
    if include_bom_flat:
        bom_flat_format_norm = _normalize_bom_flat_format(bom_flat_format)
        bom_flat_columns_norm = _normalize_bom_flat_columns(bom_flat_columns)
        bom_flat_name = _safe_filename(
            bom_flat_filename,
            "bom_flat.csv" if bom_flat_format_norm == "csv" else "bom_flat.jsonl",
        )

    file_links: List[Dict[str, Any]] = []
    fallback_item_ids: List[str] = []
    if file_scope == "version":
        version_by_item, item_by_version = _map_item_versions(
            items, eligible_item_set=eligible_item_set
        )
        version_ids = [vid for vid in version_by_item.values() if vid]
        if version_ids:
            version_files = (
                session.query(VersionFile)
                .options(joinedload(VersionFile.file))
                .filter(VersionFile.version_id.in_(version_ids))
                .all()
            )
            for vf in version_files:
                file_links.append(
                    {
                        "file": vf.file,
                        "file_role": vf.file_role,
                        "item_id": item_by_version.get(vf.version_id),
                        "source_version_id": vf.version_id,
                        # A4-R1: WP1.3 staleness from the version-snapshot link.
                        "needs_update": vf.needs_update,
                        "source_batch_id": vf.source_batch_id,
                        "staleness_reason": vf.staleness_reason,
                        "import_batch_id": vf.import_batch_id,
                    }
                )
        fallback_item_ids = [
            item.id
            for item in items
            if item.id in eligible_item_set and not item.current_version_id
        ]
        if fallback_item_ids:
            fallback_files = (
                session.query(ItemFile)
                .options(joinedload(ItemFile.file))
                .filter(ItemFile.item_id.in_(fallback_item_ids))
                .all()
            )
            for item_file in fallback_files:
                file_links.append(
                    {
                        "file": item_file.file,
                        "file_role": item_file.file_role,
                        "item_id": item_file.item_id,
                        "source_version_id": None,
                        # A4-R1: version-scope fallback reads the current ItemFile link.
                        "needs_update": item_file.needs_update,
                        "source_batch_id": item_file.source_batch_id,
                        "staleness_reason": item_file.staleness_reason,
                        "import_batch_id": item_file.import_batch_id,
                    }
                )
    else:
        item_files = (
            session.query(ItemFile)
            .options(joinedload(ItemFile.file))
            .filter(ItemFile.item_id.in_(eligible_item_ids))
            .all()
        )
        for item_file in item_files:
            file_links.append(
                {
                    "file": item_file.file,
                    "file_role": item_file.file_role,
                    "item_id": item_file.item_id,
                    "source_version_id": getattr(
                        item_by_id.get(item_file.item_id),
                        "current_version_id",
                        None,
                    ),
                    # A4-R1: WP1.3 staleness from the current ItemFile link.
                    "needs_update": item_file.needs_update,
                    "source_batch_id": item_file.source_batch_id,
                    "staleness_reason": item_file.staleness_reason,
                    "import_batch_id": item_file.import_batch_id,
                }
            )

    # A4-R1: per-item model (document_type "3d") import_batch_id, sourced from the
    # SAME loaded fileset (file-scope-consistent with the drawing's needs_update);
    # None when the item's model isn't in the bundle. Explanatory context only --
    # needs_update on the drawing link is the authoritative staleness verdict.
    model_batch_by_item: Dict[str, Optional[str]] = {}
    for _link in file_links:
        _f = _link.get("file")
        _role = (_link.get("file_role") or "").lower()
        # Match WP1.3's model M: a 3D *native_cad* file -- NOT any 3D file (a 3D
        # geometry/attachment must not fix the model batch).
        if (
            _f is not None
            and _role == "native_cad"
            and (getattr(_f, "document_type", "") or "").lower() == "3d"
        ):
            model_batch_by_item.setdefault(
                _link.get("item_id"), _link.get("import_batch_id")
            )

    max_files = _env_int("YUANTUS_PACKGO_MAX_FILES", 2000)
    max_bytes = _env_int("YUANTUS_PACKGO_MAX_BYTES", 0)
    progress_interval = max(1, _env_int("YUANTUS_PACKGO_PROGRESS_INTERVAL", 50))

    if progress_callback:
        progress_callback(
            "collecting",
            0,
            len(file_links),
            "collecting files",
            {"total_candidates": len(file_links)},
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="yuantus_packgo_"))
    temp_paths: List[Path] = []
    pack_files: List[PackAndGoFile] = []
    missing_files: List[Dict[str, Any]] = []
    seen_files: set[str] = set()
    used_paths: set[str] = set()
    processed_links = 0
    version_lock_file_links: List[Dict[str, Any]] = []

    for link in file_links:
        processed_links += 1
        try:
            file = link.get("file")
            if not file or not getattr(file, "id", None):
                continue
            if file.id in seen_files:
                continue

            source_item_id = link.get("item_id") or ""
            if eligible_item_set and source_item_id not in eligible_item_set:
                continue
            source_item = item_by_id.get(source_item_id)
            if not source_item:
                continue

            file_role = (link.get("file_role") or "").lower()
            if file_role not in normalized_roles:
                continue

            document_type = (file.document_type or "").lower()
            if document_type and document_type not in normalized_types:
                continue

            file_extension = _resolve_file_extension(file)
            if allowed_ext_set and file_extension not in allowed_ext_set:
                continue
            if blocked_ext_set and file_extension in blocked_ext_set:
                continue

            if not file.filename or not file.system_path:
                continue

            if not file_service.file_exists(file.system_path):
                missing_files.append(
                    {
                        "file_id": file.id,
                        "filename": file.filename,
                        "file_role": file_role,
                        "source_item_id": source_item_id,
                        "source_version_id": link.get("source_version_id"),
                    }
                )
                continue

            effective_source_version_id = (
                link.get("source_version_id")
                or getattr(source_item, "current_version_id", None)
            )
            version_lock_file_links.append(
                _version_lock_link_for_included_file(link, source_item)
            )

            source_item_number = _resolve_item_number(source_item)
            internal_ref = _resolve_internal_ref(source_item)
            revision = _resolve_item_revision(source_item, version_by_id)
            output_name = _build_output_filename(
                file.filename,
                filename_mode=filename_mode,
                filename_template=filename_template,
                item_number=source_item_number,
                item_id=source_item.id,
                item_type=source_item.item_type_id,
                item_state=source_item.state,
                internal_ref=internal_ref,
                revision=revision,
                file_role=file_role,
                document_type=document_type or None,
                cad_format=file.cad_format,
                file_id=file.id,
                index=len(pack_files) + 1,
            )
            package_path = _build_package_path(
                source_item_number,
                file_role,
                output_name,
                path_strategy=path_strategy,
                document_type=document_type or None,
            )
            package_path = _ensure_unique_path(
                package_path,
                file_id=file.id,
                used_paths=used_paths,
                strategy=collision_strategy,
            )
            output_filename = Path(package_path).name
            # dry_run is manifest-first: do NOT download remote blobs (no temp_path added). A local-cache
            # hit may still be stat'd for size; a miss leaves source_path None and falls back to file_size.
            source_path, temp_path = _resolve_source_path(
                file_service, file, temp_dir, download=not dry_run
            )
            if temp_path:
                temp_paths.append(temp_path)

            size = int(file.file_size or 0)
            if not size and source_path:
                try:
                    size = int(Path(source_path).stat().st_size)
                except OSError:
                    size = 0

            pack_files.append(
                PackAndGoFile(
                    file_id=file.id,
                    filename=file.filename,
                    output_filename=output_filename,
                    file_role=file_role,
                    document_type=document_type or None,
                    cad_format=file.cad_format,
                    file_extension=file_extension or None,
                    document_version=file.document_version,
                    size=size,
                    path_in_package=package_path,
                    source_item_id=source_item.id,
                    source_item_number=source_item_number,
                    source_item_type=source_item.item_type_id,
                    source_item_state=source_item.state,
                    item_revision=revision,
                    internal_ref=internal_ref,
                    source_version_id=effective_source_version_id,
                    # "" placeholder on a dry-run cache miss (never zipped, absent from the manifest);
                    # a real path on the zip path so zipf.write has the bytes.
                    source_path=source_path or "",
                    # A4-R1: drawing-role only; warn-not-exclude (entry stays in the
                    # bundle). model_import_batch_id is file-scope-consistent context.
                    needs_update=(
                        bool(link.get("needs_update"))
                        if file_role == "drawing"
                        else None
                    ),
                    staleness_reason=(
                        link.get("staleness_reason")
                        if file_role == "drawing"
                        else None
                    ),
                    source_batch_id=(
                        link.get("source_batch_id")
                        if file_role == "drawing"
                        else None
                    ),
                    model_import_batch_id=(
                        model_batch_by_item.get(source_item_id)
                        if file_role == "drawing"
                        else None
                    ),
                )
            )
            seen_files.add(file.id)
            used_paths.add(package_path)

            if max_files > 0 and len(pack_files) > max_files:
                raise HTTPException(
                    status_code=413, detail="pack-and-go max files exceeded"
                )
        finally:
            if progress_callback and (
                processed_links % progress_interval == 0
                or processed_links == len(file_links)
            ):
                progress_callback(
                    "collecting",
                    processed_links,
                    len(file_links),
                    None,
                    {"included_files": len(pack_files)},
                )

    # Bundle version-lock evaluation (#590 scope-gate / Tier-B runtime
    # wiring). Reuses the merged 3a row→descriptor resolver and the
    # merged `evaluate_bundle_version_locks` arithmetic — the plugin
    # never re-implements lock semantics. Evaluate against the same
    # file set that will enter the zip (after item/file-role/type/
    # extension/missing-file filters) so the manifest summary and
    # enforcement describe the actual bundle, not raw candidates.
    try:
        version_lock_report = _evaluate_bundle_version_lock(
            session,
            file_links=version_lock_file_links,
            require_locked_versions=require_locked_versions,
        )
    except Exception:
        for temp_path in temp_paths:
            try:
                temp_path.unlink()
            except OSError:
                continue
        try:
            temp_dir.rmdir()
        except OSError:
            pass
        raise

    total_bytes = sum(entry.size for entry in pack_files)
    if max_bytes > 0 and total_bytes > max_bytes:
        raise HTTPException(status_code=413, detail="pack-and-go max bytes exceeded")

    root_item = item_by_id.get(item_id)
    root_number = _resolve_item_number(root_item) if root_item else item_id
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    if cache_enabled and cache_key:
        zip_name = f"pack_and_go_{_sanitize_component(root_number)}_{cache_key}.zip"
        zip_path = cache_path or (output_dir / f"pack_and_go_{cache_key}.zip")
    else:
        zip_name = f"pack_and_go_{_sanitize_component(root_number)}_{timestamp}.zip"
        zip_path = output_dir / zip_name

    bom_tree_name = None
    manifest_csv_name = None
    if include_bom_tree:
        bom_tree_name = _safe_filename(bom_tree_filename, "bom_tree.json")
    if include_manifest_csv:
        manifest_csv_name = _safe_filename(manifest_csv_filename, "manifest.csv")

    bom_flat_entries: Optional[List[Dict[str, Any]]] = None
    if include_bom_flat:
        bom_flat_entries = _flatten_bom_tree(bom_tree)
        if eligible_item_set:
            bom_flat_entries = [
                entry
                for entry in bom_flat_entries
                if entry.get("parent_id") in eligible_item_set
                and entry.get("child_id") in eligible_item_set
            ]
        if bom_flat_format_norm == "jsonl":
            bom_flat_payload = _build_bom_flat_jsonl(bom_flat_entries)
        else:
            bom_flat_payload = _build_bom_flat_csv(
                bom_flat_entries,
                bom_flat_columns_norm or _BOM_FLAT_COLUMNS,
            )

    manifest = {
        "root_item_id": item_id,
        "root_item_number": root_number,
        "depth": depth,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "export_type": export_type,
        "filename_mode": filename_mode,
        "filename_template": filename_template,
        "path_strategy": path_strategy,
        "collision_strategy": collision_strategy,
        "file_scope": file_scope,
        "relationship_types": list(relationship_types) if relationship_types else None,
        "file_roles": list(normalized_roles),
        "document_types": list(normalized_types),
        "options": {
            "include_previews": include_previews,
            "include_printouts": include_printouts,
            "include_geometry": include_geometry,
            "include_bom_tree": include_bom_tree,
            "include_manifest_csv": include_manifest_csv,
            "include_bom_flat": include_bom_flat,
        },
        "file_count": len(pack_files),
        "total_bytes": total_bytes,
        "zip_name": zip_name,
        "cache_key": cache_key,
        "cache_hit": cache_hit,
        "files": [
            {
                "file_id": entry.file_id,
                "filename": entry.filename,
                "output_filename": entry.output_filename,
                "file_role": entry.file_role,
                "document_type": entry.document_type,
                "cad_format": entry.cad_format,
                "file_extension": entry.file_extension,
                "document_version": entry.document_version,
                "size": entry.size,
                "path_in_package": entry.path_in_package,
                "source_item_id": entry.source_item_id,
                "source_item_number": entry.source_item_number,
                "source_item_type": entry.source_item_type,
                "source_item_state": entry.source_item_state,
                "item_revision": entry.item_revision,
                "internal_ref": entry.internal_ref,
                "source_version_id": entry.source_version_id,
                # A4-R1: WP1.3 drawing staleness (None for non-drawing entries).
                "needs_update": entry.needs_update,
                "staleness_reason": entry.staleness_reason,
                "source_batch_id": entry.source_batch_id,
                "model_import_batch_id": entry.model_import_batch_id,
            }
            for entry in pack_files
        ],
        "missing_files": missing_files,
    }
    manifest["version_lock_summary"] = {
        "total": version_lock_report.total,
        "locked": version_lock_report.locked,
        "unlocked": list(version_lock_report.unlocked),
        "mismatched": list(version_lock_report.mismatched),
        "stale": list(version_lock_report.stale),
        "ok": version_lock_report.ok,
        "requires_lock": bool(require_locked_versions),
    }
    # A4-R1: WP1.3 drawing-staleness summary -- DISTINCT from version_lock_summary
    # (which is version-lock/checkout state, NOT CAD drawing staleness). R1 is
    # warn-not-exclude: stale drawings stay in the bundle; this surfaces the risk.
    _drawing_entries = [e for e in pack_files if e.file_role == "drawing"]
    _stale_drawings = [e for e in _drawing_entries if e.needs_update]
    manifest["drawing_staleness_summary"] = {
        "total_drawings": len(_drawing_entries),
        "needs_update": len(_stale_drawings),
        "stale_file_ids": [e.file_id for e in _stale_drawings],
        "excluded": False,
    }
    if context:
        manifest["context"] = context
    if skipped_item_ids:
        manifest["skipped_item_ids"] = sorted(skipped_item_ids)
    if fallback_item_ids:
        manifest["fallback_item_ids"] = sorted(set(fallback_item_ids))
    if manifest_columns:
        manifest["manifest_csv_columns"] = list(manifest_columns)
    if bom_flat_format_norm:
        manifest["bom_flat_format"] = bom_flat_format_norm
    if bom_flat_columns_norm:
        manifest["bom_flat_columns"] = list(bom_flat_columns_norm)

    filters: Dict[str, Any] = {}
    if include_item_ids_set:
        filters["include_item_ids"] = sorted(include_item_ids_set)
    if exclude_item_ids_set:
        filters["exclude_item_ids"] = sorted(exclude_item_ids_set)
    if include_item_types_set:
        filters["include_item_types"] = sorted(include_item_types_set)
    if exclude_item_types_set:
        filters["exclude_item_types"] = sorted(exclude_item_types_set)
    if allowed_states_set:
        filters["allowed_states"] = sorted(allowed_states_set)
    if blocked_states_set:
        filters["blocked_states"] = sorted(blocked_states_set)
    if allowed_ext_set:
        filters["allowed_extensions"] = sorted(allowed_ext_set)
    if blocked_ext_set:
        filters["blocked_extensions"] = sorted(blocked_ext_set)
    if filters:
        manifest["filters"] = filters

    extra_files: List[Dict[str, Any]] = []
    if bom_tree_name:
        manifest["bom_tree_file"] = bom_tree_name
        extra_files.append({"kind": "bom_tree", "path": bom_tree_name})
    if manifest_csv_name:
        manifest["manifest_csv_file"] = manifest_csv_name
        extra_files.append({"kind": "manifest_csv", "path": manifest_csv_name})
    if bom_flat_name and bom_flat_payload is not None:
        manifest["bom_flat_file"] = bom_flat_name
        extra_files.append({"kind": "bom_flat", "path": bom_flat_name})
    if extra_files:
        manifest["extra_files"] = extra_files

    # A4-R1: dry-run / manifest-first. The manifest is now fully built (files +
    # drawing staleness + version-lock summary + extra-file plan); return it WITHOUT
    # writing the zip / cache / artifact. Clean up the gather temp dir HERE -- the
    # normal path cleans it post-zip, which dry-run skips, so without this each
    # dry-run would leak a temp dir (+ any extracted blobs). Mirrors the post-zip
    # cleanup. Only POST's dry_run branch passes this.
    if dry_run:
        for temp_path in temp_paths:
            try:
                temp_path.unlink()
            except OSError:
                continue
        try:
            temp_dir.rmdir()
        except OSError:
            pass
        return manifest

    if progress_callback:
        progress_callback(
            "zipping",
            len(pack_files),
            len(pack_files),
            "writing zip",
            {"file_count": len(pack_files), "zip_name": zip_name},
        )

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for entry in pack_files:
            zipf.write(entry.source_path, entry.path_in_package)
        zipf.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=True, indent=2),
        )
        if bom_tree_name:
            zipf.writestr(
                bom_tree_name,
                json.dumps(bom_tree, ensure_ascii=True, indent=2),
            )
        if manifest_csv_name:
            zipf.writestr(
                manifest_csv_name,
                _build_manifest_csv(
                    manifest["files"],
                    columns=manifest_columns or _MANIFEST_CSV_COLUMNS,
                ),
            )
        if bom_flat_name and bom_flat_payload is not None:
            zipf.writestr(bom_flat_name, bom_flat_payload)

    for temp_path in temp_paths:
        try:
            temp_path.unlink()
        except OSError:
            continue
    try:
        temp_dir.rmdir()
    except OSError:
        pass

    duration = time.monotonic() - start_time
    logger.info(
        "pack-and-go build complete",
        extra={
            "event": "packgo_build_complete",
            "item_id": item_id,
            "file_count": len(pack_files),
            "total_bytes": total_bytes,
            "duration_sec": round(duration, 3),
        },
    )

    if progress_callback:
        progress_callback(
            "complete",
            len(pack_files),
            len(pack_files),
            "complete",
            {
                "file_count": len(pack_files),
                "total_bytes": total_bytes,
                "duration_sec": round(duration, 3),
            },
        )

    return PackAndGoResult(
        zip_path=zip_path,
        zip_name=zip_name,
        manifest=manifest,
        file_count=len(pack_files),
        total_bytes=total_bytes,
    )


def _build_job_payload(req: PackAndGoRequest, *, user_id: Optional[str]) -> Dict[str, Any]:
    ctx = get_request_context()
    payload = req.model_dump(by_alias=True)
    payload.update(
        {
            "tenant_id": ctx.tenant_id,
            "org_id": ctx.org_id,
            "user_id": user_id,
        }
    )
    payload["context"] = {
        "tenant_id": ctx.tenant_id,
        "org_id": ctx.org_id,
        "user_id": user_id,
    }
    return payload


@router.post("")
def pack_and_go(
    req: PackAndGoRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_get_db),
    current_user: Any = Depends(_current_user),
):
    try:
        (
            file_roles,
            document_types,
            include_printouts,
            include_geometry,
            export_type,
        ) = _resolve_export_preset(
            export_type=req.export_type,
            file_roles=req.file_roles,
            document_types=req.document_types,
            include_printouts=req.include_printouts,
            include_geometry=req.include_geometry,
            fields_set=_model_fields_set(req),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        filename_mode = _normalize_filename_mode(req.filename_mode)
        path_strategy = _normalize_path_strategy(req.path_strategy)
        collision_strategy = _normalize_collision_strategy(req.collision_strategy)
        file_scope = _normalize_file_scope(req.file_scope)
        if req.filename_template:
            _validate_filename_template(req.filename_template)
        manifest_columns = (
            _normalize_manifest_csv_columns(req.manifest_csv_columns)
            if req.include_manifest_csv
            else None
        )
        bom_flat_format = (
            _normalize_bom_flat_format(req.bom_flat_format)
            if req.include_bom_flat
            else None
        )
        bom_flat_columns = (
            _normalize_bom_flat_columns(req.bom_flat_columns)
            if req.include_bom_flat
            else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_file_roles = list(file_roles or _DEFAULT_FILE_ROLES)
    resolved_document_types = list(document_types or _DEFAULT_DOCUMENT_TYPES)
    cache_enabled = _env_bool("YUANTUS_PACKGO_CACHE_ENABLED", False)
    cache_ttl_minutes = _env_int("YUANTUS_PACKGO_CACHE_TTL_MINUTES", 60)
    cache_key = None
    if cache_enabled:
        cache_key = _build_cache_key(
            _build_cache_payload(
                item_id=req.item_id,
                depth=req.depth,
                export_type=export_type,
                file_roles=resolved_file_roles,
                document_types=resolved_document_types,
                include_previews=req.include_previews,
                include_printouts=include_printouts,
                include_geometry=include_geometry,
                filename_mode=filename_mode,
                filename_template=req.filename_template,
                path_strategy=path_strategy,
                collision_strategy=collision_strategy,
                file_scope=file_scope,
                include_bom_tree=req.include_bom_tree,
                bom_tree_filename=req.bom_tree_filename,
                include_manifest_csv=req.include_manifest_csv,
                manifest_csv_filename=req.manifest_csv_filename,
                manifest_csv_columns=manifest_columns,
                include_bom_flat=req.include_bom_flat,
                bom_flat_format=bom_flat_format,
                bom_flat_filename=req.bom_flat_filename,
                bom_flat_columns=bom_flat_columns,
                relationship_types=req.relationship_types,
                include_item_types=req.include_item_types,
                exclude_item_types=req.exclude_item_types,
                include_item_ids=req.include_item_ids,
                exclude_item_ids=req.exclude_item_ids,
                allowed_states=req.allowed_states,
                blocked_states=req.blocked_states,
                allowed_extensions=req.allowed_extensions,
                blocked_extensions=req.blocked_extensions,
                require_locked_versions=req.require_locked_versions,
            )
        )

    ctx = get_request_context()
    context = {
        "tenant_id": ctx.tenant_id,
        "org_id": ctx.org_id,
        "user_id": str(getattr(current_user, "id", "")) or None,
    }

    # A4-R1: dry-run / manifest-first. Takes precedence over async_flag; bypasses the
    # cache entirely (always a fresh manifest) and the zip/artifact/download path.
    # Returns the plan + manifest (incl. drawing staleness) synchronously.
    if req.dry_run:
        try:
            dry_manifest = build_pack_and_go_package(
                db,
                item_id=req.item_id,
                depth=req.depth,
                file_roles=resolved_file_roles,
                document_types=resolved_document_types,
                include_previews=req.include_previews,
                include_printouts=include_printouts,
                include_geometry=include_geometry,
                export_type=export_type,
                filename_mode=filename_mode,
                filename_template=req.filename_template,
                path_strategy=path_strategy,
                collision_strategy=collision_strategy,
                file_scope=file_scope,
                dry_run=True,
                include_item_types=req.include_item_types,
                exclude_item_types=req.exclude_item_types,
                include_item_ids=req.include_item_ids,
                exclude_item_ids=req.exclude_item_ids,
                allowed_states=req.allowed_states,
                blocked_states=req.blocked_states,
                allowed_extensions=req.allowed_extensions,
                blocked_extensions=req.blocked_extensions,
                include_bom_tree=req.include_bom_tree,
                bom_tree_filename=req.bom_tree_filename,
                include_manifest_csv=req.include_manifest_csv,
                manifest_csv_filename=req.manifest_csv_filename,
                manifest_csv_columns=manifest_columns,
                include_bom_flat=req.include_bom_flat,
                bom_flat_format=bom_flat_format,
                bom_flat_filename=req.bom_flat_filename,
                bom_flat_columns=bom_flat_columns,
                relationship_types=req.relationship_types,
                cache_key=None,
                cache_enabled=False,
                cache_ttl_minutes=cache_ttl_minutes,
                context=context,
                output_dir=Path(
                    _env_str("YUANTUS_PACKGO_OUTPUT_DIR", "./tmp/pack_and_go")
                ),
                require_locked_versions=req.require_locked_versions,
            )
        except BundleVersionLockError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "bundle_version_lock_violation",
                    "message": str(exc),
                    "unlocked": exc.unlocked,
                    "mismatched": exc.mismatched,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        stale_summary = dry_manifest.get("drawing_staleness_summary", {}) or {}
        plan = {
            "item_count": len(
                {f.get("source_item_id") for f in dry_manifest.get("files", [])}
            ),
            "file_count": dry_manifest.get("file_count", 0),
            "total_drawings": stale_summary.get("total_drawings", 0),
            "stale_drawings": stale_summary.get("needs_update", 0),
            "would_zip": False,
        }
        warnings: List[str] = []
        if stale_summary.get("needs_update"):
            warnings.append(
                f"{stale_summary['needs_update']} drawing(s) are stale (needs_update); "
                "included with a warning, not excluded (set exclude_stale_drawings in a "
                "future release to drop them)."
            )
        return JSONResponse(
            {
                "ok": True,
                "dry_run": True,
                "manifest": dry_manifest,
                "plan": plan,
                "warnings": warnings,
            }
        )

    if req.async_flag:
        from yuantus.meta_engine.services.job_service import JobService

        job_service = JobService(db)
        payload = _build_job_payload(req, user_id=str(getattr(current_user, "id", "")) or None)
        payload.update(
            {
                "export_type": export_type,
                "file_roles": resolved_file_roles,
                "document_types": resolved_document_types,
                "include_printouts": include_printouts,
                "include_geometry": include_geometry,
                "filename_mode": filename_mode,
                "filename_template": req.filename_template,
                "path_strategy": path_strategy,
                "collision_strategy": collision_strategy,
                "file_scope": file_scope,
                "manifest_csv_columns": manifest_columns,
                "bom_flat_format": bom_flat_format,
                "bom_flat_columns": bom_flat_columns,
                "cache_enabled": cache_enabled,
                "cache_ttl_minutes": cache_ttl_minutes,
                "cache_key": cache_key,
                "context": context,
            }
        )
        job = job_service.create_job("pack_and_go", payload, user_id=getattr(current_user, "id", None))
        status_url = str(request.url_for("pack_and_go_job_status", job_id=job.id))
        return JSONResponse(
            PackAndGoJobResponse(ok=True, job_id=job.id, status_url=status_url).model_dump()
        )

    output_dir = Path(_env_str("YUANTUS_PACKGO_OUTPUT_DIR", "./tmp/pack_and_go"))
    retention_minutes = _env_int("YUANTUS_PACKGO_RETENTION_MINUTES", 30)
    _prune_old_outputs(output_dir, retention_minutes)

    try:
        result = build_pack_and_go_package(
            db,
            item_id=req.item_id,
            depth=req.depth,
            file_roles=resolved_file_roles,
            document_types=resolved_document_types,
            include_previews=req.include_previews,
            include_printouts=include_printouts,
            include_geometry=include_geometry,
            export_type=export_type,
            filename_mode=filename_mode,
            filename_template=req.filename_template,
            path_strategy=path_strategy,
            collision_strategy=collision_strategy,
            file_scope=file_scope,
            include_item_types=req.include_item_types,
            exclude_item_types=req.exclude_item_types,
            include_item_ids=req.include_item_ids,
            exclude_item_ids=req.exclude_item_ids,
            allowed_states=req.allowed_states,
            blocked_states=req.blocked_states,
            allowed_extensions=req.allowed_extensions,
            blocked_extensions=req.blocked_extensions,
            include_bom_tree=req.include_bom_tree,
            bom_tree_filename=req.bom_tree_filename,
            include_manifest_csv=req.include_manifest_csv,
            manifest_csv_filename=req.manifest_csv_filename,
            manifest_csv_columns=manifest_columns,
            include_bom_flat=req.include_bom_flat,
            bom_flat_format=bom_flat_format,
            bom_flat_filename=req.bom_flat_filename,
            bom_flat_columns=bom_flat_columns,
            relationship_types=req.relationship_types,
            cache_key=cache_key,
            cache_enabled=cache_enabled,
            cache_ttl_minutes=cache_ttl_minutes,
            context=context,
            output_dir=output_dir,
            require_locked_versions=req.require_locked_versions,
        )
    except BundleVersionLockError as exc:
        # Must catch BEFORE the generic ``ValueError`` below: this is a
        # version-lock enforcement failure (HTTP 409), not the existing
        # ValueError → 404 path (which is for missing items / inputs).
        raise HTTPException(
            status_code=409,
            detail={
                "error": "bundle_version_lock_violation",
                "message": str(exc),
                "unlocked": exc.unlocked,
                "mismatched": exc.mismatched,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _cleanup(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            return

    background_tasks.add_task(_cleanup, result.zip_path)
    return FileResponse(
        result.zip_path,
        filename=result.zip_name,
        media_type="application/zip",
        background=background_tasks,
    )


@router.get("/jobs/{job_id}", name="pack_and_go_job_status")
def pack_and_go_job_status(
    job_id: str,
    request: Request,
    db: Session = Depends(_get_db),
    _current_user: Any = Depends(_current_user),
) -> Dict[str, Any]:
    from yuantus.meta_engine.services.job_service import JobService

    service = JobService(db)
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    payload = job.payload or {}
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    safe_result = dict(result) if isinstance(result, dict) else {}
    safe_result.pop("zip_path", None)
    progress = payload.get("progress") if isinstance(payload, dict) else None
    download_url = None
    zip_path = result.get("zip_path") if isinstance(result, dict) else None
    if job.status == "completed" and zip_path:
        if os.path.exists(zip_path):
            download_url = str(
                request.url_for("pack_and_go_job_download", job_id=job.id)
            )

    return {
        "id": job.id,
        "status": job.status,
        "task_type": job.task_type,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "result": safe_result,
        "progress": progress,
        "download_url": download_url,
    }


@router.get("/jobs/{job_id}/download", name="pack_and_go_job_download")
def pack_and_go_job_download(
    job_id: str,
    db: Session = Depends(_get_db),
    _current_user: Any = Depends(_current_user),
):
    from yuantus.meta_engine.services.job_service import JobService

    service = JobService(db)
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    payload = job.payload or {}
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    zip_path = result.get("zip_path") if isinstance(result, dict) else None
    zip_name = result.get("zip_name") if isinstance(result, dict) else None

    if job.status != "completed" or not zip_path:
        raise HTTPException(status_code=409, detail="Job not completed")
    if not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="Package not found")

    return FileResponse(
        zip_path,
        filename=zip_name or Path(zip_path).name,
        media_type="application/zip",
    )


def handle_pack_and_go_job(
    payload: Dict[str, Any],
    session: Session,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    item_id = payload.get("item_id")
    if not item_id:
        raise ValueError("item_id required")

    output_dir = Path(_env_str("YUANTUS_PACKGO_OUTPUT_DIR", "./tmp/pack_and_go"))
    retention_minutes = _env_int("YUANTUS_PACKGO_RETENTION_MINUTES", 30)
    _prune_old_outputs(output_dir, retention_minutes)
    fields_set = {
        key
        for key in payload
        if key in {"file_roles", "document_types", "include_printouts", "include_geometry"}
    }
    try:
        (
            file_roles,
            document_types,
            include_printouts,
            include_geometry,
            _,
        ) = _resolve_export_preset(
            export_type=payload.get("export_type"),
            file_roles=payload.get("file_roles"),
            document_types=payload.get("document_types"),
            include_printouts=bool(payload.get("include_printouts", True)),
            include_geometry=bool(payload.get("include_geometry", True)),
            fields_set=fields_set,
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    try:
        filename_mode = _normalize_filename_mode(payload.get("filename_mode"))
        path_strategy = _normalize_path_strategy(payload.get("path_strategy"))
        collision_strategy = _normalize_collision_strategy(
            payload.get("collision_strategy")
        )
        file_scope = _normalize_file_scope(payload.get("file_scope"))
        filename_template = payload.get("filename_template")
        if filename_template:
            _validate_filename_template(str(filename_template))
        manifest_columns = (
            _normalize_manifest_csv_columns(payload.get("manifest_csv_columns"))
            if payload.get("include_manifest_csv")
            else None
        )
        bom_flat_format = (
            _normalize_bom_flat_format(payload.get("bom_flat_format"))
            if payload.get("include_bom_flat")
            else None
        )
        bom_flat_columns = (
            _normalize_bom_flat_columns(payload.get("bom_flat_columns"))
            if payload.get("include_bom_flat")
            else None
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    resolved_file_roles = list(file_roles or _DEFAULT_FILE_ROLES)
    resolved_document_types = list(document_types or _DEFAULT_DOCUMENT_TYPES)
    cache_enabled = bool(
        payload.get("cache_enabled", _env_bool("YUANTUS_PACKGO_CACHE_ENABLED", False))
    )
    cache_ttl_minutes = int(
        payload.get(
            "cache_ttl_minutes", _env_int("YUANTUS_PACKGO_CACHE_TTL_MINUTES", 60)
        )
    )
    cache_key = payload.get("cache_key")
    if cache_enabled and not cache_key:
        cache_key = _build_cache_key(
            _build_cache_payload(
                item_id=item_id,
                depth=int(payload.get("depth", -1)),
                export_type=payload.get("export_type"),
                file_roles=resolved_file_roles,
                document_types=resolved_document_types,
                include_previews=bool(payload.get("include_previews", False)),
                include_printouts=include_printouts,
                include_geometry=include_geometry,
                filename_mode=filename_mode,
                filename_template=filename_template,
                path_strategy=path_strategy,
                collision_strategy=collision_strategy,
                file_scope=file_scope,
                include_bom_tree=bool(payload.get("include_bom_tree", False)),
                bom_tree_filename=payload.get("bom_tree_filename"),
                include_manifest_csv=bool(payload.get("include_manifest_csv", False)),
                manifest_csv_filename=payload.get("manifest_csv_filename"),
                manifest_csv_columns=manifest_columns,
                include_bom_flat=bool(payload.get("include_bom_flat", False)),
                bom_flat_format=bom_flat_format,
                bom_flat_filename=payload.get("bom_flat_filename"),
                bom_flat_columns=bom_flat_columns,
                relationship_types=payload.get("relationship_types"),
                include_item_types=payload.get("include_item_types"),
                exclude_item_types=payload.get("exclude_item_types"),
                include_item_ids=payload.get("include_item_ids"),
                exclude_item_ids=payload.get("exclude_item_ids"),
                allowed_states=payload.get("allowed_states"),
                blocked_states=payload.get("blocked_states"),
                allowed_extensions=payload.get("allowed_extensions"),
                blocked_extensions=payload.get("blocked_extensions"),
                require_locked_versions=bool(
                    payload.get("require_locked_versions", False)
                ),
            )
        )

    context = payload.get("context")
    if not isinstance(context, dict):
        context = {
            "tenant_id": payload.get("tenant_id"),
            "org_id": payload.get("org_id"),
            "user_id": payload.get("user_id"),
        }

    progress_callback = None
    if job_id:
        from yuantus.meta_engine.services.job_service import JobService

        job_service = JobService(session)

        def _update_progress(
            stage: str,
            current: int,
            total: int,
            message: Optional[str],
            extra: Optional[Dict[str, Any]],
        ) -> None:
            job_service.update_job_progress(
                job_id,
                stage=stage,
                current=current,
                total=total,
                message=message,
                extra=extra,
            )

        progress_callback = _update_progress

    result = build_pack_and_go_package(
        session,
        item_id=item_id,
        depth=int(payload.get("depth", -1)),
        file_roles=resolved_file_roles,
        document_types=resolved_document_types,
        include_previews=bool(payload.get("include_previews", False)),
        include_printouts=include_printouts,
        include_geometry=include_geometry,
        export_type=payload.get("export_type"),
        filename_mode=filename_mode,
        filename_template=filename_template,
        path_strategy=path_strategy,
        collision_strategy=collision_strategy,
        file_scope=file_scope,
        include_item_types=payload.get("include_item_types"),
        exclude_item_types=payload.get("exclude_item_types"),
        include_item_ids=payload.get("include_item_ids"),
        exclude_item_ids=payload.get("exclude_item_ids"),
        allowed_states=payload.get("allowed_states"),
        blocked_states=payload.get("blocked_states"),
        allowed_extensions=payload.get("allowed_extensions"),
        blocked_extensions=payload.get("blocked_extensions"),
        include_bom_tree=bool(payload.get("include_bom_tree", False)),
        bom_tree_filename=payload.get("bom_tree_filename"),
        include_manifest_csv=bool(payload.get("include_manifest_csv", False)),
        manifest_csv_filename=payload.get("manifest_csv_filename"),
        manifest_csv_columns=manifest_columns,
        include_bom_flat=bool(payload.get("include_bom_flat", False)),
        bom_flat_format=bom_flat_format,
        bom_flat_filename=payload.get("bom_flat_filename"),
        bom_flat_columns=bom_flat_columns,
        relationship_types=payload.get("relationship_types"),
        cache_key=cache_key,
        cache_enabled=cache_enabled,
        cache_ttl_minutes=cache_ttl_minutes,
        context=context,
        progress_callback=progress_callback,
        output_dir=output_dir,
        require_locked_versions=bool(payload.get("require_locked_versions", False)),
    )

    return {
        "zip_path": str(result.zip_path),
        "zip_name": result.zip_name,
        "file_count": result.file_count,
        "total_bytes": result.total_bytes,
        "cache_key": result.manifest.get("cache_key") if result.manifest else None,
        "cache_hit": result.manifest.get("cache_hit") if result.manifest else None,
    }


def register_job_handlers(worker: Any) -> None:
    worker.register_handler("pack_and_go", handle_pack_and_go_job)
