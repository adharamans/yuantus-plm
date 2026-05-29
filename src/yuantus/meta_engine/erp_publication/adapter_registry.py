"""Resolve an ErpPublicationAdapter for a target_system (G2 R3).

R3 supports a single settings-configured generic-HTTP target; every other (and
any unconfigured deployment) resolves to the no-I/O Null adapter — so dev/CI
never accidentally performs a real external write. Multi-target selection / a
per-target config table is a later slice (the `CadConnectorRegistry` idiom).
"""
from __future__ import annotations

from typing import Any, Optional

from yuantus.config import get_settings
from yuantus.meta_engine.erp_publication.adapter import (
    ErpPublicationAdapter,
    NullErpPublicationAdapter,
)


def resolve_adapter(
    target_system: Optional[str], *, settings: Any = None
) -> ErpPublicationAdapter:
    s = settings or get_settings()
    configured = (getattr(s, "PUBLICATION_ERP_TARGET_SYSTEM", "") or "").strip()
    base_url = (getattr(s, "PUBLICATION_ERP_BASE_URL", "") or "").strip()
    if configured and base_url and (target_system or "").strip() == configured:
        # Lazy import so the common Null path doesn't import httpx.
        from yuantus.meta_engine.erp_publication.http_adapter import (
            HttpErpPublicationAdapter,
        )

        return HttpErpPublicationAdapter(settings=s)
    return NullErpPublicationAdapter()
