from __future__ import annotations

import contextlib
import inspect
import json
import os
import shutil
import socket
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pytest
import requests
import uvicorn
from sqlalchemy.orm import sessionmaker

PACT_DOCS_URL = "https://docs.pact.io/implementation_guides/python/docs/provider"
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PACT_DIR = REPO_ROOT / "contracts" / "pacts"
DEFAULT_PROVIDER_NAME = os.getenv("YUANTUS_PACT_PROVIDER_NAME", "YuantusPLM")
DEFAULT_HOST = os.getenv("YUANTUS_PACT_HOST", "localhost")


def _pact_dir() -> Path:
    return Path(os.getenv("YUANTUS_PACT_DIR", str(DEFAULT_PACT_DIR))).resolve()


def _available_pact_files(pact_dir: Path) -> list[Path]:
    return sorted(path for path in pact_dir.glob("*.json") if path.is_file())


def _find_free_port(host: str = DEFAULT_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _provider_state_handler(
    state: str,
    action: str,
    parameters: dict[str, Any] | None,
) -> None:
    """
    Provider state handler.

    We still pre-seed all required data once at the top of the test
    (see `_seed_pact_fixtures`) rather than seeding per-state. The handler
    therefore stays a no-op; the state names exist for documentation and
    for future work where some interactions may need per-state isolation.
    """

    _ = (state, action, parameters)
    return None


# ---------------------------------------------------------------------------
# Test database isolation
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _isolated_test_database():
    """
    Spin up an ephemeral SQLite database scoped to a tempdir, and patch all
    yuantus globals so the FastAPI app's lifespan and request handlers use it.

    On exit, restore the original env vars / globals and delete the tempdir.

    Why this is necessary:
      - `yuantus.database.engine` is created at module import time from the
        process-wide DATABASE_URL. Setting an env var after the import has
        no effect on that already-bound engine. We have to reassign the
        module-level globals after changing the env var.
      - `yuantus.security.auth.database` caches its engine via _engine /
        _sessionmaker globals; we have to reset those too.
      - `get_settings` is `@lru_cache`d, so the cache must be cleared.
      - We force `TENANCY_MODE=single` so the verifier does not need to
        understand multi-tenant DB routing.
    """
    tmpdir = tempfile.mkdtemp(prefix="yuantus_pact_")
    db_path = os.path.join(tmpdir, "pact_meta.db")
    identity_db_path = os.path.join(tmpdir, "pact_identity.db")

    # Settings uses env_prefix="YUANTUS_", so all overrides MUST use that
    # prefix. Without the prefix the values are silently ignored and the
    # .env file's defaults (db-per-tenant-org, yuantus_mt_skip.db, ...) win.
    saved_env = {
        key: os.environ.get(key)
        for key in (
            "YUANTUS_DATABASE_URL",
            "YUANTUS_IDENTITY_DATABASE_URL",
            "YUANTUS_TENANCY_MODE",
            "YUANTUS_ENVIRONMENT",
            "YUANTUS_SCHEMA_MODE",
            "YUANTUS_AUTH_MODE",
        )
    }
    os.environ["YUANTUS_DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["YUANTUS_IDENTITY_DATABASE_URL"] = f"sqlite:///{identity_db_path}"
    os.environ["YUANTUS_TENANCY_MODE"] = "single"
    os.environ["YUANTUS_ENVIRONMENT"] = "dev"
    os.environ["YUANTUS_SCHEMA_MODE"] = "create_all"
    os.environ["YUANTUS_AUTH_MODE"] = "optional"

    # Clear cached settings so the new env vars take effect.
    from yuantus.config import get_settings
    get_settings.cache_clear()

    # Reset the module-level engine in yuantus.database. The default
    # `engine` was created at import time from whatever DATABASE_URL was
    # then. Now we rebuild it against the new tempdir.
    import yuantus.database as db_mod

    saved_db_engine = db_mod.engine
    saved_db_sessionlocal = db_mod.SessionLocal
    saved_tenant_engines = dict(db_mod._tenant_engines)
    saved_tenant_sessions = dict(db_mod._tenant_sessions)
    saved_tenant_init_done = set(db_mod._tenant_init_done)

    db_mod.engine = db_mod.create_db_engine()
    db_mod.SessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        bind=db_mod.engine,
    )
    db_mod._tenant_engines.clear()
    db_mod._tenant_sessions.clear()
    db_mod._tenant_init_done.clear()

    # Reset the identity DB cache so init_identity_db rebuilds against the
    # new IDENTITY_DATABASE_URL.
    import yuantus.security.auth.database as id_mod

    saved_id_engine = id_mod._engine
    saved_id_sessionmaker = id_mod._sessionmaker
    saved_id_engine_url = id_mod._engine_url
    id_mod._engine = None
    id_mod._sessionmaker = None
    id_mod._engine_url = None

    try:
        yield {
            "tmpdir": tmpdir,
            "db_path": db_path,
            "identity_db_path": identity_db_path,
        }
    finally:
        # Restore yuantus.database globals
        db_mod.engine = saved_db_engine
        db_mod.SessionLocal = saved_db_sessionlocal
        db_mod._tenant_engines.clear()
        db_mod._tenant_engines.update(saved_tenant_engines)
        db_mod._tenant_sessions.clear()
        db_mod._tenant_sessions.update(saved_tenant_sessions)
        db_mod._tenant_init_done.clear()
        db_mod._tenant_init_done.update(saved_tenant_init_done)

        # Restore identity DB
        id_mod._engine = saved_id_engine
        id_mod._sessionmaker = saved_id_sessionmaker
        id_mod._engine_url = saved_id_engine_url

        # Restore env vars
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        get_settings.cache_clear()

        # Cleanup tempdir
        shutil.rmtree(tmpdir, ignore_errors=True)


def _wait_for_ready(
    health_url: str,
    thread: threading.Thread,
    thread_exception: dict[str, BaseException],
    deadline: float,
) -> None:
    """
    Poll the provider's /api/v1/health until it responds 200, the thread
    dies, or the deadline expires.

    Only readiness-probe exceptions (requests / network) are caught here.
    Anything else propagates.
    """
    last_error: Exception | None = None
    while time.time() < deadline:
        if not thread.is_alive():
            cause = thread_exception.get("error")
            if cause is not None:
                raise RuntimeError(
                    f"Pact provider server crashed during startup: "
                    f"{type(cause).__name__}: {cause}"
                ) from cause
            raise RuntimeError(
                "Pact provider server thread exited cleanly before becoming "
                "ready (uvicorn returned without binding); inspect lifespan "
                "startup hooks in yuantus.api.app._lifespan."
            )
        try:
            response = requests.get(health_url, timeout=0.5)
            if response.ok:
                return
            last_error = RuntimeError(
                f"health endpoint returned {response.status_code}"
            )
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(0.1)

    raise RuntimeError(
        f"Pact provider server did not become ready at {health_url}"
    ) from last_error


# ---------------------------------------------------------------------------
# Test data seeding
# ---------------------------------------------------------------------------


# Constants used by both the pact JSON examples and the seed function. The
# pact request bodies hard-code these values, so seed must match exactly.
PACT_TENANT_ID = "tenant-1"
PACT_USERNAME = "metasheet-svc"
PACT_PASSWORD = "secret"
PACT_ITEM_TYPE = "Part"
PACT_DOCUMENT_ITEM_TYPE = "Document"
PACT_SUBSTITUTE_ITEM_TYPE = "Part BOM Substitute"
PACT_ITEM_ID_PRIMARY = "01H000000000000000000000P1"
PACT_ITEM_ID_SECONDARY = "01H000000000000000000000P2"
PACT_ITEM_ID_SUBSTITUTE = "01H000000000000000000000P3"
PACT_ITEM_ID_POST_CHILD = "01H000000000000000000000P4"
PACT_ITEM_ID_DELETE_CHILD = "01H000000000000000000000P5"
PACT_VERSION_ID_PRIMARY = "01H000000000000000000000V1"
PACT_VERSION_ID_SECONDARY = "01H000000000000000000000V2"
PACT_VERSION_ID_SUBSTITUTE = "01H000000000000000000000V3"
PACT_VERSION_ID_POST_CHILD = "01H000000000000000000000V4"
PACT_VERSION_ID_DELETE_CHILD = "01H000000000000000000000V5"
PACT_BOM_RELATIONSHIP_TYPE = "Part BOM"
PACT_DOCUMENT_RELATIONSHIP_TYPE = "Document Part"
PACT_BOM_LINE_ID_PRIMARY = "01H000000000000000000000R1"
PACT_BOM_LINE_ID_POST = "01H000000000000000000000R3"
PACT_BOM_LINE_ID_DELETE = "01H000000000000000000000R4"
PACT_DOCUMENT_ID_PRIMARY = "01H000000000000000000000D1"
PACT_DOCUMENT_REL_ID_PRIMARY = "01H000000000000000000000R2"
PACT_SUBSTITUTE_REL_ID_LIST = "01H000000000000000000000R5"
PACT_SUBSTITUTE_REL_ID_DELETE = "01H000000000000000000000R6"
PACT_FILE_ID_PRIMARY = "01H000000000000000000000F1"
PACT_ITEM_FILE_ID_PRIMARY = "01H000000000000000000000A1"
PACT_CAD_FILE_ID_PROPERTIES_GET = "01H000000000000000000000F2"
PACT_CAD_FILE_ID_PROPERTIES_PATCH = "01H000000000000000000000F3"
PACT_CAD_FILE_ID_VIEW_GET = "01H000000000000000000000F4"
PACT_CAD_FILE_ID_VIEW_PATCH = "01H000000000000000000000F5"
PACT_CAD_FILE_ID_REVIEW_GET = "01H000000000000000000000F6"
PACT_CAD_FILE_ID_REVIEW_POST = "01H000000000000000000000F7"
PACT_CAD_FILE_ID_HISTORY = "01H000000000000000000000F8"
PACT_CAD_FILE_ID_DIFF_LEFT = "01H000000000000000000000F9"
PACT_CAD_FILE_ID_DIFF_RIGHT = "01H000000000000000000000F10"
PACT_CAD_FILE_ID_MESH = "01H000000000000000000000F11"
PACT_ECO_STAGE_ID_HISTORY = "01H000000000000000000000S1"
PACT_ECO_STAGE_ID_APPROVE = "01H000000000000000000000S2"
PACT_ECO_STAGE_ID_REJECT = "01H000000000000000000000S3"
PACT_ECO_ID_HISTORY = "01H000000000000000000000E1"
PACT_ECO_ID_APPROVE = "01H000000000000000000000E2"
PACT_ECO_ID_REJECT = "01H000000000000000000000E3"
PACT_APPROVAL_ID_HISTORY = "01H000000000000000000000H1"
PACT_CAD_DOCUMENT_PATH_VIEW_PATCH = "pact/cad/view-patch-document.json"
PACT_CAD_METADATA_PATH_MESH = "pact/cad/mesh-stats-metadata.json"

WAVE5_CAD_DOWNLOAD_PAYLOADS: dict[str, dict[str, Any]] = {}


def _seed_pact_fixtures() -> None:
    """
    Pre-seed the isolated test databases with everything every Wave 1 P0
    interaction needs. Called once after the FastAPI app has been created
    but before the server thread starts handling requests.

    Wave 1.5 / 2 milestones:
      - M2: identity tenant + user for /auth/login
      - M3: ItemType('Part') + Item for /search and /aml/apply
      - M4: ItemType('Part BOM') + child Item + BOM relationship for /bom/{id}/tree
      - M5: second comparable Item for /bom/compare
      - M6: FileContainer + ItemFile for /file/item and /file/{id}
      - M7: ItemType('Document') + ItemType('Document Part') + relation for /aml/query expand
      - M8: ECO stages + ECO records for /eco/{id}/approvals|approve|reject
      - M9: ECO list/detail fixtures plus isolated BOM substitute read/write rows
    """
    _seed_identity_user()
    _seed_meta_engine_data()


def _register_wave5_download_payload(path: str, payload: dict[str, Any]) -> None:
    WAVE5_CAD_DOWNLOAD_PAYLOADS[path] = payload


def _seed_meta_engine_data() -> None:
    """
    Seed the meta engine database with the ItemType + Item rows the pact
    interactions need. Uses direct SQLAlchemy writes to bypass the AML
    engine's permission/lifecycle/validation pipeline — those subsystems
    are not the target of the pact contract test, the field shapes are.
    """
    import sys
    import uuid
    import yuantus.database as db_mod
    from yuantus.database import init_db
    from yuantus.meta_engine.models.cad_audit import CadChangeLog
    from yuantus.meta_engine.models.eco import ECO, ECOApproval, ECOStage
    from yuantus.meta_engine.models.file import FileContainer, ItemFile
    from yuantus.meta_engine.models.item import Item
    from yuantus.meta_engine.models.meta_schema import ItemType, Property
    from yuantus.meta_engine.version.models import ItemVersion
    from yuantus.security.rbac.models import RBACUser

    sys.stderr.write(
        f"[seed] db_mod.engine.url={db_mod.engine.url}\n"
    )
    init_db(create_tables=True, bind_engine=db_mod.engine)

    # Use the module-level SessionLocal, which we reassigned in
    # _isolated_test_database to bind to the temp DB.
    #
    # Seed in two phases to avoid SQLAlchemy ORM flush-ordering issues:
    # the Item model's circular relationships (current_version_id,
    # source_id, related_id) confuse the topological sorter, so we
    # commit ItemTypes first, then Items in a second session.

    # Phase A: create ItemTypes
    with db_mod.SessionLocal() as session:
        if session.get(ItemType, PACT_ITEM_TYPE) is None:
            session.add(
                ItemType(
                    id=PACT_ITEM_TYPE,
                    label="Part",
                    is_relationship=False,
                    is_versionable=True,
                    properties_schema={},
                    methods={},
                )
            )
        session.flush()

        existing_property_names = {
            name
            for (name,) in (
                session.query(Property.name)
                .filter(Property.item_type_id == PACT_ITEM_TYPE)
                .all()
            )
        }

        def _add_part_property(
            name: str,
            label: str,
            data_type: str,
            *,
            length: int | None = None,
            is_required: bool = False,
            default_value: str | None = None,
        ) -> None:
            if name in existing_property_names:
                return
            session.add(
                Property(
                    item_type_id=PACT_ITEM_TYPE,
                    name=name,
                    label=label,
                    data_type=data_type,
                    length=length,
                    is_required=is_required,
                    default_value=default_value,
                )
            )
            existing_property_names.add(name)

        # The metadata endpoint reads ItemType.properties, so seed the
        # minimal Part field set directly from the Property table.
        _add_part_property(
            "item_number",
            "料号",
            "string",
            length=64,
            is_required=True,
        )
        _add_part_property("name", "Name", "string", length=128)
        _add_part_property("description", "Description", "string", length=256)
        _add_part_property("state", "State", "string", length=32)
        _add_part_property("cost", "Cost", "float", length=18)
        _add_part_property("weight", "Weight", "float", length=18)
        if session.get(ItemType, PACT_BOM_RELATIONSHIP_TYPE) is None:
            session.add(
                ItemType(
                    id=PACT_BOM_RELATIONSHIP_TYPE,
                    label="Part BOM",
                    is_relationship=True,
                    is_versionable=False,
                    source_item_type_id=PACT_ITEM_TYPE,
                    related_item_type_id=PACT_ITEM_TYPE,
                    properties_schema={},
                    methods={},
                )
            )
        if session.get(ItemType, PACT_DOCUMENT_ITEM_TYPE) is None:
            session.add(
                ItemType(
                    id=PACT_DOCUMENT_ITEM_TYPE,
                    label="Document",
                    is_relationship=False,
                    is_versionable=True,
                    properties_schema={},
                    methods={},
                )
            )
        if session.get(ItemType, PACT_DOCUMENT_RELATIONSHIP_TYPE) is None:
            session.add(
                ItemType(
                    id=PACT_DOCUMENT_RELATIONSHIP_TYPE,
                    label="Document Part",
                    is_relationship=True,
                    is_versionable=False,
                    source_item_type_id=PACT_ITEM_TYPE,
                    related_item_type_id=PACT_DOCUMENT_ITEM_TYPE,
                    properties_schema={},
                    methods={},
                )
            )
        if session.get(ItemType, PACT_SUBSTITUTE_ITEM_TYPE) is None:
            session.add(
                ItemType(
                    id=PACT_SUBSTITUTE_ITEM_TYPE,
                    label="Part BOM Substitute",
                    is_relationship=True,
                    is_versionable=False,
                    source_item_type_id=PACT_BOM_RELATIONSHIP_TYPE,
                    related_item_type_id=PACT_ITEM_TYPE,
                    properties_schema={},
                    methods={},
                )
            )
        session.commit()
        sys.stderr.write("[seed] ItemTypes committed\n")

    # Phase B: create Items (parent, child, related Document, relationships)
    with db_mod.SessionLocal() as session:
        if session.get(Item, PACT_ITEM_ID_PRIMARY) is None:
            session.add(
                Item(
                    id=PACT_ITEM_ID_PRIMARY,
                    item_type_id=PACT_ITEM_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Released",
                    is_versionable=True,
                    properties={
                        "item_number": "P-0001",
                        "name": "Mounting Bracket",
                        "description": "Steel mounting bracket",
                    },
                )
            )
        if session.get(Item, PACT_ITEM_ID_SECONDARY) is None:
            session.add(
                Item(
                    id=PACT_ITEM_ID_SECONDARY,
                    item_type_id=PACT_ITEM_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Released",
                    is_versionable=True,
                    properties={
                        "item_number": "P-0002",
                        "name": "Bolt M6",
                        "description": "Metric fastener for bracket assembly",
                    },
                )
            )
        if session.get(Item, PACT_ITEM_ID_SUBSTITUTE) is None:
            session.add(
                Item(
                    id=PACT_ITEM_ID_SUBSTITUTE,
                    item_type_id=PACT_ITEM_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Released",
                    is_versionable=True,
                    properties={
                        "item_number": "P-0003",
                        "name": "Alt Bracket Assembly",
                        "description": "Alternate bracket assembly for search coverage",
                    },
                )
            )
        if session.get(Item, PACT_ITEM_ID_POST_CHILD) is None:
            session.add(
                Item(
                    id=PACT_ITEM_ID_POST_CHILD,
                    item_type_id=PACT_ITEM_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Released",
                    is_versionable=True,
                    properties={
                        "item_number": "P-0004",
                        "name": "Washer M6",
                        "description": "Washer used on substitute POST fixture",
                    },
                )
            )
        if session.get(Item, PACT_ITEM_ID_DELETE_CHILD) is None:
            session.add(
                Item(
                    id=PACT_ITEM_ID_DELETE_CHILD,
                    item_type_id=PACT_ITEM_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Released",
                    is_versionable=True,
                    properties={
                        "item_number": "P-0005",
                        "name": "Spacer Block",
                        "description": "Spacer used on substitute DELETE fixture",
                    },
                )
            )
        if session.get(Item, PACT_DOCUMENT_ID_PRIMARY) is None:
            session.add(
                Item(
                    id=PACT_DOCUMENT_ID_PRIMARY,
                    item_type_id=PACT_DOCUMENT_ITEM_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Draft",
                    is_versionable=True,
                    properties={
                        "item_number": "DOC-0001",
                        "doc_number": "DOC-0001",
                        "number": "DOC-0001",
                        "name": "Mounting Bracket Drawing",
                        "current_version_id": "VER-0001",
                        "document_type": "drawing",
                    },
                )
            )
        # BOM relationship: P1 → P2
        if session.get(Item, PACT_BOM_LINE_ID_PRIMARY) is None:
            session.add(
                Item(
                    id=PACT_BOM_LINE_ID_PRIMARY,
                    item_type_id=PACT_BOM_RELATIONSHIP_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Released",
                    is_versionable=False,
                    source_id=PACT_ITEM_ID_PRIMARY,
                    related_id=PACT_ITEM_ID_SECONDARY,
                    properties={
                        "quantity": 4,
                        "uom": "ea",
                        "find_num": "10",
                        "refdes": "B1",
                    },
                )
            )
        if session.get(Item, PACT_BOM_LINE_ID_POST) is None:
            session.add(
                Item(
                    id=PACT_BOM_LINE_ID_POST,
                    item_type_id=PACT_BOM_RELATIONSHIP_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Released",
                    is_versionable=False,
                    source_id=PACT_ITEM_ID_PRIMARY,
                    related_id=PACT_ITEM_ID_POST_CHILD,
                    properties={
                        "quantity": 2,
                        "uom": "ea",
                        "find_num": "20",
                        "refdes": "W1",
                    },
                )
            )
        if session.get(Item, PACT_BOM_LINE_ID_DELETE) is None:
            session.add(
                Item(
                    id=PACT_BOM_LINE_ID_DELETE,
                    item_type_id=PACT_BOM_RELATIONSHIP_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Released",
                    is_versionable=False,
                    source_id=PACT_ITEM_ID_PRIMARY,
                    related_id=PACT_ITEM_ID_DELETE_CHILD,
                    properties={
                        "quantity": 1,
                        "uom": "ea",
                        "find_num": "30",
                        "refdes": "S1",
                    },
                )
            )
        if session.get(Item, PACT_DOCUMENT_REL_ID_PRIMARY) is None:
            session.add(
                Item(
                    id=PACT_DOCUMENT_REL_ID_PRIMARY,
                    item_type_id=PACT_DOCUMENT_RELATIONSHIP_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Released",
                    is_versionable=False,
                    source_id=PACT_ITEM_ID_PRIMARY,
                    related_id=PACT_DOCUMENT_ID_PRIMARY,
                    properties={
                        "role": "related_document",
                        "quantity": 1,
                        "uom": "ea",
                        "find_num": "DOC-10",
                        "refdes": "DOC1",
                    },
                )
            )
        if session.get(Item, PACT_SUBSTITUTE_REL_ID_LIST) is None:
            session.add(
                Item(
                    id=PACT_SUBSTITUTE_REL_ID_LIST,
                    item_type_id=PACT_SUBSTITUTE_ITEM_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Active",
                    is_versionable=False,
                    source_id=PACT_BOM_LINE_ID_PRIMARY,
                    related_id=PACT_ITEM_ID_SUBSTITUTE,
                    properties={
                        "rank": 1,
                        "note": "preferred alternate",
                    },
                )
            )
        if session.get(Item, PACT_SUBSTITUTE_REL_ID_DELETE) is None:
            session.add(
                Item(
                    id=PACT_SUBSTITUTE_REL_ID_DELETE,
                    item_type_id=PACT_SUBSTITUTE_ITEM_TYPE,
                    config_id=str(uuid.uuid4()),
                    generation=1,
                    is_current=True,
                    state="Active",
                    is_versionable=False,
                    source_id=PACT_BOM_LINE_ID_DELETE,
                    related_id=PACT_ITEM_ID_SUBSTITUTE,
                    properties={
                        "rank": 2,
                        "note": "remove me",
                    },
                )
            )
        session.commit()
        sys.stderr.write("[seed] Items + relationships + substitutes committed\n")

    # Phase B.5: attach released current versions for Part targets consumed by
    # BOM/substitute write paths. latest-released guard now checks both
    # Item.current_version_id and ItemVersion.is_released, so seed must match
    # production semantics rather than relying on state="Released" alone.
    with db_mod.SessionLocal() as session:
        released_parts = (
            (PACT_ITEM_ID_PRIMARY, PACT_VERSION_ID_PRIMARY),
            (PACT_ITEM_ID_SECONDARY, PACT_VERSION_ID_SECONDARY),
            (PACT_ITEM_ID_SUBSTITUTE, PACT_VERSION_ID_SUBSTITUTE),
            (PACT_ITEM_ID_POST_CHILD, PACT_VERSION_ID_POST_CHILD),
            (PACT_ITEM_ID_DELETE_CHILD, PACT_VERSION_ID_DELETE_CHILD),
        )
        items_to_update: list[tuple[Item, str]] = []
        item_ids_to_reset: list[str] = []

        for item_id, version_id in released_parts:
            item = session.get(Item, item_id)
            if item is None:
                continue

            version = session.get(ItemVersion, version_id)
            if version is None:
                session.add(
                    ItemVersion(
                        id=version_id,
                        item_id=item_id,
                        generation=1,
                        revision="A",
                        version_label="1.A",
                        state="Released",
                        is_current=True,
                        is_released=True,
                        released_at=datetime.utcnow(),
                        created_at=datetime.utcnow(),
                        properties=dict(item.properties or {}),
                    )
                )

            items_to_update.append((item, version_id))
            item_ids_to_reset.append(item_id)

        session.flush()

        for item, version_id in items_to_update:
            item.current_version_id = version_id
            item.state = "Released"
            item.is_current = True
            item.updated_at = None

        session.commit()
        (
            session.query(Item)
            .filter(Item.id.in_(item_ids_to_reset))
            .update({Item.updated_at: None}, synchronize_session=False)
        )
        session.commit()
        sys.stderr.write("[seed] Released item versions committed\n")

    # Phase C: create attached file + item file link
    with db_mod.SessionLocal() as session:
        if session.get(FileContainer, PACT_FILE_ID_PRIMARY) is None:
            session.add(
                FileContainer(
                    id=PACT_FILE_ID_PRIMARY,
                    filename="mounting-bracket-drawing.pdf",
                    file_type="pdf",
                    mime_type="application/pdf",
                    file_size=2048,
                    author=PACT_USERNAME,
                    source_system="yuantus",
                    source_version="v1",
                    document_version="A",
                    checksum="sha256-demo-file-1",
                    system_path="demo/files/mounting-bracket-drawing.pdf",
                    document_type="drawing",
                    is_native_cad=False,
                    preview_path="demo/previews/mounting-bracket-drawing.png",
                    conversion_status="pending",
                )
            )
        if session.get(ItemFile, PACT_ITEM_FILE_ID_PRIMARY) is None:
            session.add(
                ItemFile(
                    id=PACT_ITEM_FILE_ID_PRIMARY,
                    item_id=PACT_ITEM_ID_PRIMARY,
                    file_id=PACT_FILE_ID_PRIMARY,
                    file_role="primary",
                    sequence=1,
                    description="Primary manufacturing drawing",
                )
            )
        session.commit()
        sys.stderr.write("[seed] Files + attachments committed\n")

    # Phase D: create approval actor, ECO stages, ECO rows, and one history row
    with db_mod.SessionLocal() as session:
        if session.get(RBACUser, 1) is None:
            session.add(
                RBACUser(
                    id=1,
                    user_id=1,
                    username=PACT_USERNAME,
                    email="metasheet-svc@test.local",
                    is_active=True,
                    is_superuser=True,
                )
            )
        for stage_id, name, sequence in (
            (PACT_ECO_STAGE_ID_HISTORY, "Engineering Review", 10),
            (PACT_ECO_STAGE_ID_APPROVE, "Approve Gate", 20),
            (PACT_ECO_STAGE_ID_REJECT, "Reject Gate", 30),
        ):
            if session.get(ECOStage, stage_id) is None:
                session.add(
                    ECOStage(
                        id=stage_id,
                        name=name,
                        sequence=sequence,
                        approval_type="mandatory",
                        approval_roles=None,
                        min_approvals=1,
                        auto_progress=False,
                    )
                )
        session.commit()

        for eco_id, name, stage_id in (
            (PACT_ECO_ID_HISTORY, "History ECO", PACT_ECO_STAGE_ID_HISTORY),
            (PACT_ECO_ID_APPROVE, "Approve ECO", PACT_ECO_STAGE_ID_APPROVE),
            (PACT_ECO_ID_REJECT, "Reject ECO", PACT_ECO_STAGE_ID_REJECT),
        ):
            if session.get(ECO, eco_id) is None:
                session.add(
                    ECO(
                        id=eco_id,
                        name=name,
                        eco_type="bom",
                        product_id=PACT_ITEM_ID_PRIMARY,
                        stage_id=stage_id,
                        state="progress",
                        priority="normal",
                        created_by_id=1,
                    )
                )
        if session.get(ECOApproval, PACT_APPROVAL_ID_HISTORY) is None:
            session.add(
                ECOApproval(
                    id=PACT_APPROVAL_ID_HISTORY,
                    eco_id=PACT_ECO_ID_HISTORY,
                    stage_id=PACT_ECO_STAGE_ID_HISTORY,
                    approval_type="mandatory",
                    required_role="engineer",
                    user_id=1,
                    status="pending",
                    comment="Waiting for engineering review",
                )
            )
        session.commit()
        sys.stderr.write("[seed] ECO approvals committed\n")

    # Phase E: create the Wave 5 CAD fixtures. These are intentionally
    # isolated by file id so the provider-state handler can stay a no-op.
    global WAVE5_CAD_DOWNLOAD_PAYLOADS
    WAVE5_CAD_DOWNLOAD_PAYLOADS = {}

    wave5_now = datetime(2026, 4, 11, 0, 0, 0)
    with db_mod.SessionLocal() as session:
        wave5_specs = [
            {
                "id": PACT_CAD_FILE_ID_PROPERTIES_GET,
                "filename": "wave5-f2.step",
                "cad_properties": {
                    "material": "AL-6061",
                    "finish": "anodized",
                },
                "cad_properties_source": "imported",
                "cad_document_schema_version": 3,
            },
            {
                "id": PACT_CAD_FILE_ID_PROPERTIES_PATCH,
                "filename": "wave5-f3.step",
                "cad_properties": {
                    "material": "AL-7075",
                    "finish": "hard-anodized",
                },
                "cad_properties_source": "manual",
                "cad_document_schema_version": 4,
            },
            {
                "id": PACT_CAD_FILE_ID_VIEW_GET,
                "filename": "wave5-f4.step",
                "cad_view_state": {
                    "hidden_entity_ids": [12, 19],
                    "notes": [
                        {
                            "entity_id": 12,
                            "note": "check hole position",
                            "color": "#FFB020",
                        }
                    ],
                },
                "cad_view_state_source": "client",
                "cad_document_schema_version": 3,
            },
            {
                "id": PACT_CAD_FILE_ID_VIEW_PATCH,
                "filename": "wave5-f5.step",
                "cad_view_state_source": "client",
                "cad_document_schema_version": 3,
                "cad_document_path": PACT_CAD_DOCUMENT_PATH_VIEW_PATCH,
                "cad_document_payload": {
                    "schema_version": 3,
                    "entities": [{"id": 12}, {"id": 19}],
                },
            },
            {
                "id": PACT_CAD_FILE_ID_REVIEW_GET,
                "filename": "wave5-f6.step",
                "cad_review_state": "pending",
                "cad_review_note": "Awaiting review",
                "cad_review_by_id": 1,
            },
            {
                "id": PACT_CAD_FILE_ID_REVIEW_POST,
                "filename": "wave5-f7.step",
                "cad_review_state": "pending",
                "cad_review_note": "",
                "cad_review_by_id": 1,
            },
            {
                "id": PACT_CAD_FILE_ID_HISTORY,
                "filename": "wave5-f8.step",
                "cad_properties": {"material": "AL-6061"},
                "cad_properties_source": "imported",
                "cad_review_state": "pending",
                "cad_review_note": "Awaiting review",
                "cad_review_by_id": 1,
            },
            {
                "id": PACT_CAD_FILE_ID_DIFF_LEFT,
                "filename": "wave5-f9.step",
                "cad_properties": {
                    "coating": "none",
                    "weight_kg": 1.1,
                },
                "cad_document_schema_version": 1,
            },
            {
                "id": PACT_CAD_FILE_ID_DIFF_RIGHT,
                "filename": "wave5-f10.step",
                "cad_properties": {
                    "finish": "anodized",
                    "weight_kg": 1.2,
                },
                "cad_document_schema_version": 2,
            },
            {
                "id": PACT_CAD_FILE_ID_MESH,
                "filename": "wave5-f11.step",
                "cad_metadata_path": PACT_CAD_METADATA_PATH_MESH,
                "cad_metadata_payload": {
                    "kind": "mesh_metadata",
                    "entities": [{"id": 1}, {"id": 2}],
                    "triangle_count": 102400,
                    "bounds": {
                        "min": {"x": 0, "y": 0, "z": 0},
                        "max": {"x": 120, "y": 80, "z": 40},
                    },
                },
            },
        ]

        for spec in wave5_specs:
            if session.get(FileContainer, spec["id"]) is not None:
                continue
            file_container = FileContainer(
                id=spec["id"],
                filename=spec["filename"],
                file_type="step",
                mime_type="model/step",
                file_size=1024,
                author=PACT_USERNAME,
                source_system="wave5",
                source_version="v5",
                document_version="A",
                checksum=f"sha256-{spec['id'].lower()}",
                system_path=f"wave5/{spec['id']}/source.step",
                document_type="2d",
                is_native_cad=True,
                cad_format="STEP",
                cad_connector_id="wave5",
                cad_properties=spec.get("cad_properties"),
                cad_properties_source=spec.get("cad_properties_source"),
                cad_properties_updated_at=wave5_now,
                cad_view_state=spec.get("cad_view_state"),
                cad_view_state_source=spec.get("cad_view_state_source"),
                cad_view_state_updated_at=wave5_now,
                cad_document_path=spec.get("cad_document_path"),
                cad_metadata_path=spec.get("cad_metadata_path"),
                cad_document_schema_version=spec.get("cad_document_schema_version"),
                cad_review_state=spec.get("cad_review_state"),
                cad_review_note=spec.get("cad_review_note"),
                cad_review_by_id=spec.get("cad_review_by_id"),
                cad_reviewed_at=wave5_now if spec.get("cad_review_state") else None,
                created_by_id=1,
            )
            session.add(file_container)
            if "cad_document_payload" in spec and spec.get("cad_document_path"):
                _register_wave5_download_payload(
                    spec["cad_document_path"], spec["cad_document_payload"]
                )
            if "cad_metadata_payload" in spec and spec.get("cad_metadata_path"):
                _register_wave5_download_payload(
                    spec["cad_metadata_path"], spec["cad_metadata_payload"]
                )
        session.commit()

    with db_mod.SessionLocal() as session:
        history_at = wave5_now
        log_specs = [
            (
                PACT_CAD_FILE_ID_HISTORY,
                "cad_properties_update",
                {
                    "properties": {
                        "material": "AL-6061",
                        "finish": "anodized",
                    },
                    "source": "imported",
                    "cad_document_schema_version": 3,
                },
                history_at,
                "cad-chg-1",
            ),
            (
                PACT_CAD_FILE_ID_HISTORY,
                "cad_review_update",
                {
                    "state": "pending",
                    "note": "Awaiting review",
                    "reviewed_by_id": 1,
                },
                history_at + timedelta(minutes=5),
                "cad-chg-2",
            ),
        ]
        for file_id, action, payload, created_at, entry_id in log_specs:
            if session.get(CadChangeLog, entry_id) is None:
                session.add(
                    CadChangeLog(
                        id=entry_id,
                        file_id=file_id,
                        action=action,
                        payload=payload,
                        created_at=created_at,
                        tenant_id=PACT_TENANT_ID,
                        org_id="org-1",
                        user_id=1,
                    )
                )
        session.commit()

    # PLM-COLLAB V1.1: seed an ACTIVE PERPETUAL plm.bom_multitable license for the pact tenant via the
    # PROVEN import path (not a raw insert), so the new manifest + BOM-context interactions verify
    # entitled:true. Ephemeral keypair signs; import_license takes the pubkeys as an argument.
    import base64 as _b64
    import uuid as _uuid
    from cryptography.hazmat.primitives import serialization as _ser
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _Ed25519
    from yuantus.meta_engine.app_framework.license_verification import canonical_payload_bytes as _canon
    from yuantus.meta_engine.app_framework.license_import_service import (
        LicenseImportService as _LicenseImportService,
    )

    _lic_priv = _Ed25519.generate()
    _lic_pub = _b64.b64encode(
        _lic_priv.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)
    ).decode()
    _lic_payload = {
        "tenant_id": PACT_TENANT_ID,
        "app_names": ["plm.bom_multitable"],
        "features": ["bom_multitable"],
        "plan_type": "Pilot",
        "license_key": _uuid.uuid4().hex,
        "subject": "Pact",
        "issued_at": "2026-04-11T00:00:00Z",
        "expires_at": None,
    }
    _lic_obj = {
        "alg": "Ed25519",
        "kid": "pact-kid",
        "payload": _lic_payload,
        "signature": _b64.b64encode(_lic_priv.sign(_canon(_lic_payload))).decode(),
    }
    with db_mod.SessionLocal() as session:
        _LicenseImportService(session).import_license(_lic_obj, {"pact-kid": _lic_pub})
        session.commit()
        sys.stderr.write("[seed] perpetual plm.bom_multitable license committed for V1.1 pact\n")


def _seed_identity_user() -> None:
    """Create the test tenant and user that the auth/login pact uses."""
    from yuantus.security.auth.database import (
        get_identity_db_session,
        init_identity_db,
    )
    from yuantus.security.auth.service import AuthService

    init_identity_db(create_tables=True)

    with get_identity_db_session() as session:
        service = AuthService(session)
        service.ensure_tenant(PACT_TENANT_ID)
        # create_user raises if the user already exists; that's fine in a
        # fresh isolated DB but we guard with a query in case of re-entry.
        from yuantus.security.auth.models import AuthUser

        existing = (
            session.query(AuthUser)
            .filter(
                AuthUser.tenant_id == PACT_TENANT_ID,
                AuthUser.username == PACT_USERNAME,
            )
            .first()
        )
        if existing is None:
            service.create_user(
                tenant_id=PACT_TENANT_ID,
                username=PACT_USERNAME,
                password=PACT_PASSWORD,
                email="metasheet-svc@test.local",
                is_superuser=True,
            )


def _override_current_user(app) -> None:
    """
    Bypass JWT authentication for non-login endpoints by injecting a fake
    superuser via FastAPI dependency overrides. The actual /auth/login
    endpoint is unaffected because it does not depend on get_current_user.
    """
    from yuantus.api.dependencies.auth import (
        CurrentUser,
        get_current_user,
        get_current_user_id_optional,
        get_current_user_optional,
    )

    fake_user = CurrentUser(
        id=1,
        tenant_id=PACT_TENANT_ID,
        org_id="org-1",
        username=PACT_USERNAME,
        email="metasheet-svc@test.local",
        roles=["admin", "superuser"],
        is_superuser=True,
    )

    def _override():
        return fake_user

    app.dependency_overrides[get_current_user] = _override
    app.dependency_overrides[get_current_user_optional] = _override
    app.dependency_overrides[get_current_user_id_optional] = lambda: fake_user.id


@contextlib.contextmanager
def _running_provider(base_host: str = DEFAULT_HOST):
    # Import lazily so that _isolated_test_database has already overridden
    # DATABASE_URL / IDENTITY_DATABASE_URL by the time create_app() runs.
    from yuantus.api.app import create_app
    from yuantus.meta_engine.services import file_service as file_service_mod

    port = _find_free_port(base_host)
    app = create_app()
    _seed_pact_fixtures()
    _override_current_user(app)

    @app.post("/_pact/provider_states")
    async def _pact_provider_states(request: Any):
        payload = await request.json()
        state = str(payload.get("state") or "")
        action = str(payload.get("action") or "setup")
        parameters = payload.get("params")
        _provider_state_handler(
            state,
            action,
            parameters if isinstance(parameters, dict) else None,
        )
        return {"ok": True}

    saved_download_file = file_service_mod.FileService.download_file

    def _patched_download_file(self, file_path: str, output_file_obj: Any) -> None:
        payload = WAVE5_CAD_DOWNLOAD_PAYLOADS.get(file_path)
        if payload is None:
            return saved_download_file(self, file_path, output_file_obj)
        output_file_obj.write(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )

    file_service_mod.FileService.download_file = _patched_download_file
    try:
        config = uvicorn.Config(app, host=base_host, port=port, log_level="error")
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # type: ignore[assignment]

        # Wrap server.run() so any exception inside the thread is captured and
        # surfaced through the readiness loop, instead of disappearing into the
        # generic "thread is not alive" message.
        thread_exception: dict[str, BaseException] = {}

        def _runner() -> None:
            try:
                server.run()
            except BaseException as exc:  # noqa: BLE001 - we re-raise via dict
                thread_exception["error"] = exc
                raise

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()

        base_url = f"http://{base_host}:{port}"
        health_url = f"{base_url}/api/v1/health"
        deadline = time.time() + 15.0

        try:
            # Phase 1: wait until the server is ready. Only readiness errors are
            # caught here; the test body's exceptions are NOT swallowed.
            _wait_for_ready(health_url, thread, thread_exception, deadline)

            # Phase 2: hand the live base_url to the test. Any exception raised by
            # the test body propagates normally — the finally below still runs.
            yield base_url
        finally:
            server.should_exit = True
            thread.join(timeout=10.0)
    finally:
        file_service_mod.FileService.download_file = saved_download_file


def test_yuantus_provider_verifies_local_pacts():
    """
    Verify Yuantus against committed consumer pact files.

    This test intentionally skips when the Pact runtime or pact artifacts are
    not present yet. That lets the repository land the verification skeleton
    before Metasheet publishes the first contract set.

    Reference:
    https://docs.pact.io/implementation_guides/python/docs/provider
    """

    pact = pytest.importorskip(
        "pact",
        reason="Install pact-python to enable provider verification",
    )
    pact_dir = _pact_dir()
    pact_files = _available_pact_files(pact_dir)
    if not pact_files:
        pytest.skip(
            f"No pact files found in {pact_dir}. Expected consumer artifacts such as "
            "'metasheet2-yuantus-plm.json'."
        )

    # Add each pact file individually rather than the whole directory.
    # `.add_source(directory)` would attempt to parse every file in the
    # directory (including README.md) and report a Pact JSON parse error.
    with _isolated_test_database():
        with _running_provider() as base_url:
            verifier_signature = inspect.signature(pact.Verifier)
            if "provider_base_url" in verifier_signature.parameters:
                verifier = pact.Verifier(
                    DEFAULT_PROVIDER_NAME,
                    provider_base_url=base_url,
                )
                success, logs = verifier.verify_pacts(
                    *(str(path) for path in pact_files),
                    provider_states_setup_url=f"{base_url}/_pact/provider_states",
                )
                assert success, logs
                return

            verifier = pact.Verifier(DEFAULT_PROVIDER_NAME)
            for path in pact_files:
                verifier = verifier.add_source(str(path))
            verifier = verifier.state_handler(_provider_state_handler, teardown=True)
            verifier = verifier.add_transport(url=base_url)
            verifier.verify()
