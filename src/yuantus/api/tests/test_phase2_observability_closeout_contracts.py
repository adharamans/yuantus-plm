"""
Phase 2 (Observability Foundation) closeout contracts — regression-prevention.

These contracts pin the *downstream-consumer* surface (log field set, metric
names, label cardinality, bucket boundaries, middleware order) that external
tools (log indexers, Prometheus dashboards, alert rules) depend on. They
extend, not duplicate, the implementation-level tests in P2.1 and P2.2:

- P2.1 chain tests use a synthetic middleware to exercise ContextVar-lifetime;
  the contract here uses the *real* `TenantOrgContextMiddleware` so a future
  refactor that drops the `request.state` snapshot fails loudly.
- P2.2 has registry / endpoint / JobService integration tests; the contracts
  here pin the *exposed* metric names and label set independently of how
  they are produced internally.
"""
from __future__ import annotations

import json
import logging
import re

from fastapi.testclient import TestClient

from yuantus.api.app import create_app
from yuantus.config import get_settings
from yuantus.observability.metrics import (
    duration_buckets,
    record_job_lifecycle,
    render_prometheus_text,
    reset_registry,
)


# ---------------------------------------------------------------------------
# Contract 1: real production middleware chain produces the pinned log field
# set with tenant_id/org_id sourced from headers via TenantOrgContextMiddleware.
# ---------------------------------------------------------------------------


def test_real_middleware_chain_log_line_carries_tenant_org_from_headers(
    caplog,
) -> None:
    """Regression-prevention: if `TenantOrgContextMiddleware` ever stops
    snapshotting `request.state.tenant_id` / `request.state.org_id`, the
    downstream log indexer loses correlation. P2.1's synthetic chain test
    can't catch this — only a real-middleware path through `create_app()`."""
    settings = get_settings()
    original_log = settings.LOG_FORMAT
    original_auth = settings.AUTH_MODE
    settings.LOG_FORMAT = "json"
    settings.AUTH_MODE = "optional"
    try:
        app = create_app()
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="yuantus.request"):
            response = client.get(
                "/api/v1/health",
                headers={"x-tenant-id": "tenant-X", "x-org-id": "org-Y"},
            )

        assert response.status_code == 200
        records = []
        for rec in caplog.records:
            try:
                records.append(json.loads(rec.message))
            except (json.JSONDecodeError, TypeError):
                continue
        assert records, "expected JSON log record from real middleware chain"
        record = records[-1]
        assert record["tenant_id"] == "tenant-X", record
        assert record["org_id"] == "org-Y", record
        assert record["path"] == "/api/v1/health"
        assert record["status_code"] == 200
        assert "request_id" in record
        assert "latency_ms" in record
    finally:
        settings.LOG_FORMAT = original_log
        settings.AUTH_MODE = original_auth


# ---------------------------------------------------------------------------
# Contract 2: metric names and label cardinality are pinned.
# ---------------------------------------------------------------------------


def test_metric_names_are_pinned_to_phase2_contract() -> None:
    """A future rename of `yuantus_jobs_total` or `yuantus_job_duration_ms`
    would silently break every Prometheus alert and Grafana dashboard. This
    contract makes that breakage loud at PR time."""
    reset_registry()
    record_job_lifecycle("cad_convert", "success", 100.0)
    text = render_prometheus_text()

    assert "yuantus_jobs_total" in text
    assert "yuantus_job_duration_ms_bucket" in text
    assert "yuantus_job_duration_ms_sum" in text
    assert "yuantus_job_duration_ms_count" in text


def test_metric_label_set_excludes_high_cardinality_dimensions() -> None:
    """Per P2.2 §8 / §9: per-tenant / per-org / per-user / per-job labels
    blow up cardinality and break Prometheus retention. Only `task_type`,
    `status`, and Prometheus's own `le` (histogram bucket boundary) are
    permitted in the metric output."""
    reset_registry()
    record_job_lifecycle("cad_convert", "success", 100.0)
    record_job_lifecycle("cad_convert", "failure", 100.0)
    record_job_lifecycle("cad_convert", "retry", 100.0)
    text = render_prometheus_text()

    labels = set(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)="', text))
    allowed = {"task_type", "status", "le"}
    unknown = labels - allowed
    assert not unknown, (
        f"unexpected labels in metrics output: {unknown}; "
        f"allowed set is {allowed}"
    )


# ---------------------------------------------------------------------------
# Contract 3: histogram bucket boundaries are pinned.
# ---------------------------------------------------------------------------


def test_histogram_bucket_boundaries_are_pinned() -> None:
    """Buckets are observed by downstream alerts and dashboards
    (`histogram_quantile()` aggregations depend on the boundary set). Per
    P2.2 §4.2: 'Do not change without explicit discussion'. This contract
    makes that documentation enforceable — a bucket change can still happen,
    but only via a deliberate update to this list, which surfaces in PR
    review."""
    expected = (50, 100, 500, 1000, 5000, 10000, 30000, 60000, 300000)
    actual = tuple(duration_buckets())
    assert actual == expected, (
        f"histogram bucket boundaries are part of the observability "
        f"contract; changing them breaks downstream alerts. "
        f"Expected {expected}, got {actual}."
    )


# ---------------------------------------------------------------------------
# Contract 4: middleware order is pinned.
# ---------------------------------------------------------------------------


def test_request_logging_middleware_is_outermost() -> None:
    """RequestLoggingMiddleware must be outermost in Starlette's stack so
    that:
      - request_id_var is set BEFORE inner middleware runs (correlation)
      - the final response status code (including 401s from
        AuthEnforcementMiddleware) is visible in the log line
      - request.state populated by inner middleware is observable when the
        log line is emitted

    Starlette convention: `app.user_middleware[0]` is outermost — last
    `add_middleware()` call wins."""
    app = create_app()
    classes = [m.cls.__name__ for m in app.user_middleware]
    assert classes[0] == "RequestLoggingMiddleware", (
        f"RequestLoggingMiddleware must be `app.user_middleware[0]` "
        f"(outermost). Current order: {classes}"
    )


def test_middleware_chain_order_is_pinned() -> None:
    """Pin the full chain order so a future `add_middleware` reordering
    surfaces in PR review. The order is correctness-load-bearing:
    AuthEnforcementMiddleware must run before TenantOrgContextMiddleware
    (so authenticated tenant/org claims preempt header-based fallbacks),
    InboundRateLimitMiddleware runs after auth so protected requests can use
    the verified tenant identity instead of trusting the header alone,
    and AuditLogMiddleware must be innermost (so it sees the final status
    code AND has identity context populated by upstream middleware)."""
    app = create_app()
    classes = [m.cls.__name__ for m in app.user_middleware]
    assert classes == [
        "RequestLoggingMiddleware",
        "AuthEnforcementMiddleware",
        "InboundRateLimitMiddleware",
        "TenantOrgContextMiddleware",
        "AuditLogMiddleware",
    ], f"middleware chain must be pinned; got {classes}"
