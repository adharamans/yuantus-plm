"""A4-R1: pack-and-go WP1.3 drawing-staleness injection + dry-run / manifest-first.

Exercises the real `build_pack_and_go_package` seam (no HTTP/auth stack) with an
injected fake file_service + seeded items/files:
- dry_run=True returns the manifest dict, builds NO zip.
- drawing-role entries carry needs_update/staleness_reason/source_batch_id/
  model_import_batch_id, file-scope-aware (item->ItemFile, version->VersionFile).
- drawing_staleness_summary is DISTINCT from version_lock_summary; warn-not-exclude.
- the CSV manifest carries the four staleness columns.
"""

from __future__ import annotations

import glob
import importlib.util
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.models.file import FileContainer, ItemFile
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.version.models import ItemVersion, VersionFile
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()

_ROOT = Path(__file__).resolve().parents[4]


def _load_plugin():
    path = _ROOT / "plugins" / "yuantus-pack-and-go" / "main.py"
    spec = importlib.util.spec_from_file_location("pack_and_go_plugin_a4", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module  # register before exec (dataclass KW_ONLY lookup)
    spec.loader.exec_module(module)
    return module


MODULE = _load_plugin()


class _FakeFileService:
    """Resolves every file to one real temp file so the build path proceeds without
    real storage (dry-run never zips, but still resolves source paths for size)."""

    def __init__(self, local_path: str):
        self._local = local_path

    def file_exists(self, system_path):  # noqa: D401
        return True

    def get_local_path(self, system_path):
        return self._local


class _NoDownloadFileService:
    """Cache-miss probe for manifest-first dry-run: no local cache, and download_file fails loudly if
    reached — dry_run must NOT fetch remote blobs just to build a preview (the P1 regression guard)."""

    def file_exists(self, system_path):  # noqa: D401
        return True

    def get_local_path(self, system_path):
        return None

    def download_file(self, system_path, handle):  # noqa: D401
        raise AssertionError(
            "dry_run must be manifest-first: download_file was called on a local-cache miss"
        )


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'a4.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    yield db
    db.close()


@pytest.fixture()
def fake_fs(tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"x")
    return _FakeFileService(str(blob))


def _item(session, iid: str, *, current_version_id: str | None = None) -> Item:
    it = Item(
        id=iid, item_type_id="Part", config_id=f"cfg-{iid}-{uuid.uuid4()}",
        generation=1, is_current=True, state="Active",
        properties={"item_number": iid}, current_version_id=current_version_id,
    )
    session.add(it)
    return it


def _fc(session, file_id: str, document_type: str) -> None:
    session.add(
        FileContainer(
            id=file_id, filename=f"{file_id}.bin",
            system_path=f"/vault/{file_id}.bin", document_type=document_type,
        )
    )


def _build(session, fake_fs, item_id: str, *, file_scope: str = "item") -> dict:
    return MODULE.build_pack_and_go_package(
        session,
        item_id=item_id,
        depth=1,
        file_roles=["native_cad", "drawing"],
        document_types=["3d", "2d"],
        include_previews=False,
        include_printouts=False,
        include_geometry=False,
        file_scope=file_scope,
        dry_run=True,
        include_manifest_csv=True,
        file_service=fake_fs,
        output_dir=Path("./tmp/pack_and_go"),
    )


def _drawing_entry(manifest: dict) -> dict:
    return next(f for f in manifest["files"] if f["file_role"] == "drawing")


# ---------- item-scope: dry-run + staleness injection -------------------------
def test_dry_run_returns_manifest_with_item_scope_drawing_staleness(session, fake_fs):
    item = _item(session, "P")
    _fc(session, "P-model", "3d")
    _fc(session, "P-dwg", "2d")
    session.add(ItemFile(item_id="P", file_id="P-model", file_role="native_cad",
                         import_batch_id="C"))
    session.add(ItemFile(item_id="P", file_id="P-dwg", file_role="drawing",
                         import_batch_id="B", source_batch_id="B",
                         needs_update=True, staleness_reason="model_moved_on"))
    session.commit()

    manifest = _build(session, fake_fs, "P")
    assert isinstance(manifest, dict)  # dry_run returned the manifest, not a zip result
    dwg = _drawing_entry(manifest)
    assert dwg["needs_update"] is True
    assert dwg["staleness_reason"] == "model_moved_on"
    assert dwg["source_batch_id"] == "B"
    assert dwg["model_import_batch_id"] == "C"  # from the item's 3d model link


def test_summary_distinct_from_version_lock_and_not_excluded(session, fake_fs):
    _item(session, "P")
    _fc(session, "P-model", "3d")
    _fc(session, "P-dwg", "2d")
    session.add(ItemFile(item_id="P", file_id="P-model", file_role="native_cad",
                         import_batch_id="C"))
    session.add(ItemFile(item_id="P", file_id="P-dwg", file_role="drawing",
                         import_batch_id="B", source_batch_id="B",
                         needs_update=True, staleness_reason="model_moved_on"))
    session.commit()

    manifest = _build(session, fake_fs, "P")
    summary = manifest["drawing_staleness_summary"]
    assert summary["total_drawings"] == 1
    assert summary["needs_update"] == 1
    assert summary["stale_file_ids"] == ["P-dwg"]
    assert summary["excluded"] is False  # warn, not exclude
    # distinct key + the stale drawing is STILL in the bundle
    assert "version_lock_summary" in manifest and summary is not manifest["version_lock_summary"]
    assert any(f["file_id"] == "P-dwg" for f in manifest["files"])


def test_manifest_csv_carries_staleness_columns(session, fake_fs):
    _item(session, "P")
    _fc(session, "P-model", "3d")
    _fc(session, "P-dwg", "2d")
    session.add(ItemFile(item_id="P", file_id="P-model", file_role="native_cad",
                         import_batch_id="C"))
    session.add(ItemFile(item_id="P", file_id="P-dwg", file_role="drawing",
                         import_batch_id="B", source_batch_id="B",
                         needs_update=True, staleness_reason="model_moved_on"))
    session.commit()

    manifest = _build(session, fake_fs, "P")
    csv_text = MODULE._build_manifest_csv(manifest["files"])
    header = csv_text.splitlines()[0]
    for col in ("needs_update", "staleness_reason", "source_batch_id",
                "model_import_batch_id"):
        assert col in header
    # the drawing row carries the values
    assert "model_moved_on" in csv_text


def test_non_drawing_entry_has_no_staleness(session, fake_fs):
    _item(session, "P")
    _fc(session, "P-model", "3d")
    session.add(ItemFile(item_id="P", file_id="P-model", file_role="native_cad",
                         import_batch_id="C"))
    session.commit()
    manifest = _build(session, fake_fs, "P")
    model = next(f for f in manifest["files"] if f["file_role"] == "native_cad")
    assert model["needs_update"] is None
    assert model["staleness_reason"] is None


# ---------- version-scope: D6 reads from VersionFile --------------------------
def test_version_scope_reads_staleness_from_versionfile(session, fake_fs):
    item = _item(session, "P")
    ver = ItemVersion(id="P-v1", item_id="P", generation=1, revision="A",
                      version_label="1.A", is_current=True, is_released=True)
    session.add(ver)
    session.flush()
    item.current_version_id = "P-v1"
    _fc(session, "P-model", "3d")
    _fc(session, "P-dwg", "2d")
    # version snapshot links carry the (frozen) staleness
    session.add(VersionFile(id="vf-model", version_id="P-v1", file_id="P-model",
                            file_role="native_cad", import_batch_id="C"))
    session.add(VersionFile(id="vf-dwg", version_id="P-v1", file_id="P-dwg",
                            file_role="drawing", import_batch_id="B",
                            source_batch_id="B", needs_update=True,
                            staleness_reason="model_moved_on"))
    session.commit()

    manifest = _build(session, fake_fs, "P", file_scope="version")
    dwg = _drawing_entry(manifest)
    assert dwg["needs_update"] is True  # read from VersionFile, not ItemFile
    assert dwg["source_batch_id"] == "B"
    assert dwg["model_import_batch_id"] == "C"


def test_dry_run_does_not_leak_temp_dir(session, fake_fs):
    # The normal path cleans the gather temp dir post-zip; dry-run skips the zip, so
    # it must clean up itself or each call leaks a temp dir.
    _item(session, "P")
    _fc(session, "P-model", "3d")
    session.add(ItemFile(item_id="P", file_id="P-model", file_role="native_cad",
                         import_batch_id="C"))
    session.commit()
    pattern = os.path.join(tempfile.gettempdir(), "yuantus_packgo_*")
    before = set(glob.glob(pattern))
    _build(session, fake_fs, "P")
    after = set(glob.glob(pattern))
    assert after <= before  # no NEW temp dir left behind


def test_model_batch_prefers_native_cad_over_other_3d(session, fake_fs):
    # The item has a 3D *geometry* (batch G, inserted first) AND a 3D *native_cad*
    # model (batch C). model_import_batch_id must come from the native_cad model (C),
    # not whatever 3D link is traversed first -- matches WP1.3's model M selection.
    _item(session, "P")
    _fc(session, "P-geom", "3d")  # 3D but NOT the model
    _fc(session, "P-model", "3d")  # the 3D native model
    _fc(session, "P-dwg", "2d")
    session.add(ItemFile(item_id="P", file_id="P-geom", file_role="geometry",
                         import_batch_id="G"))
    session.add(ItemFile(item_id="P", file_id="P-model", file_role="native_cad",
                         import_batch_id="C"))
    session.add(ItemFile(item_id="P", file_id="P-dwg", file_role="drawing",
                         import_batch_id="B", source_batch_id="B",
                         needs_update=True, staleness_reason="model_moved_on"))
    session.commit()

    manifest = _build(session, fake_fs, "P")
    assert _drawing_entry(manifest)["model_import_batch_id"] == "C"  # native_cad, not G


# ---------- P1 regression: dry-run is manifest-first (must not download) -------
def test_dry_run_does_not_download_on_cache_miss(session):
    # With NO local cache, building a dry-run preview must NOT fall through to download_file — otherwise a
    # large-assembly preview becomes N synchronous S3/MinIO fetches (heavy I/O / 504). The fake's
    # download_file raises, so reaching it fails this test (RED before the fix); the manifest must still build.
    _item(session, "P")
    _fc(session, "P-model", "3d")
    session.add(ItemFile(item_id="P", file_id="P-model", file_role="native_cad",
                         import_batch_id="C"))
    session.commit()

    pattern = os.path.join(tempfile.gettempdir(), "yuantus_packgo_*")
    before = set(glob.glob(pattern))
    manifest = _build(session, _NoDownloadFileService(), "P")  # raises if it tries to download
    after = set(glob.glob(pattern))

    assert isinstance(manifest, dict)  # dry_run returned the manifest, not a zip result
    assert any(f["file_id"] == "P-model" for f in manifest["files"])  # the file is still in the preview
    assert after <= before  # no temp dir created/leaked (download never reached)
