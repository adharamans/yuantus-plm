"""Tier-B #3 §3.7 design-loopback metrics — R1 tests.

Taskbook: `docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_DESIGN_LOOPBACK_METRICS_20260519.md`
(merged #611 `c2e404f`). 9 MANDATORY exactly-named tests.

Pinned:
- §3.A SQL aggregate over the persisted `BreakageIncident.eco_id`
  (NOT an in-memory `event_bus` counter); helper called only
  from `prometheus_metrics`, NOT from `summary()`.
- §3.B live-link 口径: `eco_id IN (SELECT id FROM meta_ecos)` —
  dangling rows (ECO hard-deleted) excluded.
- §3.C `created_vs_reused` explicitly NOT done.
- §3.D exactly three `yuantus_parallel_breakage_design_loopback_links_*`
  gauges with **no `common_labels`** (Medium 2).
- §3.E current-state, not `created_at`-windowed.
- §3.F no new route; route count follows the current app-level pin;
  `summary()`/`/parallel-ops/summary[/export]` JSON
  byte-identical (Medium 1).
"""

from __future__ import annotations

import ast
import inspect
import re
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.models.eco import ECO, ECOStage
from yuantus.meta_engine.models.job import ConversionJob
from yuantus.meta_engine.models.parallel_tasks import (
    BreakageIncident,
    ConsumptionPlan,
    ConsumptionRecord,
    ECOActivityGate,
    ECOActivityGateEvent,
    RemoteSite,
    ThreeDOverlay,
    WorkflowCustomActionRule,
    WorkflowCustomActionRun,
    WorkorderDocumentLink,
)
from yuantus.meta_engine.services.parallel_tasks_service import (
    BreakageIncidentService,
    ParallelOpsOverviewService,
)
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser


# --------------------------------------------------------------------------
# Harness — mirrors §3.6 (StaticPool shared in-memory + ECO + RBAC tables).
# --------------------------------------------------------------------------


def _tables():
    """Curated list — mirrors the proven `test_parallel_ops_router_e2e.py`
    harness (the precedent for `summary()` / `prometheus_metrics()`
    tests) **plus** `ECO`/`ECOStage` so we can create linked
    incidents via `create_breakage_design_loopback_eco`.
    `Base.metadata.create_all` without a filter fails on cross-
    metadata FKs (e.g. `meta_effectivities → users`).
    """

    return [
        RBACUser.__table__,
        RemoteSite.__table__,
        ECOActivityGate.__table__,
        ECOActivityGateEvent.__table__,
        WorkflowCustomActionRule.__table__,
        WorkflowCustomActionRun.__table__,
        ConsumptionPlan.__table__,
        ConsumptionRecord.__table__,
        BreakageIncident.__table__,
        WorkorderDocumentLink.__table__,
        ThreeDOverlay.__table__,
        ConversionJob.__table__,
        ECOStage.__table__,
        ECO.__table__,
    ]


def _session():
    import_all_models()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=_tables())
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


def _linked_incident(svc, *, status="resolved", severity="high", description="m"):
    incident = svc.create_incident(
        description=description, status=status, severity=severity
    )
    svc.session.commit()
    with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
        svc.create_breakage_design_loopback_eco(incident.id, user_id=42)
    svc.session.commit()
    return incident


def _unlinked_incident(svc, *, status="open", severity="low", description="u"):
    incident = svc.create_incident(
        description=description, status=status, severity=severity
    )
    svc.session.commit()
    return incident


# ==========================================================================
# MANDATORY 1 — links_total counts only live-linked incidents
# ==========================================================================


def test_links_total_counts_only_live_linked_incidents():
    session = _session()
    try:
        _add_user(session, 42)
        svc = BreakageIncidentService(session)
        _linked_incident(svc, description="l-1")
        _linked_incident(svc, description="l-2")
        _linked_incident(svc, description="l-3")
        _unlinked_incident(svc, description="u-1")
        _unlinked_incident(svc, description="u-2")

        ops = ParallelOpsOverviewService(session)
        result = ops._breakage_design_loopback_metrics()
        assert result["links_total"] == 3
    finally:
        session.close()


# ==========================================================================
# MANDATORY 2 — dangling eco_id is NOT counted (live-link 口径, §3.B)
# ==========================================================================


def test_dangling_eco_id_is_not_counted_as_linked():
    session = _session()
    try:
        _add_user(session, 42)
        svc = BreakageIncidentService(session)
        keep = _linked_incident(svc, description="keep")
        gone = _linked_incident(svc, description="gone")
        session.refresh(gone)
        gone_eco_id = gone.eco_id

        ops = ParallelOpsOverviewService(session)
        before = ops._breakage_design_loopback_metrics()
        assert before["links_total"] == 2

        # Hard-delete the ECO — no FK → dangling eco_id on `gone`.
        # §3.2 ratified this as degraded-to-no-link; §3.7 must
        # match (live-link 口径 = (b)).
        session.query(ECO).filter(ECO.id == gone_eco_id).delete()
        session.commit()
        session.refresh(gone)
        assert gone.eco_id == gone_eco_id  # column still dangling

        after = ops._breakage_design_loopback_metrics()
        assert after["links_total"] == 1  # decremented
        # `gone` left both by_status and by_severity. The only
        # live-linked incident is `keep`.
        assert sum(after["by_status"].values()) == 1
        assert sum(after["by_severity"].values()) == 1
        assert keep.status in after["by_status"]
        assert keep.severity in after["by_severity"]
    finally:
        session.close()


# ==========================================================================
# MANDATORY 3 — by_status / by_severity group live-linked only
# ==========================================================================


def test_by_status_and_by_severity_group_live_links():
    session = _session()
    try:
        _add_user(session, 42)
        svc = BreakageIncidentService(session)
        # 2 resolved+high, 1 closed+medium, 1 resolved+low (linked).
        _linked_incident(svc, status="resolved", severity="high", description="rh-1")
        _linked_incident(svc, status="resolved", severity="high", description="rh-2")
        _linked_incident(svc, status="closed", severity="medium", description="cm-1")
        _linked_incident(svc, status="resolved", severity="low", description="rl-1")
        # Unlinked rows must NOT appear.
        _unlinked_incident(svc, status="open", severity="high", description="oh-1")
        _unlinked_incident(svc, status="in_progress", severity="medium", description="ip-1")

        ops = ParallelOpsOverviewService(session)
        result = ops._breakage_design_loopback_metrics()
        assert result["links_total"] == 4
        assert result["by_status"] == {"resolved": 3, "closed": 1}
        assert result["by_severity"] == {"high": 2, "medium": 1, "low": 1}
    finally:
        session.close()


# ==========================================================================
# MANDATORY 4 — empty data emits zero, no error
# ==========================================================================


def test_empty_data_emits_zero_not_error():
    session = _session()
    try:
        ops = ParallelOpsOverviewService(session)
        result = ops._breakage_design_loopback_metrics()
        assert result == {"links_total": 0, "by_status": {}, "by_severity": {}}

        text = ops.prometheus_metrics()
        assert isinstance(text, str) and text.endswith("\n")
        assert (
            "# TYPE yuantus_parallel_breakage_design_loopback_links_total gauge" in text
        )
        # links_total emits as `name 0` (no `{}` block since no labels).
        assert re.search(
            r"^yuantus_parallel_breakage_design_loopback_links_total \d+(\.\d+)?$",
            text,
            re.MULTILINE,
        ), text
    finally:
        session.close()


# ==========================================================================
# MANDATORY 5 — metrics are current-state, not created_at-windowed (§3.E)
# ==========================================================================


def test_metrics_are_current_state_not_window_filtered():
    session = _session()
    try:
        _add_user(session, 42)
        svc = BreakageIncidentService(session)
        old = _linked_incident(svc, description="old")
        # Backdate created_at far outside any `window_days`.
        old.created_at = datetime.utcnow() - timedelta(days=400)
        session.add(old)
        session.commit()

        ops = ParallelOpsOverviewService(session)
        # Tightest window — must still count the old row (§3.E).
        text = ops.prometheus_metrics(window_days=1)
        assert re.search(
            r"^yuantus_parallel_breakage_design_loopback_links_total 1(\.0)?$",
            text,
            re.MULTILINE,
        ), text
    finally:
        session.close()


# ==========================================================================
# MANDATORY 6 — AST guard: no load-all of BreakageIncident entities
# ==========================================================================


def test_no_load_all_uses_sql_aggregate():
    """§3.A: the helper must use `func.count` + `GROUP BY`, NOT
    `session.query(BreakageIncident).all()` / similar entity
    load-all. AST walk per the advisor's spec — forbids any
    `Call` whose `func` is `Attribute(attr="query")` AND whose
    first positional arg is `Name(id="BreakageIncident")`
    anywhere in the helper body. `sa_func.count(BreakageIncident.id)`
    passes because its first arg is a `Call` (`sa_func.count(...)`),
    not a bare `Name`.
    """

    src = inspect.getsource(
        ParallelOpsOverviewService._breakage_design_loopback_metrics
    )
    tree = ast.parse(inspect.cleandoc(src) if False else src.lstrip())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "query"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id == "BreakageIncident":
            offenders.append(ast.dump(node))
    assert not offenders, (
        "load-all guard: `session.query(BreakageIncident)` is forbidden; "
        f"found {offenders}"
    )
    # Positive sanity: the source mentions sa_func.count + group_by.
    assert "sa_func.count" in src
    assert "group_by" in src


# ==========================================================================
# MANDATORY 7 — prometheus surface exposes 3 gauges, no new route
# ==========================================================================


def test_prometheus_surface_exposes_three_gauges_no_new_route():
    session = _session()
    try:
        _add_user(session, 42)
        svc = BreakageIncidentService(session)
        _linked_incident(svc, description="one")
        ops = ParallelOpsOverviewService(session)
        text = ops.prometheus_metrics()
        for name in (
            "yuantus_parallel_breakage_design_loopback_links_total",
            "yuantus_parallel_breakage_design_loopback_links_by_status",
            "yuantus_parallel_breakage_design_loopback_links_by_severity",
        ):
            assert f"# TYPE {name} gauge" in text
    finally:
        session.close()

    # This slice still adds no route. Keep this secondary pin aligned with the
    # current app-level route-count contract (695 after the 2 PLM-COLLAB-P2-B
    # approval-automation routes; 693 after the 2 PLM-COLLAB-P1-D feature-affordance
    # routes; 691 after the OdooPLM G3 BOM auto-layout route).
    from yuantus.api.app import create_app

    app = create_app()
    assert len(app.routes) == 695


# ==========================================================================
# MANDATORY 8 — summary() JSON surface unchanged (Medium 1)
# ==========================================================================


def test_summary_json_surface_unchanged():
    """§3.A (b) / Medium 1: §3.7 does NOT touch `summary()` —
    `/parallel-ops/summary[/export]` JSON stays byte-identical.
    The targeted check: no `breakage_design_loopback` key. Plus
    a robustness check: the keyset is identical whether or not a
    linked incident exists (proves §3.7 didn't conditionally
    inject the key based on data state).
    """

    session = _session()
    try:
        ops = ParallelOpsOverviewService(session)
        empty_summary = ops.summary()
        assert isinstance(empty_summary, dict) and empty_summary
        assert "breakage_design_loopback" not in empty_summary
        empty_keys = frozenset(empty_summary.keys())

        _add_user(session, 42)
        svc = BreakageIncidentService(session)
        _linked_incident(svc, description="linked")
        linked_summary = ops.summary()
        assert "breakage_design_loopback" not in linked_summary
        linked_keys = frozenset(linked_summary.keys())
        # Adding a linked incident must not add or remove summary keys
        # — proves the §3.7 helper is NOT reached via summary().
        assert empty_keys == linked_keys, (empty_keys ^ linked_keys)
    finally:
        session.close()


# ==========================================================================
# MANDATORY 9 — loopback gauges carry no common_labels (Medium 2)
# ==========================================================================


def test_loopback_gauges_have_no_common_labels():
    """§3.D / Medium 2: the three loopback gauges MUST NOT carry
    the existing renderer's `common_labels`
    (`window_days`/`site_id`/`target_object`/`template_key`).
    `*_links_total` has no `{}` block at all; `*_by_status` only
    `{status}`; `*_by_severity` only `{severity}`.
    """

    session = _session()
    try:
        _add_user(session, 42)
        svc = BreakageIncidentService(session)
        _linked_incident(svc, status="resolved", severity="high", description="g-1")
        _linked_incident(svc, status="closed", severity="medium", description="g-2")

        ops = ParallelOpsOverviewService(session)
        text = ops.prometheus_metrics(
            window_days=7,
            site_id="site-X",
            target_object="ECO",
            template_key="tpl-Y",
        )
        all_lines = text.splitlines()
        forbidden = ("window_days=", "site_id=", "target_object=", "template_key=")

        # links_total: no labels at all.
        total_lines = [
            ln
            for ln in all_lines
            if ln.startswith("yuantus_parallel_breakage_design_loopback_links_total")
        ]
        assert total_lines, "links_total line missing"
        for ln in total_lines:
            assert re.match(
                r"^yuantus_parallel_breakage_design_loopback_links_total \d+(\.\d+)?$",
                ln,
            ), f"links_total must have no labels: {ln!r}"
            for tok in forbidden:
                assert tok not in ln, f"forbidden common-label on {ln!r}"

        # by_status: only {status} label.
        status_lines = [
            ln
            for ln in all_lines
            if ln.startswith("yuantus_parallel_breakage_design_loopback_links_by_status{")
        ]
        assert status_lines
        for ln in status_lines:
            assert re.match(
                r'^yuantus_parallel_breakage_design_loopback_links_by_status\{status="[^"]+"\} \d+(\.\d+)?$',
                ln,
            ), f"by_status must only have status label: {ln!r}"
            for tok in forbidden:
                assert tok not in ln

        # by_severity: only {severity} label.
        sev_lines = [
            ln
            for ln in all_lines
            if ln.startswith(
                "yuantus_parallel_breakage_design_loopback_links_by_severity{"
            )
        ]
        assert sev_lines
        for ln in sev_lines:
            assert re.match(
                r'^yuantus_parallel_breakage_design_loopback_links_by_severity\{severity="[^"]+"\} \d+(\.\d+)?$',
                ln,
            ), f"by_severity must only have severity label: {ln!r}"
            for tok in forbidden:
                assert tok not in ln
    finally:
        session.close()
