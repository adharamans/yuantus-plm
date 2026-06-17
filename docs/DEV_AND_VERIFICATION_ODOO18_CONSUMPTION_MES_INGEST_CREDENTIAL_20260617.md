# Dev & Verification: MES Ingest **Credential / Auth Boundary** (Consumption R2.2)

Date: 2026-06-17
Status: **IMPLEMENTED** — pending gate review + merge
Taskbook: `docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_CONSUMPTION_MES_INGEST_CREDENTIAL_TASKBOOK_20260617.md` (#781)
Follows R2 (#778) + R2.1 uom (#779).

## 1. Summary

The MES ingest route is now a **machine entrypoint** authenticated by a dedicated credential
bound to a **fixed tenant from config**, not a user session. It is whitelisted from the global
JWT and gated solely by `require_mes_ingest_credential` (fail-closed). Manual `/actuals`,
idempotency, uom, and variance semantics are unchanged; no route/migration/pin change.

## 2. OQ resolutions (as ratified)

OQ1 **whitelist + sole credential** (machine-only, replaces `get_current_user`) · OQ2
**`503` unconfigured / `401` bad-or-missing cred** (not 403) · OQ3 **two headers**
`X-MES-Ingest-User` / `X-MES-Ingest-Secret` · OQ4 **`MES_INGEST_ORG_ID` optional**
(tenant required) · OQ5 **entitlement deferred** (v1 = secret + tenant-pin).

## 3. Design (as built)

- **Settings** (`config/settings.py`, `YUANTUS_`-prefixed, restart-only): `MES_INGEST_USER`,
  `MES_INGEST_SECRET`, `MES_INGEST_TENANT_ID`, `MES_INGEST_ORG_ID` (USER/SECRET never logged).
- **Whitelist** (`auth_enforce.py`, watch-point 1): `_is_mes_ingest_path` matches **only**
  `^/api/v1/consumption/plans/[^/]+/mes-actuals/?$` (one plan_id segment — **not** broadened to
  `/consumption/*`); the middleware early-returns it from JWT enforcement.
- **Credential dependency** (`api/dependencies/mes_ingest_auth.py`), gate order:
  (1) **fail-closed** — empty `SECRET` *or* `TENANT_ID` → `503` (before any header read);
  (2) **auth** — `secrets.compare_digest` on both header halves → `401` on miss/mismatch;
  secret never logged/echoed; (3) **tenant bind** — `tenant_id_var`/`org_id_var` from **config,
  never the `x-tenant-id` header**.
- **Route**: `Depends(require_mes_ingest_credential)` replaces `get_current_user` + `get_db` on
  mes-actuals only.

### 3.1 Tenant pinning + the contextvar/threadpool nuance (watch-point 2)
`get_db_session()` reads `tenant_id_var` **at session-creation time** to bake the schema into a
per-transaction `SET LOCAL search_path`. FastAPI runs a sync-generator dependency's setup and
teardown in **different threadpool contexts**, so a contextvar `Token` must **not** be held
across the `yield` (`reset()` would raise). The dependency therefore sets the contextvars, opens
the session, and resets — **all synchronously before the yield, in one context**. The session
keeps the bound-tenant schema for its whole life regardless of the contextvar; isolation is on
the **session**, not a request-scoped global. Isolation = **schema-per-tenant** + no tenant_id
column, so a cross-tenant `plan_id` is structurally not-found. The data isolation was confirmed
by adversarial review (the `after_begin` schema closure never re-reads the contextvar; the
handler/service use only the injected session; no fresh session is opened).

**Known limitation (attribution-only, deferred):** because the whitelisted path skips
`AuthEnforcementMiddleware`, `TenantOrgContextMiddleware` sets the request-context
`tenant_id_var` from the `x-tenant-id` header, and the dependency's config-tenant pin lives in a
separate threadpool context — so the `AuditLogMiddleware` row records the *request header's*
tenant, not `MES_INGEST_TENANT_ID`. This writes to the **identity DB (audit table), not any
tenant schema**, so it is **not** a data-isolation breach (verified) — only a forensics
attribution inaccuracy: a caller can put an arbitrary string in the audit `tenant_id`. A precise
fix is middleware-level (pin the MES tenant in the request context) and is a deliberate
**follow-up**, kept out of this narrow slice. The DATA path is correct and unaffected.

### 3.2 No existence-probing (watch-point 3)
`503`/`401` are raised by the dependency **before** the handler runs — no plan read, no write —
so an unauthorized caller gets the same `401` for a real vs a non-existent `plan_id`.

## 4. Verification

- **`test_consumption_mes_ingest_credential.py` (11)**: 503 (no secret / no tenant); 401
  (missing headers / wrong secret / wrong user / bearer-not-bypassed); 200 valid → CREATED;
  bad-cred same 401 real-vs-fake plan (no existence leak); secret never echoed; **dependency
  unit**: tenant pinned to config **at session creation** (spy) and a pre-set spoofed
  `x-tenant-id` is overridden by config, contextvar restored after (no leak).
- **R2/R2.1 unchanged**: `test_consumption_mes_ingestion_runtime.py` (23) green — its `client`
  fixture now overrides `require_mes_ingest_credential` to yield the in-memory session (the
  behavior tests are not testing auth; auth is the credential file's job).
- **Infra**: route count **713** (no route added — only the route's deps changed); CI list order
  + owner contract + config-variants + 18 auth/middleware tests green; the new test is
  dual-registered (`ci.yml` sorted + `conftest._ALLOWLIST_NO_DB`).
- **HONEST TEST CAVEAT**: cross-tenant **schema** isolation is a Postgres runtime property;
  SQLite single-mode does not switch schemas, so no unit test proves "tenant-B plan_id → 404".
  Tests assert the *mechanism* (config-pinned tenant not header, set-before-session, fail-closed,
  auth codes); schema isolation is documented as structural (§3.1) — **not** claimed as proven by
  the SQLite suite.

### 4.1 Adversarial security verify (7 lenses → refute)
A 7-lens security review (auth-bypass, fail-closed, tenant-isolation, secret-timing,
contextvar/CM, existence-probing, regression), each finding independently refuted, returned
**0 must_fix** and confirmed the W2 data isolation correct (session schema baked in; header can't
win; no cross-request leak) and W3 (auth before any plan read). One **should_fix** fixed:
`secrets.compare_digest(str, str)` raises `TypeError` on a non-ASCII (attacker-controlled) header
→ an unhandled 500 instead of the documented clean 401 (not a bypass/leak/DoS; the verifier
confirmed end-to-end via a raw socket). Fixed by comparing on UTF-8 **bytes** (content-safe +
constant-time); regression `test_non_ascii_credential_is_clean_reject_not_typeerror`. One nit
(audit attribution) is documented as a deferred limitation (§3.1). Two issues caught during the
build (contextvar Token across the threadpool; commit-on-error in the manual CM teardown) were
fixed before this review.

## 5. Boundary / files

Machine-auth + fixed-tenant on one route only. No worker, no new route/migration/pin, no
idempotency/uom/variance change. `source_type` widening, unit conversion, MES outbox/worker, and
a multi-credential registry remain separate, later, opted slices.

Files: `config/settings.py` (4 settings) · `api/dependencies/mes_ingest_auth.py` (new) ·
`api/middleware/auth_enforce.py` (whitelist predicate) ·
`web/parallel_tasks_consumption_router.py` (route auth swap) ·
`tests/test_consumption_mes_ingest_credential.py` (new) ·
`tests/test_consumption_mes_ingestion_runtime.py` (fixture override) · `ci.yml` + `conftest.py`
(register) · `docs/DELIVERY_DOC_INDEX.md` (this doc).
