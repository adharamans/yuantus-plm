# CAD Helper Bridge G1-A — Document Lock Routes R1 (Development And Verification)

Date: 2026-05-25

Implements the taskbook
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_G1_A_CHECKOUT_STATUS_ROUTES_20260525.md`
(merged at `f5944c4f`). Grounded against `origin/main = f5944c4f`.

## 1. Scope Delivered

Three helper routes that **proxy existing backend primitives**; helper route
count **10 → 13**:

| Helper route | Backend primitive (reused unchanged) |
|---|---|
| `POST /document/checkout` (body `{item_id}`) | `POST /cad/{item_id}/checkout` (`cad_checkin_router.py:129`) |
| `POST /document/undo-checkout` (body `{item_id}`) | `POST /cad/{item_id}/undo-checkout` (`:157`) |
| `POST /document/status` (body `{item_id}`) | `GET /cad/{item_id}/checkin-status` (`:233`) |

- New **GET forwarding seam**: `IPlmBusinessClient.GetAsync` (interface +
  `HttpPlmBusinessClient.GetAsync`), mirroring `PostAsync` minus the body, so
  `/document/status` forwards a backend **GET** with no request body.
- Three new service methods on `HelperBusinessAuditService`:
  `DocumentCheckoutAsync`, `DocumentUndoCheckoutAsync`, `DocumentStatusAsync`
  (checkout/undo share `ProxyDocumentLockAsync`).
- Item-scoped backend paths are built with `Uri.EscapeDataString(itemId)`.

## 2. Auth Posture (taskbook §4 — uniform, backend unchanged)

All three methods call the existing `TryReadSession` gate **before** any
backend call. A missing/expired PLM session short-circuits with
`ErrorCodes.AuthPlmNotLoggedIn` and makes **zero** backend calls. The session
bearer is forwarded to the backend via the existing `bearerToken` seam. No
backend route or backend auth dependency was changed; G1-A relies on the
backend's existing `get_current_user` gate (transitive via
`get_checkin_manager`, pinned by the §6.D drift guard).

## 3. No Audit (deliberate scope decision, taskbook §3.B)

G1-A is a **pure proxy** (request shaping + the existing error mapping only).
It does **not** write audit rows for lock/unlock/status and does **not**
change the existing helper response contract (the helper still returns its own
`200 + ResponseEnvelope` and folds backend results into `PlmBusinessResponse`;
backend headers/status are not surfaced). A lock-audit vocabulary, if desired,
is a separate follow-up taskbook — intentionally out of G1-A scope.

## 4. Files Changed

- `clients/cad-desktop-helper/Helper/HelperRuntime.cs` — `IPlmBusinessClient.GetAsync`
  (+ `HttpPlmBusinessClient.GetAsync`); 3 service methods + `ProxyDocumentLockAsync`;
  3 route registrations.
- `clients/cad-desktop-helper/Helper.Tests/G1ADocumentLockRoutesContractTests.cs` — new
  xUnit tests + a per-file `RecordingDocumentClient` fake.
- `clients/cad-desktop-helper/Helper.Tests/HelperBusinessAuditContractTests.cs` — added
  `GetAsync` to the existing `RecordingBusinessClient` (compile requirement of the
  interface change; existing tests never invoke GET); route-count expectation updated
  to 13 and the three `/document/*` routes asserted.
- `clients/cad-desktop-helper/Helper.Tests/HelperSessionRoutesContractTests.cs`,
  `clients/cad-desktop-helper/Helper.Tests/HelperResetLocalTokenContractTests.cs` —
  route-count expectations updated to 13 and the three `/document/*` routes asserted.
- `clients/cad-desktop-helper/Bridge.Tests/BridgeContractTests.cs` — bridge still
  declares no helper routes; helper route-count expectation updated to 13 and the
  three `/document/*` routes asserted.
- `clients/autocad-material-sync/CADDedupPlugin.Client.Tests/MaterialSyncClientS8ContractTests.cs`
  — material-sync client still keeps `/dedup/check` absent; helper route-count
  expectation updated to 13 and the three `/document/*` routes asserted.
- `clients/cad-desktop-helper/verify_bridge_static.py`,
  `clients/cad-desktop-helper/verify_lisp_shell_static.py`,
  `clients/autocad-material-sync/verify_material_sync_static.py` — route-count guard
  `10 → 13`; material-sync verifier also asserts the three `/document/*` routes
  present and keeps `/dedup/check` absent.
- `src/yuantus/meta_engine/tests/test_g1a_checkin_manager_auth_dependency.py` — new
  §6.D backend drift guard.
- `docs/` — this DEV doc + `DELIVERY_DOC_INDEX.md` line.

## 5. Mandatory Tests (taskbook §6)

xUnit (`Helper.Tests`, net6.0-windows; **added, not locally executed** — see §6):

- `test_g1a_document_routes_require_plm_session_before_backend_call` (6.A — zero
  backend calls on missing session, `AuthPlmNotLoggedIn` for all three).
- `test_g1a_checkout_forwards_post_to_cad_checkout_with_bearer` (6.B).
- `test_g1a_undo_checkout_forwards_post_to_cad_undo_checkout_with_bearer` (6.B).
- `test_g1a_status_forwards_get_to_cad_checkin_status_without_body` (6.B — GET verb,
  no body).
- `test_g1a_document_routes_require_item_id` (input validation; bonus).

Static (Python, **run locally**):

- `test_g1a_static_guard_counts_routes_at_thirteen_and_keeps_dedup_out` — realized in
  `verify_material_sync_static.py` (count == 13 + three `/document/*` present +
  `/dedup/check` absent); route-count guards in `verify_bridge_static.py` and
  `verify_lisp_shell_static.py` also moved to 13.

pytest (**run locally**):

- `test_g1a_checkin_manager_dependency_pins_get_current_user` (6.D backend drift guard).

## 6. Verification Scope

**Run locally on this workstation (macOS, no .NET):**

- `python3 clients/cad-desktop-helper/verify_bridge_static.py` → `All 10 S9 bridge static guards passed` (route count now 13).
- `python3 clients/cad-desktop-helper/verify_lisp_shell_static.py` → `All 20 S10 Lisp shell static guards passed` (route count now 13).
- `python3 clients/autocad-material-sync/verify_material_sync_static.py` → `OK` (count 13 + `/document/*` present + dedup absent).
- `python3 -m pytest .../test_g1a_checkin_manager_auth_dependency.py` → `1 passed`.
- doc-contract pytests (index references / completeness / sorting / R2 / Tier-B) → `32 passed`.
- `git diff --check` → clean.

**Deferred to Windows CI** (cannot run on macOS; helper targets `net46`/`net6.0-windows`):

- `dotnet build` of `Helper` (`net46;net6.0-windows`) and `Helper.Tests` (`net6.0-windows`).
- The §6.A / §6.B xUnit tests listed in §5 (added but not executed here).

This mirrors the deferred-signoff pattern from S7/S8/S9: the Windows-only build
and xUnit execution are an honest carry-forward to CI, not claimed as locally
verified.

## 7. Explicit Non-Goals (unchanged from taskbook §8)

No checkin/multipart (→ G1-B); no BOM; no CAD-host command (helper-routes-only);
no CAD entity write / no relaxation of the S10 `(entmake`/`(entmod` guard; no
backend route or backend auth changes; no dedup changes; no audit; no new
`ErrorCodes`.
