"""PLM-COLLAB-P2.5 (Integration Handshake): the integration capability manifest.

Generalizes the P2-D scenario capability entry into a single provider-wide handshake a
consumer (MetaSheet) queries on connect to learn "what does this PLM instance support,
and what is this tenant entitled to?", so it can degrade/upgrade gracefully instead of
locking step with PLM deploys.

INVARIANTS (owner-ratified):
- ADVISORY ONLY. The manifest is a UI/degradation hint; it is NEVER an authorization
  source. The real gate stays enforced at every feature endpoint (is_entitled at each
  /context, /actions, /provision call). A stale or spoofed manifest must not be a
  bypass -- hence ``advisory: true`` in the body.
- ``entitled`` is computed ONLY by ``EntitlementService.is_entitled`` (the single
  judgment path) -- never a second/reimplemented check, never a ``license_data`` read.
- ``supported`` = the feature key is LIT in ``FEATURE_APP_NAMES`` (maps to a non-empty
  app-name set). Derived from the registry, tenant-INDEPENDENT.
- Cache boundary is explicit per feature: ``supported`` is tenant-independent (global-
  cacheable); ``entitled`` is tenant-dependent and must NOT be cached across tenants.
- ``schema_version`` is the manifest's own contract version; it evolves additively
  only (same discipline as the committed Pact). It deliberately does NOT expose the
  internal app route-count pin.
- The manifest performs NO write and NO resource (ECO/etc.) lookup.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy.orm import Session

from yuantus.meta_engine.app_framework.entitlement_service import (
    FEATURE_APP_NAMES,
    EntitlementService,
)
from yuantus.meta_engine.services.approval_automation_eco_service import ACTION_ALLOWLIST

SCHEMA_VERSION = "v1"
PROVIDER = "yuantus-plm"

# Per-feature cache scope, ADVISORY metadata for consumers (a hint that ``supported`` is
# global-cacheable while ``entitled`` is tenant-scoped). HTTP caches do NOT read the body,
# so the actual cross-tenant cache prevention is enforced by the router via
# ``Cache-Control: no-store`` + ``Vary`` on the tenant headers -- this field only documents
# the intent.
_CACHE_SCOPE: Dict[str, str] = {"supported": "global", "entitled": "tenant"}

# Integration-relevant features the handshake advertises. The rich descriptor
# (api_version / scenarios / actions / action_status) is emitted ONLY when the feature
# is SUPPORTED (lit). Keys MUST be a subset of FEATURE_APP_NAMES (a test pins this) so
# is_entitled never sees an unknown key. ``bom_multitable`` is lit in P3-B (its own SKU);
# it advertises the read-only ``bom_review`` scenario (the P3-A projection) with NO actions
# -- it is a read surface, not an action surface like approval_automation.
_FEATURE_DESCRIPTORS: Dict[str, Dict[str, Any]] = {
    "approval_automation": {
        "api_version": "v1",
        "scenarios": ["eco"],
        "actions": sorted(ACTION_ALLOWLIST),
        "action_status": "stubbed",
    },
    "bom_multitable": {
        "api_version": "v1",
        "scenarios": ["bom_review"],
    },
    # ECM-P1B: PLM->ECM publish is a release-triggered EVENT surface (an outbox is
    # enqueued on release), not an action or read surface -- it advertises the
    # ``release_publish`` scenario with NO actions. ADVISORY only (the real gate is the
    # ECM_PUBLISH_ENABLED kill-switch + is_entitled at the publish path, not this hint).
    "ecm_publish": {
        "api_version": "v1",
        "scenarios": ["release_publish"],
    },
}


def _feature_entry(session: Session, feature_key: str, descriptor: Dict[str, Any]) -> Dict[str, Any]:
    supported = bool(FEATURE_APP_NAMES.get(feature_key))
    entitled = EntitlementService(session).is_entitled(feature_key)
    entry: Dict[str, Any] = {
        "supported": supported,
        "api_version": descriptor.get("api_version") if supported else None,
        "entitled": entitled,
        "cache_scope": dict(_CACHE_SCOPE),
    }
    if supported:
        # rich descriptor only for a supported feature
        for key in ("scenarios", "actions", "action_status"):
            if key in descriptor:
                entry[key] = descriptor[key]
    return entry


def build_manifest(session: Session) -> Dict[str, Any]:
    """Build the advisory integration capability manifest for the current tenant."""
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": PROVIDER,
        "advisory": True,
        "features": {
            feature_key: _feature_entry(session, feature_key, descriptor)
            for feature_key, descriptor in _FEATURE_DESCRIPTORS.items()
        },
    }
