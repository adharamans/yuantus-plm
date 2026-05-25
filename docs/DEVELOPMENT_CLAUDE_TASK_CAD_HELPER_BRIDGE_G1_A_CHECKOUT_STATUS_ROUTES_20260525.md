# Claude Taskbook: CAD Helper Bridge G1-A — Document Lock Routes (status / checkout / undo-checkout)

Date: 2026-05-25

Type: **Doc-only taskbook.** Changes no runtime, no schema, no workflow, and
no helper / bridge / plugin / client code. It specifies the contract a later,
separately opted-in implementation PR will deliver. Merging this taskbook
does **NOT** authorize that implementation.

Naming: **"G1-A" is a proposal** for the first slice of the G1 program
(`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_G1_LAST_MILE_DESIGN_20260525.md`).
It does **not** claim the team has ratified the `G1-x` sub-series naming;
that ratification is a separate team step.

## 1. Purpose

G1-A is the **first, lowest-risk** slice of the G1 "last-mile" program. It
opens the **read / lock channel** of the in-CAD document loop by adding
**exactly three** helper routes that **proxy to already-existing backend
primitives**. It is deliberately the smallest increment that proves the
helper → backend "last-mile" main channel end-to-end.

G1-A is **helper-routes-only**: it adds **no** CAD-host command surface
(no LISP, no C# ribbon). Wiring a CAD-side command to these routes is a
**separate later slice**. Therefore G1-A is fully testable at the HTTP
level and needs **no real-CAD operational signoff**.

## 2. Grounded Current Reality

Grounded against `origin/main = c809c25b`.

### 2.1 Backend primitives reused **as-is** (no backend change in G1-A)

`src/yuantus/meta_engine/web/cad_checkin_router.py` (prefix `/cad`):

- `POST /cad/{item_id}/checkout` (`:129`) → `CheckinManager.checkout` →
  locks item; returns `{status, message, locked_by_id}`. Depends on
  `get_checkin_manager`, which **itself `Depends(get_current_user)`**
  (`:25`–`:30`) — so it is **auth-gated** (transitively).
- `POST /cad/{item_id}/undo-checkout` (`:157`) → unlock. Also via
  `get_checkin_manager` → **auth-gated** (transitively).
- `GET /cad/{item_id}/checkin-status` (`:233`) → `CadCheckinStatusResponse`,
  **auth-gated** via `get_current_user` directly.

**All three require PLM user context — there is no auth asymmetry.** (An
earlier draft wrongly read checkout/undo-checkout as unauthenticated; they
are gated through `get_checkin_manager` at `:25`.) **Out of G1-A scope:**
`POST /cad/{item_id}/checkin` (`:169`, multipart) → that is G1-B.

### 2.2 Helper current surface (10 routes)

Production helper Kestrel routes are exactly ten (per the G1 design doc
§2.2). There is **no** `/document/*` route today. Dedup is legacy-direct
(`/api/dedup/check`, not a helper route). The route-count guard lives at
`clients/autocad-material-sync/verify_material_sync_static.py:234`
(`helper.count("MapGet(") + helper.count("MapPost(") == 10`).

### 2.3 Session / auth today

Helper `POST /session/login` establishes a PLM session (JWT) held by the
helper. Existing business routes (`/diff/preview`, `/sync/*`) proxy to the
backend through `IPlmBusinessClient`, which already takes a `bearerToken`
(`HelperRuntime.cs:2133`) — so the session-bearer → backend
`get_current_user` propagation seam already exists. **Caveat:**
`IPlmBusinessClient` today exposes **only `PostAsync`** (`:2131`–`:2133`);
proxying the backend **GET** `checkin-status` requires the slice to add a
`GetAsync` (or generic `SendAsync`) forwarding method (see §3.B).

## 3. G1-A Scope & Boundaries

### 3.A Routes added — exactly three; helper route count **10 → 13**

| Helper route (proposed) | Proxies to backend primitive |
|---|---|
| `POST /document/checkout` (body `{item_id}`) | `POST /cad/{item_id}/checkout` (`:129`) |
| `POST /document/undo-checkout` (body `{item_id}`) | `POST /cad/{item_id}/undo-checkout` (`:157`) |
| `POST /document/status` (body `{item_id}`) | `GET /cad/{item_id}/checkin-status` (`:233`) |

All three are proposed as `POST` carrying `item_id` in a JSON body, for
consistency with the helper's existing POST-JSON business routes
(`/diff/preview`, `/sync/*`) and the S9 `(yuantus-helper-call …)` JSON
transport. (`/session/status` is a param-less `GET`; `/document/status`
needs `item_id`, hence POST-with-body. The implementing slice may choose
`GET …?item_id=` instead, but the route **count** is +3 either way.)

### 3.B Proxy mechanics

The helper handlers **shape the request and forward** to the backend via
`IPlmBusinessClient`; they map backend responses/errors back to the helper's
response envelope. **No business logic** beyond request shaping + error
mapping; no caching, no retries beyond the existing transport's behavior.
Because the client is **POST-only today** (`HelperRuntime.cs:2133`), the
slice **must add a GET (or generic `SendAsync`) forwarding seam** so
`/document/status` proxies the backend **GET** `checkin-status` without body
or verb drift. `checkout` / `undo-checkout` map to backend POSTs and reuse
the existing `PostAsync`.

### 3.C JWT / tenant propagation

All three backend primitives are auth-gated (§2.1), so the helper must
forward the PLM session bearer + tenant context (from `/session/login`
state) to the backend via the `bearerToken` seam for **all three** routes,
and a **missing/expired session must short-circuit in the helper without
calling the backend** (see §4). **Quota note:** lock / unlock / status are
**not** quota-consuming (no file is created), so G1-A introduces **no quota
logic**, and it does **not** change the existing helper response contract:
the helper always returns its own `200 + ResponseEnvelope`
(`HelperRuntime.cs:1333`) and folds the backend result into
`PlmBusinessResponse` (`:2151`–`:2181`), so backend headers/status are
**not** surfaced and must **not** be passed through. G1-A only requires
session-bearer propagation plus the **existing** error mapping (backend
`401/403` → `AuthPlmNotLoggedIn`, other non-2xx → `PlmValidationFailed`).
Quota propagation becomes material only in **G1-B** (checkin / multipart).

### 3.D Hard boundaries (non-goals restated as guardrails)

G1-A must **not**:

- add `/document/checkin` or any multipart handling (→ G1-B);
- touch BOM (`/bom/upload`, `import_bom`, `/cad/import`) (→ later track);
- introduce any CAD entity write or DWG mutation, and must **not** relax the
  S10 `(entmake` / `(entmod` prohibition;
- add a CAD-host command (LISP / C#) — G1-A is helper-routes-only;
- modify any **backend** route (`cad_checkin_router` is consumed unchanged);
- change the S1–S11 transport, session, audit, or security-gate contracts
  beyond additively registering the three new routes;
- alter dedup (stays legacy-direct).

### 3.E Route-count contract: 10 → 13

The implementation slice updates
`clients/autocad-material-sync/verify_material_sync_static.py:234`
(`== 10` → `== 13`) and any `Helper.Tests` route-set guards: add the three
new routes to the expected-route set; **keep** the existing
`Assert.DoesNotContain("/dedup/check", …)` guard.

## 4. AUTH SEMANTICS — STANDALONE DECISION POINT (ratify before implementing)

Corrected premise: **all three backend primitives require PLM user
context** (§2.1) — `checkout` / `undo-checkout` transitively via
`get_checkin_manager` at `:25` (`Depends(get_current_user)`),
`checkin-status` directly. There is **no backend auth gap** for G1-A to
inherit or fix.

The G1-A decision is therefore simpler and **uniform**:

- The helper **requires an active PLM session for all three routes** and
  forwards the session bearer to the backend (the `IPlmBusinessClient`
  `bearerToken` seam, `HelperRuntime.cs:2133`).
- A **missing or expired session must short-circuit in the helper** and
  return an auth error **without calling the backend** — pinned by a test
  (§6).
- G1-A relies on the backend's existing `get_current_user` gate; it does
  **not** add, remove, or alter any backend auth dependency.

Decision to ratify: confirm "helper requires session for all three +
short-circuit on missing session" is the intended posture (recommended). A
guard test may additionally pin the **transitive** dependency
(`get_checkin_manager` → `get_current_user`) so a future backend refactor
that drops it fails loudly rather than silently de-authing lock / unlock.

## 5. R1 Target Output

Implementation PR should contain:

- helper handlers for the three routes (§3.A) with session-bearer/tenant
  propagation (§3.C), the §4 uniform auth posture, **and the GET-forwarding
  seam (§3.B)**;
- updated route-count guard (`verify_material_sync_static.py` `== 13`) and
  `Helper.Tests` contract tests for the three routes + count + auth +
  GET-forwarding;
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_G1_A_CHECKOUT_STATUS_ROUTES_R1_20260525.md`;
- one `docs/DELIVERY_DOC_INDEX.md` line for the DEV/Verification doc.

Implementation PR must **not** contain:

- `/document/checkin` or any multipart;
- BOM / `import_bom` / `/cad/import` changes;
- CAD entity write or DWG mutation, or any relaxation of the S10 guard;
- a CAD-host command (LISP/C#);
- backend route changes;
- new transport egress or new `ErrorCodes` families;
- dedup changes.

## 6. Mandatory Tests And Guards (named + assertion shape)

The implementation PR must add the tests below. Names are **canonical —
adopt verbatim**; each entry gives the **assertion shape, not full code**.
Layer key: **[xUnit]** = `Helper.Tests` behavior test driving the handler
with a **fake/stub `IPlmBusinessClient` that records calls** (so "did/did
not call backend", "which verb", "which path", "bearer present" are all
assertable); **[static]** = Python source verifier mirroring
`verify_material_sync_static.py`. ([xUnit] methods may use the PascalCase
equivalent of the snake_case name; the name identity + assertion shape is
what is canonical.)

### 6.A Uniform auth / missing-session short-circuit (highest-risk boundary)

- **`test_g1a_document_routes_require_plm_session_before_backend_call`** [xUnit]
  - *Given* the helper has **no active PLM session** (missing/expired bearer);
    *when* each of `POST /document/checkout`, `/document/undo-checkout`,
    `/document/status` is called;
  - *then* each returns a **helper-side auth error** (reuse existing
    `ErrorCodes.AuthPlmNotLoggedIn` — no new code) **and the stub client
    records ZERO backend calls** for all three (assert forward-count == 0).
    The short-circuit — not the error code — is the point.

### 6.B Forwarding shape (verb / path / bearer / body)

- **`test_g1a_checkout_forwards_post_to_cad_checkout_with_bearer`** [xUnit]
  - *Given* an active session; *when* `POST /document/checkout {item_id}`;
    *then* the client is invoked **once** with verb **POST**, endpoint
    **`/cad/{item_id}/checkout`**, the **session bearer forwarded**; the
    backend response is mapped into the helper envelope.
- **`test_g1a_undo_checkout_forwards_post_to_cad_undo_checkout_with_bearer`** [xUnit]
  - Same shape → verb **POST**, endpoint **`/cad/{item_id}/undo-checkout`**,
    bearer forwarded.
- **`test_g1a_status_forwards_get_to_cad_checkin_status_without_body`** [xUnit]
  - *Given* an active session; *when* `POST /document/status {item_id}`;
    *then* the client is invoked via the **new GET/`SendAsync` seam** with
    verb **GET**, endpoint **`/cad/{item_id}/checkin-status`**, **no request
    body**, bearer forwarded. Explicitly assert it is **not** `PostAsync` and
    carries **no JSON body** (guards against hard-casting the POST-only path).

### 6.C Static guards (route surface + scope boundaries)

- **`test_g1a_static_guard_counts_routes_at_thirteen_and_keeps_dedup_out`** [static]
  - Assert `MapGet(` + `MapPost(` count **== 13**; assert the three
    `/document/*` routes are registered; assert **`/dedup/check` absent**
    (`DoesNotContain`) — dedup stays legacy-direct.
- **`test_g1a_handlers_have_no_multipart_or_uploadfile`** [static]
  - None of the three handlers accept multipart / file upload (that is G1-B).
- **`test_g1a_handlers_do_not_reference_bom_or_cad_import`** [static]
  - No `import_bom` / `/cad/import` / `/bom` reference in the G1-A surface.
- **`test_g1a_introduces_no_dwg_entity_mutation_token`** [static]
  - No `(entmake` / `(entmod` / … token introduced (the S10 guard holds).
- **`test_g1a_backend_cad_checkin_router_unmodified`** [static/diff]
  - The PR does not modify
    `src/yuantus/meta_engine/web/cad_checkin_router.py`.

### 6.D Optional backend drift guard (recommended, per §4)

- **`test_g1a_checkin_manager_dependency_pins_get_current_user`** [pytest]
  - Pin that `get_checkin_manager` still `Depends(get_current_user)`
    (`cad_checkin_router.py:25`) so a future backend refactor that drops it
    fails loudly instead of silently de-authing lock / unlock.

## 7. Verification Plan

Doc-contract checks (this taskbook PR and the later implementation PR):

```bash
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py

git diff --check
```

The implementation PR additionally runs the helper static verifier and the
`Helper.Tests` dotnet suite. **No real-CAD signoff is needed for G1-A** —
these are HTTP-level helper routes with no CAD-host command surface.

## 8. Explicit Non-Goals

G1-A does NOT: handle checkin/multipart (G1-B); touch BOM; add a CAD-host
command; write CAD entities or relax the S10 guard; change backend routes
or backend auth dependencies; alter dedup; add error-code families; change
schema/workflow/tenant data; authorize any implementation.

## 9. Recommended Branch (after a separate opt-in)

Do **not** start implementation from this taskbook PR. After the team
ratifies the `G1-x` naming and opts in to G1-A, use:

```text
feat/cad-helper-bridge-g1-a-document-lock-routes-r1-20260525
```

## 10. Reviewer Focus

1. Confirm scope is exactly the three lock/status routes; count 10 → 13.
2. Confirm §4 uniform auth posture (session required for all three;
   short-circuit on missing session) and that no backend auth dependency is
   changed.
3. Confirm no multipart / no BOM / no CAD-host command / no entity write.
4. Confirm backend `cad_checkin_router` is reused unchanged.
5. Confirm dedup stays legacy-direct and the route-count guard moves to 13.

## 11. Status

This taskbook is ready for review once:

- the doc exists at the canonical path;
- `docs/DELIVERY_DOC_INDEX.md` references it (sorted position);
- doc-index / R2 / Tier-B drift checks pass;
- `git diff --check` is clean.
