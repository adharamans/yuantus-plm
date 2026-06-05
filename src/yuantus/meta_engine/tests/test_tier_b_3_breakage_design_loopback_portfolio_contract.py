"""Tier-B #3 §3 breakage design-loopback portfolio drift guard.

Read-only / pure introspection (no DB, no `event_bus.subscribe`,
no fixture). Asserts the catalog's contract surface so a future
refactor breaks loudly here instead of silently drifting from
the closeout MD's design invariants. Pinned per the advisor's
guidance:

- signatures via **subset** (`<=`) — additive kwargs allowed,
  removal isn't;
- Pydantic field defaults via `model_fields[...]`, NOT runtime
  `get_settings()` (would read whatever the env set);
- Prometheus surface + ``summary()``-unchanged + phase-4
  route-count via **source-scan**, not duplicating the §3.7
  runtime harness or the phase-4 assertion.

Companion closeout MD:
``docs/DEV_AND_VERIFICATION_ODOO18_TIER_B_3_BREAKAGE_DESIGN_LOOPBACK_PORTFOLIO_CLOSEOUT_20260520.md``.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from yuantus.config.settings import Settings
from yuantus.meta_engine.events.domain_events import BreakageDesignLoopbackEcoEvent
from yuantus.meta_engine.services.parallel_tasks_service import (
    BreakageDesignLoopbackLinkRace,
    BreakageIncidentService,
    ParallelOpsOverviewService,
)


_REPO = Path(__file__).resolve().parents[4]
_CLOSEOUT = (
    _REPO
    / "docs"
    / "DEV_AND_VERIFICATION_ODOO18_TIER_B_3_BREAKAGE_DESIGN_LOOPBACK_PORTFOLIO_CLOSEOUT_20260520.md"
)
_SERVICE_SRC = _REPO / "src" / "yuantus" / "meta_engine" / "services" / "parallel_tasks_service.py"
_PHASE4_TEST = (
    _REPO
    / "src"
    / "yuantus"
    / "meta_engine"
    / "tests"
    / "test_phase4_search_closeout_contracts.py"
)

# §1 of the closeout MD enumerates these. PR #601 = umbrella
# remainder taskbook; #602 = §3.1 impl (no separate taskbook).
# §3.2/§3.3/§3.4/§3.6/§3.7 each = (taskbook, impl) pair → 10 PRs.
# Total = 1 + 1 + 10 = 12.
_TIER_B_3_PRS = (601, 602, 603, 604, 605, 606, 607, 608, 609, 610, 611, 612)

_CATALOG_DEV_MDS = (
    "DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_ROUTE_R1_20260519.md",
    "DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_DURABLE_IDEMPOTENCY_R1_20260519.md",
    "DEV_AND_VERIFICATION_ODOO18_BREAKAGE_UPDATE_STATUS_AUTO_TRIGGER_R1_20260519.md",
    "DEV_AND_VERIFICATION_ODOO18_BREAKAGE_HELPDESK_SYNC_AUTO_TRIGGER_R1_20260519.md",
    "DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_EVENT_EMISSION_R1_20260519.md",
    "DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_METRICS_R1_20260519.md",
)


# --------------------------------------------------------------------------
# Closeout MD — exists, lists all 12 PRs, cites all 6 catalog DEV MDs.
# --------------------------------------------------------------------------


def test_closeout_md_exists_and_lists_all_twelve_prs():
    assert _CLOSEOUT.exists(), f"closeout MD missing: {_CLOSEOUT}"
    text = _CLOSEOUT.read_text()
    missing = [pr for pr in _TIER_B_3_PRS if f"#{pr}" not in text]
    assert not missing, f"closeout MD does not list PRs: {missing}"


def test_closeout_md_cites_all_six_catalog_dev_mds():
    text = _CLOSEOUT.read_text()
    missing = [md for md in _CATALOG_DEV_MDS if md not in text]
    assert not missing, f"closeout MD does not cite DEV MDs: {missing}"


@pytest.mark.parametrize("dev_md", _CATALOG_DEV_MDS)
def test_catalog_dev_md_exists_on_disk(dev_md):
    assert (_REPO / "docs" / dev_md).exists(), f"missing DEV MD: {dev_md}"


# --------------------------------------------------------------------------
# Helper + caller signatures — subset, not equality (advisor #1).
# --------------------------------------------------------------------------


def _kw_param_sets(func):
    sig = inspect.signature(func)
    required: set[str] = set()
    optional: set[str] = set()
    for name, param in sig.parameters.items():
        if param.kind is not inspect.Parameter.KEYWORD_ONLY:
            continue
        if param.default is inspect.Parameter.empty:
            required.add(name)
        else:
            optional.add(name)
    return required, optional


def test_auto_trigger_design_loopback_signature_surface():
    required, optional = _kw_param_sets(
        BreakageIncidentService._auto_trigger_design_loopback
    )
    assert {"target_status", "loopback_user_id", "trigger_source"} <= required, (
        f"helper lost required kwargs; required={required}"
    )
    assert {"sync_status", "provider_ticket_status"} <= optional, (
        f"helper lost optional kwargs; optional={optional}"
    )


def test_update_status_signature_surface():
    required, optional = _kw_param_sets(BreakageIncidentService.update_status)
    # `status` is required; `auto_loopback` and `loopback_user_id`
    # are the §3.3 optional opt-in surface.
    assert "status" in required
    assert {"auto_loopback", "loopback_user_id"} <= optional, (
        f"update_status lost §3.3 optional surface; optional={optional}"
    )


# --------------------------------------------------------------------------
# Dedicated unrecoverable-race exception class.
# --------------------------------------------------------------------------


def test_link_race_is_runtime_error_subclass():
    # Pinned by §3.3/§3.4 route ordering: must be RuntimeError so the
    # route's `except ValueError → 404` and `except Exception → 400`
    # clauses don't swallow it before the dedicated 409 handler.
    assert issubclass(BreakageDesignLoopbackLinkRace, RuntimeError)
    assert not issubclass(BreakageDesignLoopbackLinkRace, ValueError), (
        "must NOT be ValueError — route 404 clause would steal it"
    )


# --------------------------------------------------------------------------
# Event class schema — §3.6 (advisor #2: Pydantic model_fields).
# --------------------------------------------------------------------------


def test_breakage_design_loopback_eco_event_schema():
    fields = BreakageDesignLoopbackEcoEvent.model_fields
    required = {n for n, f in fields.items() if f.is_required()}
    # `event_type` has a default on the subclass (`"breakage.design_loopback_eco"`)
    # so it is not "required" in Pydantic's sense; assert via default below.
    assert {
        "incident_id",
        "eco_id",
        "created",
        "trigger_source",
        "incident_status",
    } <= required, f"event lost required fields; required={required}"
    # Optional sync-context fields stay optional (§3.F threading).
    assert fields["sync_status"].is_required() is False
    assert fields["provider_ticket_status"].is_required() is False
    # Frozen event_type discriminator.
    assert fields["event_type"].default == "breakage.design_loopback_eco"


# --------------------------------------------------------------------------
# S2 settings flag default — §3.6 (advisor #2: declared default, not env).
# --------------------------------------------------------------------------


def test_breakage_design_loopback_events_flag_defaults_off():
    field = Settings.model_fields["BREAKAGE_DESIGN_LOOPBACK_EVENTS_ENABLED"]
    assert field.default is False, (
        f"S2 ratified default-OFF; declared default = {field.default!r}"
    )


# --------------------------------------------------------------------------
# §3.7 Prometheus surface — source-scan (advisor #3, no harness duplication).
# --------------------------------------------------------------------------


_EXPECTED_GAUGE_NAMES = (
    "yuantus_parallel_breakage_design_loopback_links_total",
    "yuantus_parallel_breakage_design_loopback_links_by_status",
    "yuantus_parallel_breakage_design_loopback_links_by_severity",
)


def test_prometheus_metrics_emits_three_loopback_gauges_by_name():
    src = _SERVICE_SRC.read_text()
    missing = [name for name in _EXPECTED_GAUGE_NAMES if name not in src]
    assert not missing, f"§3.7 gauge name(s) absent from service source: {missing}"


def test_loopback_metrics_helper_exists_on_ops_overview_service():
    # The §3.7 dedicated helper; §3.A (b) calls it ONLY from
    # `prometheus_metrics`. (Surface containment is also pinned by
    # the next test.)
    assert hasattr(
        ParallelOpsOverviewService, "_breakage_design_loopback_metrics"
    )


# --------------------------------------------------------------------------
# §3.A (b) surface containment — `summary()` does NOT reach the loopback
# helper (advisor #3: source-scan instead of full DB harness).
# --------------------------------------------------------------------------


def test_summary_method_does_not_reference_breakage_design_loopback():
    src = inspect.getsource(ParallelOpsOverviewService.summary)
    assert "_breakage_design_loopback_metrics" not in src, (
        "§3.A (b): summary() must NOT call the loopback helper "
        "(would widen /parallel-ops/summary JSON surface)."
    )
    assert "breakage_design_loopback" not in src, (
        "§3.A (b): summary() must NOT mention `breakage_design_loopback` "
        "(JSON surface must stay byte-identical)."
    )


# --------------------------------------------------------------------------
# Route count — cross-reference, not re-assertion (advisor #4).
# --------------------------------------------------------------------------


def test_phase4_route_count_pin_still_lives_at_705():
    # Cross-reference to the authoritative phase-4 pin. Bumped 691 -> 693 (P1-D) ->
    # 695 (P2-B) -> 697 (P2-C) -> 698 (P2-D ECO capability entry) -> 699 (P2.5
    # integration capability manifest) -> 701 (WP1.3 CAD 2D/3D staleness) -> 702
    # (P3-A BOM multi-table projection) -> 704 (WP1.2 PDM traversal) -> 705
    # (WP1.2 stale-drawings).
    text = _PHASE4_TEST.read_text()
    assert "len(app.routes) == 705" in text, (
        "phase-4 route-count pin (705) must still exist as the "
        "authoritative assertion."
    )


# --------------------------------------------------------------------------
# Canonical trigger_source values are referenced in the service source.
# --------------------------------------------------------------------------


def test_trigger_source_canonical_values_referenced_in_service():
    src = _SERVICE_SRC.read_text()
    for value in ("route", "update_status", "helpdesk_sync"):
        # quoted string literal — distinguishes from incidental word use.
        assert (
            f'"{value}"' in src or f"'{value}'" in src
        ), f"canonical trigger_source value not referenced: {value!r}"
    # The §3.6 §3.F threading expression itself.
    assert (
        'trigger_source="update_status"' in src
        or "trigger_source='update_status'" in src
    ), "update_status caller must thread trigger_source='update_status'"
    assert (
        'trigger_source="helpdesk_sync"' in src
        or "trigger_source='helpdesk_sync'" in src
    ), "apply_helpdesk_ticket_update caller must thread trigger_source='helpdesk_sync'"
