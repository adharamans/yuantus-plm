# Claude Taskbook: CAD Helper Bridge G1-B — Document Checkin (multipart upload bridge)

Date: 2026-05-26

Type: **Doc-only taskbook.** Changes no runtime, no schema, no workflow, and
no helper / bridge / plugin / client code. It specifies the contract a later,
separately opted-in implementation PR will deliver. Merging this taskbook
does **NOT** authorize that implementation.

Naming: **"G1-B" is a proposal** for the second slice of the G1 program. It
does **not** claim team ratification of the `G1-x` sub-series.

## 1. Purpose

G1-B is the **write half** of the in-CAD document loop, after G1-A's
read/lock half (`/document/checkout|undo-checkout|status`, merged at
`2c471aac` / #646). It adds **one** helper route, `POST /document/checkin`,
that uploads the user's **already-saved** CAD file (the multipart body) to the
existing backend checkin primitive and maps the response into the helper
envelope. Helper route count **13 → 14**.

G1-B is **higher risk than G1-A** for two reasons that this taskbook locks
down: (a) it introduces a **multipart file-upload forwarding seam** (the
existing `IPlmBusinessClient` only does JSON `PostAsync` and `GetAsync`), and
(b) the backend checkin is **quota-enforced**, so quota semantics must be
surfaced without breaking the helper's fixed-200 envelope contract.

G1-B remains **helper-routes-only** (no CAD-host command; that is a later
slice) and is fully testable at the HTTP/service level — no real-CAD signoff.

## 2. Grounded Current Reality

Grounded against `origin/main = 2c471aac`.

### 2.1 Backend checkin primitive (reused as-is; no backend change)

`POST /cad/{item_id}/checkin` (`src/yuantus/meta_engine/web/cad_checkin_router.py:169`):

- **multipart**: `file: UploadFile = File(...)` (`:174`);
- **auth-gated** (`get_current_user`) and **quota-enforced**
  (`QuotaService.evaluate`, `:186`):
  - soft over-limit → `200` + response header `X-Quota-Warning` (`:192`);
  - hard over-limit → **`429`** + FastAPI body
    `{"detail": {"code": "QUOTA_EXCEEDED", ...}}` (`:195`–`:198`);
- success → `CadCheckinResponse` (`version_id`, `generation`, `file_id`,
  `conversion_job_ids`, `status_url`, `file_status_url`) and triggers CAD
  conversion jobs.

### 2.2 Helper current surface (14 after G1-B; 13 today)

- 13 routes after G1-A (incl. `/document/checkout|undo-checkout|status`).
- `IPlmBusinessClient` (`HelperRuntime.cs:2131`) exposes **`PostAsync`** (JSON,
  `:2133`) and **`GetAsync`** (`:2143`). **No multipart seam.**
- Response contract (G1-A-ratified): helper always returns its own
  `200 + ResponseEnvelope` (`:1333`) and folds backend results into
  `PlmBusinessResponse`; **non-2xx is folded to `PlmValidationFailed`**
  (`:2188` / `:2218`) and **backend headers are not surfaced**. This is why
  quota needs an explicit decision (§4.C) — by default a `429` would collapse
  into a generic `PLM_VALIDATION_FAILED` and `X-Quota-Warning` would be lost.
- G1-A precedent reused: the `TryReadSession` gate (uniform PLM session,
  short-circuit before any backend call) and the `/document/*` route pattern.

## 3. G1-B Scope

- **One** new helper route: `POST /document/checkin`. Route count **13 → 14**.
- Uploads the **already-saved** file via multipart; proxies to backend
  `POST /cad/{item_id}/checkin`; maps the response (incl. quota) into the
  helper envelope.
- Reuses the `TryReadSession` uniform-session gate (zero backend call on
  missing session) and forwards the session bearer.

## 4. Decision Points To Ratify (lock before implementing)

### 4.A Entry shape: client → helper (RATIFY)

How does the CAD/client hand the file to the helper?

- **Option A — multipart bytes (recommended).** The client POSTs the saved
  file **bytes** as multipart to `/document/checkin` (`item_id` as a form
  field, filename from the file part). The helper streams those bytes onward;
  it does **not** read the local filesystem.
- **Option B — local path.** The client sends a file **path**; the helper
  reads it from disk. *Rejected by default:* expands the helper's local
  file-read permission surface and adds path-traversal risk.

**Recommendation: Option A.** The implementation must **not** add local
filesystem reads to the helper for checkin (pinned by a static guard, §6).

### 4.B Multipart forwarding seam (new `IPlmBusinessClient` member)

`PostAsync`/`GetAsync` cannot carry a file. G1-B adds a third seam, e.g.:

```
Task<PlmBusinessResponse> PostMultipartAsync(
    Uri serverUri, string endpointPath, string bearerToken, string traceId,
    byte[] fileContent, string fileName, CancellationToken cancellationToken);
```

- Sends `multipart/form-data` with the file part named to match the backend
  `file` parameter; forwards bearer + protocol/trace headers like the existing
  seams; **must also read the response `X-Quota-Warning` header** (see §4.C).
- Both implementers must add it: production `HttpPlmBusinessClient` and the
  test fake(s). (`byte[]` vs `Stream` is a slice choice; the seam shape and the
  `file` part name are what is canonical.)
- Because current `PlmBusinessResponse` only carries `Ok/Data/Code/Message`,
  the implementation must extend the response object (or an equivalent typed
  return value) to carry response metadata: soft `quota_warning` and hard quota
  error details. Do **not** squeeze quota details into the human message only.

### 4.C Quota mapping (CORE DECISION — RATIFY)

G1-B must **not** let quota collapse into generic `PLM_VALIDATION_FAILED`, and
must **not** break the fixed-200 envelope contract. Ratified target: quota
rides **inside** the helper envelope.

- **Hard (`429` + `detail.code == QUOTA_EXCEEDED`)** → helper returns
  `ok=false` with `ErrorCodes.QuotaExceeded = "QUOTA_EXCEEDED"` (new helper
  constant, same wire string as the backend) and preserves the backend quota
  payload under `error.details.quota`. This requires extending
  `HelperRouteResult` / `HelperRouteResponse` so error details are not always
  `{}`.
- **Soft (`200` + `X-Quota-Warning`)** → helper returns `ok=true` and surfaces
  the warning in the success envelope as `data.quota_warning`. This
  requires the multipart seam to capture the response header (§4.B).

These field names are canonical for G1-B and pinned by tests (§6).

### 4.D Auth (same as G1-A; not re-litigated)

Uniform PLM session via `TryReadSession`: a missing/expired session
short-circuits with `AuthPlmNotLoggedIn` and makes **zero** backend calls. No
backend route or backend auth change.

### 4.E checkin ≠ DWG entity mutation

checkin uploads the **already-saved** file; it performs **no** DWG entity
creation/modification. The S10 `(entmake`/`(entmod` prohibition is **not**
relaxed.

## 5. Contract Surface To Update (route count 13 → 14)

The G1-A review showed route-count contracts live in **more than the Python
verifiers**. G1-B must update **all** of these together (13 → 14, and assert
`/document/checkin` present):

- Python: `clients/cad-desktop-helper/verify_bridge_static.py`
  (`check_helper_route_count_after_g1a`), `verify_lisp_shell_static.py`,
  `clients/autocad-material-sync/verify_material_sync_static.py`;
- C#: `Helper.Tests/HelperBusinessAuditContractTests.cs`
  (`test_s6_business_routes_plus_g1a_document_routes_have_expected_count` or a
  G1-B successor), `Bridge.Tests/BridgeContractTests.cs`;
- material-sync **client** tests;
- the DEV/Verification doc.

A pre-implementation `grep` of the whole `clients/` tree for the old count and
`MapGet(`/`MapPost(` count assertions is mandatory.

## 6. Mandatory Tests And Guards (named + assertion shape)

Layer key as in G1-A: **[xUnit]** behavior test with a recording fake;
**[static]** Python source verifier.

### 6.A Auth / forwarding

- **`test_g1b_checkin_requires_plm_session_before_backend_call`** [xUnit] —
  missing session → `AuthPlmNotLoggedIn`, **zero** backend calls.
- **`test_g1b_checkin_forwards_multipart_post_to_cad_checkin_with_bearer`** [xUnit] —
  active session → the **multipart** seam is invoked once with endpoint
  `/cad/{item_id}/checkin`, the file part present (bytes + filename), bearer
  forwarded; **not** the JSON `PostAsync`.
- **`test_g1b_checkin_requires_item_id_and_file`** [xUnit] — missing `item_id`
  or file → `HelperInputValidationFailed`, zero backend calls.

### 6.B Quota (the core decision, pinned)

- **`test_g1b_checkin_maps_hard_quota_429_to_quota_envelope_not_validation_failed`**
  [xUnit] — backend `429 QUOTA_EXCEEDED` → helper envelope `ok=false` with the
  `ErrorCodes.QuotaExceeded` / `"QUOTA_EXCEEDED"` code and
  `error.details.quota` copied from the backend `detail` payload (explicitly
  **not** `PLM_VALIDATION_FAILED` and not a generic HTTP-429 message).
- **`test_g1b_checkin_surfaces_soft_quota_warning_in_success_envelope`** [xUnit] —
  backend `200 + X-Quota-Warning` → `ok=true` +
  `data.quota_warning == <header value>`.

### 6.C Static guards

- **`test_g1b_static_guard_counts_routes_at_fourteen_and_keeps_document_and_dedup_constraints`**
  [static] — count **== 14**; `/document/checkin` present (alongside the 3
  G1-A `/document/*` routes); `/dedup/check` absent; bridge declares no routes.
- **`test_g1b_checkin_handler_does_not_read_local_filesystem`** [static] — per
  §4.A Option A, the checkin path adds no local file read (`File.OpenRead`,
  `File.ReadAllBytes`, etc.) in the helper.
- **`test_g1b_introduces_no_dwg_entity_mutation_token`** [static] — S10 guard holds.
- **`test_g1b_backend_cad_checkin_router_unmodified`** [static/diff].

## 7. Verification Plan

Doc-contract pytests (this taskbook PR and the later implementation PR):

```bash
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
git diff --check
```

The implementation PR additionally runs the three Python static verifiers and
(on Windows CI) `dotnet build` + the xUnit suite. No real-CAD signoff needed.

## 8. Explicit Non-Goals

G1-B does NOT: add a CAD-host command (helper-routes-only); touch BOM; write
CAD entities or relax the S10 guard; change backend routes/auth; alter dedup;
**audit** the checkin (consistent with the G1-A §3.B pure-proxy posture; a
checkin-audit vocabulary is a separate follow-up); read the local filesystem
(§4.A Option A); authorize any implementation.

## 9. Recommended Branch (after a separate opt-in)

Do **not** start implementation from this taskbook PR. After opt-in:

```text
feat/cad-helper-bridge-g1-b-document-checkin-r1-<date>
```

## 10. Reviewer Focus

1. Confirm scope is exactly one route (`/document/checkin`); count 13 → 14.
2. Ratify §4.A entry shape (multipart bytes; no helper local-file read).
3. Ratify §4.C quota mapping (envelope-internal;
   `ErrorCodes.QuotaExceeded = "QUOTA_EXCEEDED"`, hard quota under
   `error.details.quota`, soft quota under `data.quota_warning`) — the core
   decision.
4. Confirm §4.B multipart seam is a new `IPlmBusinessClient` member, not a
   reshape of `PostAsync`.
5. Confirm §4.D uniform session + §4.E checkin ≠ entity mutation.
6. Confirm §5 lists the full route-count contract surface (Python + C# +
   client tests + DEV doc) so the G1-A miss does not recur.

## 11. Status

Ready for review once: the doc exists at the canonical path;
`docs/DELIVERY_DOC_INDEX.md` references it (sorted); doc-index / R2 / Tier-B
drift checks pass; `git diff --check` is clean.
