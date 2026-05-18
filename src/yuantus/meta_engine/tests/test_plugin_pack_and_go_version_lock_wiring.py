"""Tests for the pack-and-go plugin version-lock runtime wiring (#590).

Layers:

- Layer 1 (no session): schema field, cache-payload + cache-key safety,
  async job payload, BundleVersionLockError shape, AST contract reuse.
- Layer 2 (SQLite session): the private `_evaluate_bundle_version_lock`
  helper covers the four user-spec'd behavior cases — default-OFF,
  locked-success, unlocked-fail, mismatched-fail, stale-only-success —
  without needing the full Item/BOM/FileService stack. The end-to-end
  `build_pack_and_go_package` path delegates to this helper exactly
  once (AST-pinned).
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.models.item import Item  # noqa: F401 (mapper registry)
from yuantus.meta_engine.services.pack_and_go_version_lock_contract import (
    BundleLockReport,
)
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.models.base import Base


def _load_plugin_module():
    root = Path(__file__).resolve().parents[4]
    plugin_path = root / "plugins" / "yuantus-pack-and-go" / "main.py"
    spec = importlib.util.spec_from_file_location("pack_and_go_plugin", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[ItemVersion.__table__])
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _seed_version(
    session,
    *,
    version_id: str,
    item_id: str,
    is_current: bool = True,
) -> ItemVersion:
    version = ItemVersion(
        id=version_id,
        item_id=item_id,
        version_label="1.A",
        is_current=is_current,
    )
    session.add(version)
    session.flush()
    return version


# --------------------------------------------------------------------------
# Layer 1 — schema, cache payload, async job payload, exception class
# --------------------------------------------------------------------------


def test_request_schema_has_require_locked_versions_default_false():
    module = _load_plugin_module()
    field = module.PackAndGoRequest.model_fields["require_locked_versions"]
    assert field.default is False
    assert field.annotation is bool


def test_cache_payload_includes_require_locked_versions_flag():
    module = _load_plugin_module()
    base_kwargs = dict(
        item_id="root",
        depth=-1,
        export_type=None,
        file_roles=[],
        document_types=[],
        include_previews=False,
        include_printouts=True,
        include_geometry=True,
        filename_mode="original",
        filename_template=None,
        path_strategy="item_role",
        collision_strategy="append_id",
        file_scope="version",
        include_bom_tree=False,
        bom_tree_filename=None,
        include_manifest_csv=False,
        manifest_csv_filename=None,
        manifest_csv_columns=None,
        include_bom_flat=False,
        bom_flat_format=None,
        bom_flat_filename=None,
        bom_flat_columns=None,
        relationship_types=None,
        include_item_types=None,
        exclude_item_types=None,
        include_item_ids=None,
        exclude_item_ids=None,
        allowed_states=None,
        blocked_states=None,
        allowed_extensions=None,
        blocked_extensions=None,
    )
    payload_off = module._build_cache_payload(
        **base_kwargs, require_locked_versions=False
    )
    payload_on = module._build_cache_payload(
        **base_kwargs, require_locked_versions=True
    )
    # Flag present in payload.
    assert payload_off["require_locked_versions"] is False
    assert payload_on["require_locked_versions"] is True
    # Cache keys must differ — caller-spec'd cache-safety pin: an
    # unlocked-bundle cache must not satisfy a locked-bundle request.
    key_off = module._build_cache_key(payload_off)
    key_on = module._build_cache_key(payload_on)
    assert key_off != key_on


def test_request_model_dump_includes_require_locked_versions():
    """Async payload safety: `_build_job_payload` does
    ``req.model_dump(by_alias=True)``; this test pins that the new
    field round-trips through serialization without needing a special
    update line in `_build_job_payload`.
    """

    module = _load_plugin_module()
    req = module.PackAndGoRequest(item_id="root", require_locked_versions=True)
    dumped = req.model_dump(by_alias=True)
    assert dumped["require_locked_versions"] is True

    req_off = module.PackAndGoRequest(item_id="root")
    dumped_off = req_off.model_dump(by_alias=True)
    assert dumped_off["require_locked_versions"] is False


def test_bundle_version_lock_error_carries_ids_and_is_not_valueerror():
    module = _load_plugin_module()
    exc = module.BundleVersionLockError(
        unlocked=["a", "b"],
        mismatched=["c"],
    )
    assert exc.unlocked == ["a", "b"]
    assert exc.mismatched == ["c"]
    msg = str(exc)
    assert "unlocked" in msg and "a" in msg and "b" in msg
    assert "mismatched" in msg and "c" in msg
    # NOT a ValueError — must NOT be caught by the existing
    # `except ValueError → HTTP 404` block at the sync route.
    assert not isinstance(exc, ValueError)


# --------------------------------------------------------------------------
# Layer 1 — AST contract-reuse pins
# --------------------------------------------------------------------------


def test_plugin_module_imports_merged_contracts_in_helper():
    """The plugin's runtime path must reuse the merged 3a resolver and
    the merged version-lock evaluator — not redefine the arithmetic.
    """

    module = _load_plugin_module()
    src = inspect.getsource(module._evaluate_bundle_version_lock)
    assert "resolve_bundle_document_descriptors" in src
    assert "evaluate_bundle_version_locks" in src
    assert "WorkorderDocLinkRow" in src
    assert "ItemVersionRow" in src


def test_plugin_does_not_redefine_lock_arithmetic():
    """No local re-implementation of the merged contract's lock
    classification (locked / unlocked / mismatched / stale).
    """

    module = _load_plugin_module()
    src = inspect.getsource(module)
    # Must NOT define a local function named like the merged contract's
    # surface — re-implementation would silently diverge.
    tree = ast.parse(src)
    func_names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden = {
        "evaluate_bundle_version_locks",
        "assert_bundle_version_locks",
        "resolve_bundle_document_descriptor",
        "resolve_bundle_document_descriptors",
    }
    assert not (func_names & forbidden), (
        f"plugin must not redefine merged contract functions: "
        f"{func_names & forbidden}"
    )


# --------------------------------------------------------------------------
# Layer 2 — `_evaluate_bundle_version_lock` behavior with real session
# --------------------------------------------------------------------------


def test_helper_default_off_returns_report_does_not_raise(session):
    """require_locked_versions=False: helper still returns a
    BundleLockReport so the caller can write
    `version_lock_summary` to the manifest, but never raises even when
    the bundle is fully unlocked.
    """

    module = _load_plugin_module()
    file_links = [
        {"item_id": "doc-1", "source_version_id": None},
        {"item_id": "doc-2", "source_version_id": None},
    ]
    report = module._evaluate_bundle_version_lock(
        session, file_links=file_links, require_locked_versions=False
    )
    assert isinstance(report, BundleLockReport)
    assert report.total == 2
    assert report.unlocked == ["doc-1", "doc-2"]
    assert report.ok is False  # report still reports the truth
    # ...but no raise happened, because require_locked_versions=False.


def test_helper_locked_and_owned_bundle_is_ok(session):
    """All source items pinned to current owned versions: ok=True, no
    unlocked / mismatched / stale entries; helper succeeds whether or
    not enforcement is requested.
    """

    module = _load_plugin_module()
    _seed_version(session, version_id="v-1", item_id="doc-1", is_current=True)
    _seed_version(session, version_id="v-2", item_id="doc-2", is_current=True)
    file_links = [
        {"item_id": "doc-1", "source_version_id": "v-1"},
        {"item_id": "doc-2", "source_version_id": "v-2"},
    ]
    report = module._evaluate_bundle_version_lock(
        session, file_links=file_links, require_locked_versions=True
    )
    assert report.ok is True
    assert report.unlocked == []
    assert report.mismatched == []
    assert report.stale == []
    assert report.locked == 2


def test_helper_unlocked_raises_on_required(session):
    """Bundle with a source item missing source_version_id is unlocked;
    helper raises BundleVersionLockError with the item id in `unlocked`.
    """

    module = _load_plugin_module()
    _seed_version(session, version_id="v-1", item_id="doc-1", is_current=True)
    file_links = [
        {"item_id": "doc-1", "source_version_id": "v-1"},
        {"item_id": "doc-2", "source_version_id": None},  # unlocked
    ]
    with pytest.raises(module.BundleVersionLockError) as exc_info:
        module._evaluate_bundle_version_lock(
            session, file_links=file_links, require_locked_versions=True
        )
    assert exc_info.value.unlocked == ["doc-2"]
    assert exc_info.value.mismatched == []


def test_helper_mismatched_version_raises_on_required(session):
    """Bundle pinned to a version that belongs to a DIFFERENT item is
    mismatched; helper raises BundleVersionLockError with the item id
    in `mismatched`.
    """

    module = _load_plugin_module()
    # v-foreign belongs to doc-OTHER, but doc-1 pins it as its source.
    _seed_version(
        session, version_id="v-foreign", item_id="doc-OTHER", is_current=True
    )
    file_links = [
        {"item_id": "doc-1", "source_version_id": "v-foreign"},
    ]
    with pytest.raises(module.BundleVersionLockError) as exc_info:
        module._evaluate_bundle_version_lock(
            session, file_links=file_links, require_locked_versions=True
        )
    assert exc_info.value.mismatched == ["doc-1"]
    assert exc_info.value.unlocked == []


def test_helper_stale_only_bundle_succeeds_under_required(session):
    """A bundle where every source item is locked & owned but
    `is_current=False` is `stale` only — per the merged contract's
    semantics, stale never blocks. Helper must NOT raise, and the
    report carries stale ids with ok=True.
    """

    module = _load_plugin_module()
    _seed_version(
        session, version_id="v-1", item_id="doc-1", is_current=False
    )
    file_links = [
        {"item_id": "doc-1", "source_version_id": "v-1"},
    ]
    report = module._evaluate_bundle_version_lock(
        session, file_links=file_links, require_locked_versions=True
    )
    assert report.ok is True
    assert report.stale == ["doc-1"]
    assert report.unlocked == []
    assert report.mismatched == []
    assert report.locked == 1


def test_helper_dedupes_multiple_files_per_source_item(session):
    """File links list may contain N files from the same source item;
    the version-lock report must contain ONE descriptor per source
    item, not one per file.
    """

    module = _load_plugin_module()
    _seed_version(session, version_id="v-1", item_id="doc-1", is_current=True)
    file_links = [
        # Three files all from doc-1 at v-1.
        {"item_id": "doc-1", "source_version_id": "v-1"},
        {"item_id": "doc-1", "source_version_id": "v-1"},
        {"item_id": "doc-1", "source_version_id": "v-1"},
    ]
    report = module._evaluate_bundle_version_lock(
        session, file_links=file_links, require_locked_versions=True
    )
    assert report.total == 1
    assert report.locked == 1
    assert report.ok is True


def test_helper_handles_missing_version_row_as_mismatched(session):
    """A bundle pinned to a version_id whose row does not exist
    (caller resolved an id that has since been deleted) follows the
    merged 3a resolver's Branch C: version_belongs_to_item=False,
    version_is_current=None → mismatched.
    """

    module = _load_plugin_module()
    # No ItemVersion row seeded — the lookup returns None.
    file_links = [
        {"item_id": "doc-1", "source_version_id": "v-missing"},
    ]
    with pytest.raises(module.BundleVersionLockError) as exc_info:
        module._evaluate_bundle_version_lock(
            session, file_links=file_links, require_locked_versions=True
        )
    assert exc_info.value.mismatched == ["doc-1"]


def test_item_scope_lock_link_uses_source_item_current_version():
    """Item-scope ItemFile rows do not carry source_version_id, but the
    final manifest uses source_item.current_version_id. The lock input
    must use the same effective version to avoid false unlocked reports.
    """

    module = _load_plugin_module()
    link = {"item_id": "doc-1", "source_version_id": None}
    source_item = SimpleNamespace(id="doc-1", current_version_id="v-current")

    assert module._version_lock_link_for_included_file(link, source_item) == {
        "item_id": "doc-1",
        "source_version_id": "v-current",
    }


def test_build_package_evaluates_lock_after_final_inclusion_filters():
    """The bundle lock report must describe actual packaged files, not
    raw candidate links. Pin the structural ordering: file existence and
    inclusion filters happen before lock links are appended, and the
    helper receives the filtered lock-link list.
    """

    module = _load_plugin_module()
    src = inspect.getsource(module.build_pack_and_go_package)
    file_exists_idx = src.find("if not file_service.file_exists")
    lock_append_idx = src.find("version_lock_file_links.append(")
    pack_append_idx = src.find("pack_files.append(")
    helper_call_idx = src.find("version_lock_report = _evaluate_bundle_version_lock(")

    assert file_exists_idx > 0, "file existence filter not found"
    assert lock_append_idx > 0, "filtered version-lock link append not found"
    assert pack_append_idx > 0, "pack_files append not found"
    assert helper_call_idx > 0, "version-lock helper call not found"
    assert file_exists_idx < lock_append_idx < pack_append_idx < helper_call_idx
    assert "file_links=version_lock_file_links" in src


# --------------------------------------------------------------------------
# Layer 2 — async job handler kwargs passthrough (monkey-patched)
# --------------------------------------------------------------------------


def test_async_handler_passes_require_locked_versions_to_build():
    """`handle_pack_and_go_job(...)` must propagate
    `require_locked_versions` from the job payload into
    `build_pack_and_go_package(...)` so the background path enforces
    the same lock the sync route would. The job-service plumbing is
    too coupled to drive end-to-end here, so this test pins the
    **exact pass-through expression** in source: a typo like a
    hardcoded `True`/`False` instead of reading from the payload
    would silently make sync and async diverge.
    """

    module = _load_plugin_module()
    src = inspect.getsource(module.handle_pack_and_go_job)
    # Exact expression — `payload.get("require_locked_versions", False)`
    # wrapped in `bool(...)`. Pins both the kwarg name and that the
    # value comes from the payload dict (not a hardcoded literal).
    assert 'require_locked_versions=bool(payload.get("require_locked_versions"' in src


def test_async_fallback_cache_payload_includes_require_locked_versions():
    """If an async worker receives cache_enabled=True without a prebuilt
    cache_key, its fallback key recomputation must stay in the same
    locked/not-locked namespace as the sync route.
    """

    module = _load_plugin_module()
    src = inspect.getsource(module.handle_pack_and_go_job)
    fallback_idx = src.find("if cache_enabled and not cache_key:")
    context_idx = src.find("context = payload.get", fallback_idx)
    assert fallback_idx > 0 and context_idx > fallback_idx
    fallback_block = src[fallback_idx:context_idx]

    assert "require_locked_versions=bool(" in fallback_block
    assert 'payload.get("require_locked_versions", False)' in fallback_block


def test_cache_hit_returns_before_version_lock_helper_runs():
    """Cache hit safety: when `build_pack_and_go_package` finds a
    non-expired cached zip, it returns early — BEFORE the version-lock
    helper runs. The cached manifest already carries
    `version_lock_summary` from when the zip was built, and cache-key
    safety (test above) ensures locked-required and not-required
    requests have different keys, so cross-flag pollution is
    impossible. This test pins the structural ordering: the
    `if cached_manifest: ... return` early-return must textually
    precede the `_evaluate_bundle_version_lock(` call in the function
    source, so a future refactor can't accidentally invert them.
    """

    module = _load_plugin_module()
    src = inspect.getsource(module.build_pack_and_go_package)
    cached_return_idx = src.find("return PackAndGoResult(")
    helper_call_idx = src.find("_evaluate_bundle_version_lock(")
    assert cached_return_idx > 0, "cache-hit early return path not found"
    assert helper_call_idx > 0, "version-lock helper call not found"
    assert cached_return_idx < helper_call_idx, (
        "cache-hit return must precede the version-lock helper call "
        "so a cached bundle does NOT re-run the helper (and the helper's "
        "side effect — extra ItemVersion query — is skipped on cache hit)"
    )
