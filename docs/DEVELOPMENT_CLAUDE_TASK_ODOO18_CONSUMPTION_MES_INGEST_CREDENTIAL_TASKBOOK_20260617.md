# Claude Taskbook: MES Ingest **Credential / Auth Boundary** (Consumption R2.2)

Date: 2026-06-17
Status: **DECISION — doc-only, pending gate review + a separate build opt-in**
Follows R2 (#778) + R2.1 uom (#779). Secures **who** may call the MES ingest route, now that
**what** it writes (idempotency, uom) is stable.

## 0. Why this exists / scope

The MES ingest route `POST /api/v1/consumption/plans/{plan_id}/mes-actuals` is currently
authenticated as a normal user (`Depends(get_current_user)`). It is a **machine** entrypoint;
it should be gated by a **dedicated MES credential**, bound to a **fixed tenant**, and must not
be reachable as an ordinary user nor have its tenant chosen by the caller. This slice is narrow:
**only** that one route. It does **not** touch manual `/actuals`, does **not** add a worker,
and does **not** change idempotency/uom/variance semantics.

Mechanism choice (ratified): **B — dedicated inbound header credential** (not A entitlement-gate,
not C role-only). Two owner tightenings are load-bearing and locked below (D1, D3).

## 1. Grounded baseline (read before implementing — file:line)

- **Global auth**: `AuthEnforcementMiddleware` (`api/middleware/auth_enforce.py:77`) enforces JWT
  when `AUTH_MODE=required`, except `_is_public_path` (`:40-59`, an exact/prefix whitelist) and
  `OPTIONS`. It runs **first** (outermost; `api/app.py:293-296`).
- **Tenant context**: `TenantOrgContextMiddleware` (`api/middleware/context.py:12-27`) runs
  **second** and sets `tenant_id_var`/`org_id_var` **from request headers**
  (`settings.TENANT_HEADER`=`x-tenant-id`, `ORG_HEADER`=`x-org-id`) — but only `if
  tenant_id_var.get() is None`. For a JWT request AuthEnforcement already set it; **for a
  whitelisted path AuthEnforcement is skipped, so tenant comes from the header.**
- **Isolation = schema-per-tenant**: `tenant_id_var` → `tenant_id_to_schema()`
  (`database.py:46-73`) → `SET LOCAL search_path TO "<schema>", public` via an `after_begin`
  listener in `get_db` (`database.py:265-280`). Also db-per-tenant / single modes.
- **`ConsumptionPlan` / `ConsumptionRecord` have NO tenant_id column**
  (`models/parallel_tasks.py:134-166`); `ConsumptionPlanService` does **not** filter by tenant.
  Isolation is **100% schema-scoping** — so a `plan_id` from another tenant is simply *not found*
  **iff** the session's tenant context was pinned before the query.
- **Pinning a fixed tenant**: set `tenant_id_var`/`org_id_var` (`yuantus.context`) **before** the
  first DB access; reset in `finally`. Pattern used by `cli.py:103-106`, `job_worker.py:147-151`.
  There is **no** built-in helper. `get_db_session()` (context manager) is the worker's session
  factory.
- **Secrets**: settings use `Field(default="", description="…(never logged)")` (e.g.
  `PUBLICATION_ECM_TRANSFER_SECRET:587`); `env_prefix="YUANTUS_"` (`:11`); constant-time compare
  via `secrets.compare_digest` is an established precedent (`security/auth/passwords.py`,
  `security/auth/jwt.py`).

## 2. Locked decisions

- **D1 — Auth FORM (owner tightening #1): whitelist + SOLE credential, not stacked.** The MES
  credential is a **machine entrypoint that replaces the user session**, not a check added after
  `get_current_user`. Implementation: add the mes-actuals path to the JWT-enforcement whitelist
  via a **path predicate** (the path has a `{plan_id}` segment, so a literal set won't match — add
  `_is_mes_ingest_path(path)` matching `^/api/v1/consumption/plans/[^/]+/mes-actuals/?$`) and
  return early from `AuthEnforcementMiddleware` for it. The route's **only** auth is a new
  dependency `require_mes_ingest_credential` (replacing both `get_current_user` and `get_db` on
  that route). **Risk acknowledged**: whitelisting removes the global JWT safety net, so the
  dependency must be **airtight + fail-closed** — proven by tests that an unconfigured / no-cred /
  wrong-cred request never reaches the handler.

- **D2 — Credential mechanism.** Two custom headers `X-MES-Ingest-User` / `X-MES-Ingest-Secret`
  compared (constant-time, `secrets.compare_digest`) against new settings. Secret/user **never
  logged** (not in audit, errors, or the 401 body).

- **D3 — Tenant binding (owner tightening #2): fixed config tenant, NEVER the request header.**
  New settings bind the credential to one tenant/org. The dependency **sets `tenant_id_var` /
  `org_id_var` from config**, unconditionally overriding any header-derived value, **before the
  session is created**. CRITICAL: it must NOT take the session via `Depends(get_db)` as a
  sub-dependency (FastAPI resolves sub-deps first → the session would bind the header tenant);
  instead set the contextvars, then open `get_db_session()` inside the dependency body, `yield`,
  reset in `finally`. The path `plan_id` is then queried within the bound tenant's schema, so a
  cross-tenant `plan_id` is structurally **not found → 404** (no explicit tenant column needed).

- **D4 — Settings (4, `YUANTUS_`-prefixed, fail-closed).**
  `MES_INGEST_USER`, `MES_INGEST_SECRET`, `MES_INGEST_TENANT_ID`, `MES_INGEST_ORG_ID`
  (all `str = Field(default="")`; SECRET + USER described `…(never logged)`). **Fail-closed**: if
  `MES_INGEST_SECRET` is empty **or** `MES_INGEST_TENANT_ID` is empty → the route is **disabled**
  (`503`), before reading headers. (`get_settings()` is `lru_cache`d → config is restart-only,
  like `ECM_PUBLISH_ENABLED`.)

- **D5 — Gate order (all before any write / any plan read).** In the dependency:
  (1) fail-closed config check → `503`; (2) constant-time user+secret compare → `401` on
  missing/mismatch; (3) pin `tenant_id_var`/`org_id_var` from config; (4) `yield` the
  tenant-scoped session. Only then does the route body run (plan load → 404 if not in tenant; uom
  → 422; ingest → 200/409/…), unchanged from R2/R2.1.

- **D6 — Scope.** ONLY mes-actuals. Manual `/actuals` keeps `get_current_user` (untouched). No
  route added (count stays **713**), no migration, no change to idempotency/uom/variance. The
  credential dependency **replaces** `get_current_user` + `get_db` on the mes-actuals route only.

- **D7 — Entitlement is OPTIONAL, deferred.** v1 = dedicated secret + fixed-tenant pin (sufficient
  for the boundary). Layering `is_entitled("mes_ingest")` as defense-in-depth is a noted, separate
  follow-up — not v1 (keeps the slice thin).

## 3. Verification plan (`test_consumption_mes_ingest_credential.py`)

- **fail-closed**: unset secret OR unset tenant → `503`, handler never runs.
- **auth**: missing headers → `401`; wrong secret (correct user) → `401`; wrong user (correct
  secret) → `401`; correct user+secret → `200` and ingest works.
- **no user-session bypass**: a normal JWT/admin user without MES headers → `401` (the route is
  machine-only now).
- **tenant pinning**: with valid creds, `tenant_id_var` is the **config** tenant even when the
  request sends a *different* `x-tenant-id` header (header is ignored).
- **secret hygiene**: the secret never appears in the response/error body.
- **R2/R2.1 unchanged**: with valid creds, CREATED/DUPLICATE/CONFLICT, variance-counts-once, uom
  422 all still hold; manual `/actuals` still uses user auth.
- **TEST-ENV CAVEAT (must state honestly)**: cross-tenant **schema** isolation is a Postgres
  runtime property; the SQLite test path (`single` mode) does **not** switch schemas, so a unit
  test cannot prove "tenant-B plan_id → 404" on SQLite. Tests assert the *mechanism* (contextvar
  pinned from config not header; dependency wiring; fail-closed/auth codes); the schema-isolation
  guarantee is documented as structural (D3) and belongs to a Postgres integration check, not a
  green SQLite unit test. Do not claim SQLite proves cross-tenant isolation.

## 4. TODO (ordered, for the build PR after opt-in)

- [ ] Settings: 4 `MES_INGEST_*` fields (`config/settings.py`).
- [ ] `api/dependencies/mes_ingest_auth.py`: `require_mes_ingest_credential` — fail-closed → 503;
      constant-time compare → 401; pin tenant/org from config; open `get_db_session()`; yield; reset.
- [ ] `auth_enforce.py`: `_is_mes_ingest_path` predicate + early-return (whitelist from JWT).
- [ ] Route: replace `get_current_user` + `get_db` on mes-actuals with the new dependency.
- [ ] Tests (§3) + dual-register in `ci.yml` (sorted) + `conftest._ALLOWLIST_NO_DB`.
- [ ] DEV/V doc + `DELIVERY_DOC_INDEX.md` (sorted). No route-count/pin/owner-contract change.

## 5. Open questions to ratify (my recommendation in **bold**)

- **OQ1** auth form: **whitelist + sole credential (D1)** vs "session-admin OR credential" dual-path.
- **OQ2** codes: **`401` bad/missing cred, `503` unconfigured** vs `403`.
- **OQ3** header style: **two headers `X-MES-Ingest-User`/`Secret`** vs a single bearer-style token.
- **OQ4** org: **`MES_INGEST_ORG_ID` optional** (tenant required, org set only if needed) vs both required.
- **OQ5** entitlement: **defer (v1 = secret + tenant-pin)** vs require `is_entitled("mes_ingest")` now.

## 6. Boundary

Machine-auth + fixed-tenant binding on one route only. No worker, no new route/migration, no
semantic change to ingest. `source_type` widening, unit conversion, MES outbox/worker, and a
multi-credential registry remain separate, later, explicitly-opted slices.
