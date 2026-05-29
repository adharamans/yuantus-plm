# DEV & Verification: PLM→ERP Publication Contract — R3 ERP Connector Taskbook

Date: 2026-05-29

Records the doc-only delivery of
`DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R3_ERP_CONNECTOR_TASKBOOK_20260529.md`
— the scope-lock for the first real external-write slice: a generic outbound-HTTP
`ErpPublicationAdapter`. Doc-only: no code; merging it does **not** authorize the
connector implementation. Baseline `main = edb96d4d` (after the R2 worker impl #672).

## 1. What changed

- New connector-slice scope-lock taskbook (generic HTTP; `sent` becomes a real
  POST; adapter shape; HTTP-status→reason mapping; on-wire idempotency; dry-run
  stays side-effect-free; GPL/AGPL boundary; secrets/resiliency; registry;
  closes the three deferred #672 notes; non-goals).
- This DEV/verification record.
- Two sorted `DELIVERY_DOC_INDEX.md` entries (under `## Development &
  Verification`).

## 2. Grounding (against `main = edb96d4d`)

- **External-client mold** = `DedupVisionClient` (settings base-url + token,
  `CircuitBreaker`, `build_outbound_headers`, primary→fallback, httpx + timeout).
- **Adapter seam** = the merged `ErpPublicationAdapter` ABC +
  `SendResult(ok, remote_id, error, error_kind)`; `process()` maps a non-OK send
  to `failed`/`error_kind`, and the worker retries only
  `remote_error`/`adapter_error`.
- **Registry** = `integrations/cad_connectors/registry.py::CadConnectorRegistry`
  idiom for `target_system`→adapter.
- The crux decision (HTTP-status → reason): `4xx → validation_error`
  (non-retryable) vs `5xx`/timeout/`429`/`408`/breaker-open → `remote_error`
  (retryable) — aligns HTTP semantics with the locked reason-based retry rule.
- At-least-once (retry + stale reclaim, PG-only SKIP LOCKED) → the connector sends
  an `Idempotency-Key` and the target must dedupe.

## 3. Locked decisions (summary)

`HttpErpPublicationAdapter` in the `DedupVisionClient` mold; `validate_contract`
**local-only** so dry-run never POSTs (#666/#667 lock survives a real connector);
`send` = the only network call (POST + Authorization + `Idempotency-Key`,
CircuitBreaker-guarded); HTTP-status→reason mapping (§4); on-wire idempotency for
at-least-once safety; GPL/AGPL-clean (built from the endpoint's docs, never
odooplm code); secrets from settings (`PUBLICATION_ERP_*`), never logged;
`target_system`→adapter registry with a single settings-configured target (**no
migration in R3**); the three #672 notes closed (bounded revalidate-raise,
idempotency mitigates at-least-once, downgrade moot). Non-goals: no
`/publication/export`, no vendor SDK, no new publication semantics.

## 4. Verification (this doc-only PR)

- doc-contract pytests — delivery-doc-index references; `## Development &
  Verification` sorting + completeness; doc-index sorting — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass
  (unchanged; no client/helper change).
- `git diff --check` clean.

## 5. Status

Doc-only scope-lock. Ratifying §2–§10 of the taskbook sets the connector
implementation plan; the connector implementation (then `/publication/export`,
and any vendor-specific adapter) each need their own explicit opt-in.
