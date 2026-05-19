"""Tier-B #3 §3.2 durable idempotency — R1 tests (taskbook `3e5104f`).

The 6 MANDATORY exactly-named tests from the taskbook §5 plus
alembic/tenant-baseline coverage. The design ratified after the
round-1 review:

- `BreakageIncident.eco_id` is a **bare String** column
  (nullable, unique, indexed, NO ForeignKey — soft-link
  convention, sidesteps the baseline FK-ordering problem).
- The race-safety mechanism is a **compare-and-swap UPDATE**
  (`WHERE id=:i AND eco_id IS NULL` + rowcount), NOT the UNIQUE
  index. The UNIQUE index is only a cross-incident
  data-integrity backstop.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.lifecycle.models import LifecycleMap, LifecycleState
from yuantus.meta_engine.models.eco import ECO, ECOStage
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.meta_engine.models.parallel_tasks import BreakageIncident
from yuantus.meta_engine.permission.models import Permission
from yuantus.meta_engine.services.parallel_tasks_service import (
    BreakageIncidentService,
)
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.meta_engine.workflow.models import WorkflowMap
from yuantus.models.base import Base
from yuantus.security.rbac.models import (
    RBACPermission,
    RBACResource,
    RBACRole,
    RBACUser,
    rbac_user_permissions,
    rbac_user_roles,
    role_permissions,
)


def _tables():
    return [
        RBACResource.__table__,
        RBACPermission.__table__,
        RBACRole.__table__,
        RBACUser.__table__,
        rbac_user_roles,
        role_permissions,
        rbac_user_permissions,
        Permission.__table__,
        WorkflowMap.__table__,
        LifecycleMap.__table__,
        LifecycleState.__table__,
        ItemType.__table__,
        Item.__table__,
        ItemVersion.__table__,
        BreakageIncident.__table__,
        ECOStage.__table__,
        ECO.__table__,
    ]


def _engine():
    import_all_models()
    # StaticPool + shared in-memory so multiple Sessions on the same
    # engine see one DB (needed for the CAS concurrency test).
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=_tables())
    return engine


def _session(engine=None):
    engine = engine or _engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionLocal()


def _add_user(session, user_id: int) -> None:
    session.add(
        RBACUser(
            id=user_id,
            user_id=user_id,
            username=f"user-{user_id}",
            email=f"user-{user_id}@example.test",
        )
    )
    session.flush()


def _eligible_incident(service, *, description="durable idempotency case"):
    return service.create_incident(
        description=description,
        status="resolved",
        severity="high",
    )


# --------------------------------------------------------------------------
# MANDATORY 1 — schema drift guard
# --------------------------------------------------------------------------


def test_breakage_eco_id_column_schema_pinned():
    """`BreakageIncident.eco_id` must stay a bare String soft-link:
    nullable + unique + indexed, and crucially NO ForeignKey
    (re-adding an FK would re-introduce the P2 tenant-baseline
    table-ordering risk).
    """

    col = BreakageIncident.__table__.columns["eco_id"]
    assert isinstance(col.type, sa.String)
    assert col.nullable is True
    assert col.unique is True
    assert col.index is True
    assert list(col.foreign_keys) == [], (
        "eco_id must have NO ForeignKey — bare soft-link only "
        "(taskbook §3.2 §4.1; matches product_item_id/bom_id/"
        "version_id convention)"
    )


# --------------------------------------------------------------------------
# MANDATORY 2 — durable link wired on first call
# --------------------------------------------------------------------------


def test_create_eco_wires_durable_link_on_first_call():
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _eligible_incident(service)
        session.commit()
        assert incident.eco_id is None

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            first = service.create_breakage_design_loopback_eco(
                incident.id, user_id=42
            )
        session.refresh(incident)
        assert first.created is True
        assert incident.eco_id == first.eco.id

        # Second call: durable eco_id lookup short-circuits. Prove
        # the substring scan is bypassed by corrupting every ECO
        # description envelope first — if the durable path weren't
        # used, the substring scan would now fail to find the ECO
        # and a duplicate would be created.
        for eco in session.query(ECO).all():
            eco.description = "envelope intentionally destroyed"
        session.flush()

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            second = service.create_breakage_design_loopback_eco(
                incident.id, user_id=42
            )
        assert second.created is False
        assert second.eco.id == first.eco.id
        assert session.query(ECO).count() == 1  # no duplicate
    finally:
        session.close()


# --------------------------------------------------------------------------
# MANDATORY 3 — compare-and-swap serializes the concurrent link
# --------------------------------------------------------------------------


def test_create_eco_compare_and_swap_serializes_concurrent_link():
    """Deterministic exercise of the rowcount==0 loser branch.

    Simulates the race where the loser's pre-create dedupe check
    ran while `eco_id` was still NULL (so it proceeds to
    create_eco), but a winner committed `eco_id` before the
    loser's CAS UPDATE fires. The CAS `WHERE eco_id IS NULL` then
    matches zero rows → the loser rolls back its own create_eco
    INSERT and returns the winner with created=False. No duplicate
    ECO is committed.
    """

    engine = _engine()
    setup = _session(engine)
    try:
        _add_user(setup, 7)
        svc_setup = BreakageIncidentService(setup)
        incident = _eligible_incident(svc_setup, description="cas race incident")
        setup.commit()

        # Winner: create + link + commit (separate session).
        winner_sess = _session(engine)
        try:
            svc_winner = BreakageIncidentService(winner_sess)
            with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
                winner = svc_winner.create_breakage_design_loopback_eco(
                    incident.id, user_id=7
                )
            winner_sess.commit()
            assert winner.created is True
        finally:
            winner_sess.close()

        # Loser: force its pre-create dedupe to miss (simulating
        # "checked before the winner committed"), so it proceeds
        # to create_eco + CAS. The CAS must see eco_id already set
        # (winner committed) → rowcount==0 → rollback → return
        # winner.
        loser_sess = _session(engine)
        try:
            svc_loser = BreakageIncidentService(loser_sess)
            with patch.object(
                BreakageIncidentService,
                "_find_breakage_design_loopback_eco_by_reference",
                return_value=None,
            ), patch(
                "yuantus.meta_engine.services.eco_service.enqueue_event"
            ):
                loser = svc_loser.create_breakage_design_loopback_eco(
                    incident.id, user_id=7
                )
            assert loser.created is False
            assert loser.eco is not None
            assert loser.eco.id == winner.eco.id
        finally:
            loser_sess.close()

        # Exactly one ECO committed — the loser's create_eco was
        # rolled back.
        check = _session(engine)
        try:
            assert check.query(ECO).count() == 1
            linked = check.get(BreakageIncident, incident.id)
            assert linked.eco_id == winner.eco.id
        finally:
            check.close()
    finally:
        setup.close()


# --------------------------------------------------------------------------
# MANDATORY 4 — allow_duplicate=True keeps first link, makes detached dup
# --------------------------------------------------------------------------


def test_allow_duplicate_true_preserves_first_eco_id_and_creates_unlinked_duplicate():
    session = _session()
    try:
        _add_user(session, 99)
        service = BreakageIncidentService(session)
        incident = _eligible_incident(service, description="dup-allowed case")
        session.commit()

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            first = service.create_breakage_design_loopback_eco(
                incident.id, user_id=99
            )
            session.commit()
            dup = service.create_breakage_design_loopback_eco(
                incident.id, user_id=99, allow_duplicate=True
            )
            session.commit()

        session.refresh(incident)
        assert first.created is True
        assert dup.created is True
        assert dup.eco.id != first.eco.id  # a genuine second ECO
        # eco_id stays the canonical first ECO — the explicit
        # duplicate is intentionally NOT back-referenced.
        assert incident.eco_id == first.eco.id
        assert session.query(ECO).count() == 2
    finally:
        session.close()


# --------------------------------------------------------------------------
# MANDATORY 5 — substring-scan fallback for pre-migration data
# --------------------------------------------------------------------------


def test_substring_scan_fallback_handles_historical_incidents():
    """A pre-migration incident has `eco_id=NULL` but an ECO whose
    `description` carries the closeout envelope. The find method
    must fall back to the substring scan and return that historical
    ECO; a subsequent create call must NOT create a duplicate.
    """

    session = _session()
    try:
        _add_user(session, 5)
        service = BreakageIncidentService(session)
        incident = _eligible_incident(service, description="historical case")
        session.commit()

        # Build the historical ECO the way the service's first run
        # would have (pre-§3.2: no eco_id link, just the envelope).
        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            historical = service.create_breakage_design_loopback_eco(
                incident.id, user_id=5
            )
            session.commit()

        # Simulate pre-migration state: clear the durable link so
        # only the description envelope remains.
        session.execute(
            sa.update(BreakageIncident)
            .where(BreakageIncident.id == incident.id)
            .values(eco_id=None)
        )
        session.commit()
        session.refresh(incident)
        assert incident.eco_id is None

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            again = service.create_breakage_design_loopback_eco(
                incident.id, user_id=5
            )
        assert again.created is False
        assert again.eco.id == historical.eco.id
        assert session.query(ECO).count() == 1  # substring fallback hit
    finally:
        session.close()


# --------------------------------------------------------------------------
# MANDATORY 6 — dangling eco_id degrades to no link (no FK = no cascade)
# --------------------------------------------------------------------------


def test_dangling_eco_id_degrades_to_no_link():
    """No FK means no ON DELETE SET NULL. A `eco_id` pointing at a
    since-deleted ECO must degrade gracefully: the lookup returns
    None and a subsequent create proceeds to make a fresh ECO
    rather than crashing on the dangling id.
    """

    session = _session()
    try:
        _add_user(session, 8)
        service = BreakageIncidentService(session)
        incident = _eligible_incident(service, description="dangling case")
        session.commit()

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            first = service.create_breakage_design_loopback_eco(
                incident.id, user_id=8
            )
            session.commit()

        # Hard-delete the linked ECO (no FK cascade — eco_id now
        # dangles).
        session.query(ECO).filter(ECO.id == first.eco.id).delete()
        session.commit()
        session.refresh(incident)
        assert incident.eco_id == first.eco.id  # still points at it

        # Lookup degrades to None; create proceeds to a fresh ECO.
        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            recreated = service.create_breakage_design_loopback_eco(
                incident.id, user_id=8
            )
        assert recreated.created is True
        assert recreated.eco.id != first.eco.id
        session.refresh(incident)
        assert incident.eco_id == recreated.eco.id
    finally:
        session.close()


# --------------------------------------------------------------------------
# Alembic + tenant baseline coverage
# --------------------------------------------------------------------------


def test_alembic_upgrade_head_creates_eco_id_column():
    """Live-DB upgrade (offline --sql mode is broken repo-wide).
    Fresh SQLite, `alembic upgrade head`, inspect the column +
    its unique index.
    """

    import tempfile

    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[4]
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "alembic_eco_id.db"
        cfg = Config(str(repo_root / "alembic.ini"))
        cfg.set_main_option("script_location", str(repo_root / "migrations"))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "head")

        eng = create_engine(f"sqlite:///{db_path}")
        insp = sa.inspect(eng)
        cols = {c["name"] for c in insp.get_columns("meta_breakage_incidents")}
        assert "eco_id" in cols
        idx = {ix["name"]: ix for ix in insp.get_indexes("meta_breakage_incidents")}
        assert "ix_meta_breakage_incidents_eco_id" in idx
        # SQLite inspector returns `unique` as 1/0, not True/False.
        assert bool(idx["ix_meta_breakage_incidents_eco_id"]["unique"]) is True
        eng.dispose()


def test_tenant_baseline_includes_breakage_eco_id_column():
    """Fresh-tenant provisioning must include `eco_id` without
    depending on the alembic migration running first. Source-scan
    the baseline file for the column declaration + the unique
    index (the file is a deterministic op-script; scanning source
    is the established pattern for baseline drift coverage).
    """

    repo_root = Path(__file__).resolve().parents[4]
    baseline = (
        repo_root
        / "migrations_tenant"
        / "versions"
        / "t1_initial_tenant_baseline.py"
    ).read_text()
    assert "sa.Column('eco_id', sa.String(), nullable=True)" in baseline
    assert (
        "op.create_index(op.f('ix_meta_breakage_incidents_eco_id'), "
        "'meta_breakage_incidents', ['eco_id'], unique=True)" in baseline
    )
