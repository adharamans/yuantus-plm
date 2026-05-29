# Claude Taskbook: PLM→ERP Publication Contract — R3 ERP Connector (Generic HTTP, Scope-Lock)

Date: 2026-05-29

Type: **Doc-only taskbook (scope-lock for the connector slice).** It locks the
**first real external-write** slice in G2: a concrete `ErpPublicationAdapter`
that POSTs the publication payload to a **generic, vendor-agnostic outbound HTTP
endpoint**. It changes no code. **Merging this taskbook does NOT authorize the
connector implementation** — that requires its own explicit opt-in.

This is the connector phase **beyond R2** (#666 §9: "the real ERP connector is a
later, separate taskbook"). R1 locked the readiness contract/API; R2 built the
adapter-interface + outbox + manual routes + worker, all reaching `sent` only via
the no-I/O Null adapter. **R3 is where `sent` becomes a real external POST.**

Parents: #663–#672 (the full G2 line through the R2 worker impl). Baseline
`main = edb96d4d`.

## 0. What this is (and is not)

- **Generic HTTP, not a vendor SDK.** The connector targets a configurable
  outbound HTTP endpoint (the operator points it at their ERP's webhook/REST
  intake). This is the closest realization of G2's framing — an **outbound
  publication CONTRACT, not an ERP implementation, not Odoo-specific integration**
  (#663). A vendor-specific adapter (Odoo XML/JSON-RPC, 用友/金蝶/SAP) is a
  separate later adapter behind the same registry seam (§9).
- **Real external write.** R3 deliberately lifts the "no external write from R2"
  boundary — under this explicit scope. `send()` performs a real HTTP POST.
- **Still no `/publication/export`** (a separate later slice); R3 is transport,
  not new publication semantics.

## 1. Grounding (against `main = edb96d4d`)

- **External-client idiom** = `integrations/dedup_vision.py::DedupVisionClient`:
  settings-driven `base_url` (`DEDUP_VISION_BASE_URL`) + service token
  (`DEDUP_VISION_SERVICE_TOKEN`), a `CircuitBreaker` (`integrations/
  circuit_breaker.py`, via `get_or_create_breaker`), `build_outbound_headers(
  authorization=...)` (`integrations/http.py`), `httpx` with a timeout,
  primary→fallback `_candidate_base_urls`, `raise_for_status`. This is the mold
  for the R3 adapter's `send()`.
- **Adapter seam (merged #668)** = `erp_publication/adapter.py`:
  `ErpPublicationAdapter(ABC)` with `build_payload(snapshot)->dict`,
  `validate_contract(payload)->ValidationResult`, `send(payload)->SendResult`
  (`SendResult(ok, remote_id, error, error_kind)`), plus `NullErpPublicationAdapter`.
- **How the service consumes a send (merged #668/#670/#672)**: `process()` sets
  `sent` on `send_result.ok`; otherwise `failed` with
  `reason = send_result.error_kind or "remote_error"`. The **worker retries only
  `remote_error`/`adapter_error`** (`reschedule_retry`), never
  `validation_error`/`not_eligible`. `dry_run()` calls only
  `build_payload`+`validate_contract` (never `send`).
- **Registry idiom** = `integrations/cad_connectors/registry.py::CadConnectorRegistry`
  (register/find-by-id/priority + module-singleton) — the model for selecting an
  adapter by `target_system`.
- **Settings** mirror the `DEDUP_VISION_*` Fields (`config/settings.py`).
- **Deferred #672 notes to close here**: (a) the revalidate-raise path defers
  unbounded; (b) `FOR UPDATE SKIP LOCKED` is PG-only / at-least-once is real now;
  (c) PG-oriented `downgrade`.

## 2. Adapter shape (ratify)

**Recommendation:** a concrete `HttpErpPublicationAdapter(ErpPublicationAdapter)`
(suggested `erp_publication/http_adapter.py`), modeled on `DedupVisionClient`:

- `__init__`: `base_url` + token from settings (§8); a `CircuitBreaker`; a
  timeout.
- `build_payload(snapshot)` — **local**: shape the publication payload from the
  outbox snapshot (the R1-B verdict already captured); no I/O.
- `validate_contract(payload)` — **local only, NO network** (see §6): schema /
  required-field checks against the target contract.
- `send(payload)` — the **only** network call: the HTTP POST (§3), wrapped in the
  `CircuitBreaker` with primary→fallback, returning a mapped `SendResult` (§4).

## 3. `sent` = real POST — request contract (ratify)

`send()` issues `POST {PUBLICATION_ERP_BASE_URL}{path}` with:

- body: the JSON publication payload (from `build_payload`);
- `Authorization`: bearer token from settings (§8) via `build_outbound_headers`;
- **`Idempotency-Key`**: the version-scoped key (§5);
- a configured timeout; `CircuitBreaker`-guarded; primary→fallback.

`2xx` → `SendResult(ok=True, remote_id=<server id or the idempotency key>)` →
`process()` sets `sent`. Non-2xx / transport error → mapped per §4.

## 4. HTTP status → `SendResult.error_kind` (ratify — the crux)

The mapping must align HTTP semantics with the **locked reason-based retry rule**
(worker retries `remote_error`/`adapter_error`; never `validation_error`):

| outcome | `SendResult` | row reason | retried? |
|---|---|---|---|
| `2xx` | `ok=True` | — (`sent`) | n/a |
| `5xx`, timeout, connection error, `429`, `408`, breaker-open | `ok=False, error_kind="remote_error"` | `remote_error` | **yes** (backoff) |
| `4xx` (except `408`/`429`) — payload/contract rejected | `ok=False, error_kind="validation_error"` | `validation_error` | **no** (permanent — needs a fix, not a retry) |
| adapter/client bug (unexpected exception escaping) | folds via `process()` `_fail_adapter_error` → `adapter_error` | `adapter_error` | yes |

Pinning `4xx → validation_error` is what stops a permanently-rejected payload from
retrying to `max_attempts`; transient `5xx`/timeout stays retryable.

## 5. At-least-once → on-wire idempotency (ratify)

The outbox is **at-least-once**: retry/backoff and stale-reclaim can re-send the
same row (and `FOR UPDATE SKIP LOCKED` is PG-only). So the connector MUST send an
**`Idempotency-Key`** header = the version-scoped key
(`item_id:version_id:target_system:publication_kind`, or the outbox row id) on
every POST, and the **target endpoint contract requires the ERP to dedupe** on it
(a re-sent publication is acknowledged, not double-applied). This is the R3
mitigation for the #672 at-least-once / SKIP-LOCKED note — correctness rests on
target idempotency, not on never-double-sending.

## 6. Dry-run stays side-effect-free with a real connector (ratify)

The #666/#667 lock — **dry-run produces no external side effect** — MUST survive
R3. Therefore `validate_contract` is **local-only (no network)**: only `send()`
touches the ERP. `dry_run()` (build_payload + validate_contract) never POSTs, even
with the HTTP adapter configured. Any contract check that needs the ERP is part of
`send`, not `validate_contract`.

## 7. GPL/AGPL boundary (ratify — non-negotiable)

The connector is built **from scratch** against the target endpoint's documented
HTTP contract. **No odooplm (or any GPL/AGPL) code is read, ported, or adapted** —
semantic/contract alignment only. Generic HTTP avoids vendor SDKs entirely; a
future vendor adapter (Odoo/用友/金蝶/SAP) must likewise be built from the
vendor's own published API docs, never from GPL/AGPL sources.

## 8. Secrets, auth, resiliency (ratify)

- Token + base-url from **settings/env** (`PUBLICATION_ERP_BASE_URL`,
  `PUBLICATION_ERP_SERVICE_TOKEN`, `PUBLICATION_ERP_TIMEOUT_SECONDS`,
  optional `PUBLICATION_ERP_PATH`) — **never in the repo**; the token is
  **never logged** (redaction, per the S11 secret-handling discipline).
- `CircuitBreaker` (DedupVision mold) trips on repeated failures so a down ERP
  isn't hammered; breaker-open → `remote_error` (retryable, §4).
- primary→fallback base-url like `DedupVisionClient` (optional).

## 9. `target_system` → adapter registry (ratify)

Introduce an adapter registry (the `CadConnectorRegistry` idiom) keyed by
`target_system`: the worker/routes resolve the adapter from the row's
`target_system` instead of hardcoding `NullErpPublicationAdapter`. R3 registers:
`null` (dev/test, default) + one generic-HTTP target configured via settings (§8).

**Single configured target via settings — no per-target config table, so R3 adds
NO migration** (the registry shape allows future multi-target; a per-target config
table is a later slice). This keeps the #672 `downgrade` note moot for R3.

## 10. Closing the deferred #672 notes

- **Unbounded revalidate-raise deferral**: R3 bounds it — a `build_publication_
  readiness` exception during the worker's revalidate is treated as a retryable
  failure that **consumes an attempt** (so it dead-letters at `max_attempts`
  rather than deferring forever); recommend a `revalidate_error` → `remote_error`
  classification, counted like a pre-send failure (guard #1).
- **`FOR UPDATE SKIP LOCKED` PG-only / at-least-once real**: mitigated by §5
  (on-wire idempotency); double-send is acknowledged-not-double-applied.
- **PG-oriented `downgrade`**: moot for R3 (no migration); if a later multi-target
  config table is added, its migration + downgrade must be SQLite-clean (guard #2).

## 11. Non-Goals

No `/publication/export`; no vendor SDK / no GPL-AGPL code; no new publication
semantics (R3 is transport); no change to the outbox state machine / reason set /
version-scoped idempotency key beyond the on-wire `Idempotency-Key` header; no
per-target config table (single settings-configured target in R3); no change to
the manual route / worker beyond resolving the adapter via the registry.

## 12. Preconditions to enter the connector IMPLEMENTATION

1. §2 adapter shape (`HttpErpPublicationAdapter`, validate-local) ratified;
2. §3 request contract (POST + headers + body) ratified;
3. §4 HTTP-status → reason mapping (esp. `4xx → validation_error`) ratified;
4. §5 on-wire idempotency (+ target-dedupe requirement) ratified;
5. §6 dry-run-no-side-effect (validate-local) ratified;
6. §7 GPL/AGPL boundary ratified;
7. §8 secrets/auth/resiliency ratified;
8. §9 registry + single-settings-target (no migration) ratified;
9. §10 closure of the three #672 notes ratified.

A **separate explicit opt-in** then authorizes the implementation.

## 13. Reviewer Focus

1. §0/§7 — generic HTTP, GPL/AGPL-clean (no vendor/odooplm code)?
2. §2/§6 — `validate_contract` local-only so dry-run stays side-effect-free?
3. §4 — `4xx → validation_error` (non-retryable) vs `5xx`/timeout → `remote_error`
   (retryable) — correct alignment with the locked retry rule?
4. §5 — `Idempotency-Key` on every POST + target-dedupe requirement (at-least-once
   safety)?
5. §8 — token from settings, never logged; CircuitBreaker?
6. §9 — registry-by-`target_system`, single settings target, **no migration**?
7. §10 — the three #672 notes closed?
8. §11 — `/publication/export` + vendor SDKs stay out?

## 14. Status

Doc-only scope-lock. Ready for review once the doc exists at the canonical path;
`DELIVERY_DOC_INDEX.md` references it + its DEV/verification record (sorted under
`## Development & Verification`); doc-index / sorting / completeness checks pass;
`git diff --check` clean. Ratifying §2–§10 sets the connector implementation
plan; **a separate explicit opt-in authorizes the implementation.**
`/publication/export` and any vendor-specific adapter remain later,
separately-opted slices.
