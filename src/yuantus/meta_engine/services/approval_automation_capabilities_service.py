"""PLM-COLLAB-P2-D: ECO-scenario capability/upgrade descriptor (pure static data).

The in-scenario product entry: "what approval automation can I do here, and if not,
how do I upgrade?". This module holds ONLY static descriptors -- it does NOT touch
the database, does NOT look up an ECO, does NOT read entitlement (the router calls
``EntitlementService.is_entitled`` once and chooses which descriptor to return), and
NEVER writes. It composes the already-shipped P2-A/B/C surfaces; it adds no judgment.

Constants are reused from the P2-C ECO service so the scenario vocabulary stays
single-sourced (FEATURE_KEY / TEMPLATE_KEY / ACTION_ALLOWLIST).
"""
from __future__ import annotations

from typing import Any, Dict

from yuantus.meta_engine.services.approval_automation_eco_service import (
    ACTION_ALLOWLIST,
    FEATURE_KEY,
    TEMPLATE_KEY,
)

# Read-only upgrade hints surfaced when a tenant is NOT entitled. Descriptive only:
# real authorization is the P1-C offline signed license; the P1-D mock path is
# demo-only and is NOT a production authorization route. This adds NO write path.
UPGRADE_HINTS: Dict[str, str] = {
    "license_mode": "offline_signed",
    "mock_activation": "demo_only",
}


def eco_capability_descriptor() -> Dict[str, Any]:
    """The STATIC "what can I do in the ECO approval-automation scenario" descriptor.

    No DB, no ECO lookup, no entitlement read. ``action_status="stubbed"`` makes clear
    the notify capability is still a placeholder (no real DingTalk dispatch yet), so a
    front end does not present it as a live integration. The endpoint templates keep
    the ``{eco_id}`` placeholder -- they point at the per-ECO P2-C surfaces.
    """
    return {
        "scenario": "eco",
        "feature_key": FEATURE_KEY,
        "actions": sorted(ACTION_ALLOWLIST),
        "action_status": "stubbed",
        "template_key": TEMPLATE_KEY,
        "endpoints": {
            "context": "/api/v1/approvals/automation/eco/{eco_id}/context",
            "actions": "/api/v1/approvals/automation/eco/{eco_id}/actions",
        },
    }
