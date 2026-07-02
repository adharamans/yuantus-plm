# P0-8b inbound rate limiting — design and verification

**Date:** 2026-07-02  
**Branch:** `codex/p0-8b-rate-limit`  
**Basis:** `docs/development/plm-p0-capability-line-todo-20260702.md` and `docs/development/plm-p0-capability-line-living-tracker.md`

## Scope

This slice implements the P0-8b inbound API rate-limit guard as a narrow security-debt follow-up to P0-8a. It does not add commercial quotas, distributed limits, or per-feature entitlement limits.

## Design

- New middleware: `src/yuantus/api/middleware/rate_limit.py`.
- New settings:
  - `YUANTUS_INBOUND_RATE_LIMIT_ENABLED` — default `false`.
  - `YUANTUS_INBOUND_RATE_LIMIT_PER_MINUTE` — default `120`; `0` disables enforcement.
  - `YUANTUS_INBOUND_RATE_LIMIT_BURST` — default `60`; `0` disables enforcement.
  - `YUANTUS_INBOUND_RATE_LIMIT_EXEMPT_PATHS` — comma-separated exact/prefix exemptions.
- Middleware chain is pinned as:
  `RequestLoggingMiddleware -> AuthEnforcementMiddleware -> InboundRateLimitMiddleware -> TenantOrgContextMiddleware -> AuditLogMiddleware`.
- Keying:
  - protected/authenticated traffic uses verified `request.state.tenant_id`;
  - public or unauthenticated traffic falls back to client IP;
  - untrusted tenant headers are ignored before authentication and cannot create fresh buckets.
- Exempt paths include health, metrics, docs, OpenAPI, and favicon.
- Implementation is intentionally process-local. Multi-replica global throttling requires a shared store and is out of scope for this slice.

## Verification

Local verification:

```text
python -m pytest src/yuantus/api/tests/test_rate_limit_middleware.py src/yuantus/api/tests/test_phase2_observability_closeout_contracts.py -q
python -m pytest src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py src/yuantus/meta_engine/tests/test_delivery_doc_index_all_sections_sorting_contracts.py src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py -q
python -m pytest src/yuantus/meta_engine/tests/test_workflow_inline_shell_syntax_contracts.py src/yuantus/meta_engine/tests/test_workflow_script_reference_contracts.py src/yuantus/meta_engine/tests/test_ci_contracts_ci_yml_test_list_order.py src/yuantus/meta_engine/tests/test_ci_contracts_pact_provider_gate.py src/yuantus/meta_engine/tests/test_ci_change_scope_contracts.py -q
python -m py_compile src/yuantus/api/middleware/rate_limit.py src/yuantus/api/app.py src/yuantus/config/settings.py
git diff --check
```

Behavior pinned by tests:

- disabled by default: no request is throttled;
- public requests cannot bypass the IP bucket by rotating `x-tenant-id`;
- verified tenant state gets independent buckets;
- exempt paths are not throttled;
- zero rate/burst disables enforcement;
- production middleware order remains pinned.

## Remaining Work

None for P0-8b. The next opened P0 capability item is P0-1 notification delivery + subscriptions, taskbook-first.
