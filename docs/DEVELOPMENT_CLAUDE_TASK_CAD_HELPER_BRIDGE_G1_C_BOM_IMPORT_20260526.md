# Claude Taskbook: CAD Helper Bridge G1-C — BOM Import (Path A, async /cad/import reuse)

Date: 2026-05-26

Type: **Doc-only taskbook.** Changes no runtime, schema, workflow, or
client/helper code. It specifies the contract a later, separately opted-in
implementation PR will deliver. Merging this taskbook does **NOT** authorize
that implementation.

Naming: **"G1-C" is a proposal** for the third G1 slice. Grounded against the
ratified BOM path decision memo
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_G1_C_BOM_PATH_DECISION_20260526.md`
(merged at `f10cc0c2` / #649): **Path A** — reuse the async `/cad/import`
pipeline; server-side BOM extraction; no client assembly walker in R1.

## 1. Purpose

G1-C adds **one** helper route, `POST /document/bom-import`, that uploads the
already-saved CAD file (multipart) to the existing backend async pipeline
`POST /cad/import` with `create_bom_job=true`, and returns the **`cad_bom` job
handle + `file_id`** so the client can poll BOM readiness/output. Helper route
count **14 → 15**. BOM extraction is **server-side** (the existing `cad_bom`
worker job → `CadBomImportService.import_bom`); G1-C builds/sends **no tree
payload** and contains **no per-host assembly walker** (deferred to a later
CAD-host slice). Helper-routes-only; fully testable at the HTTP level.

## 2. Grounded Current Reality (origin/main = f10cc0c2)

- Backend `POST /cad/import` (`web/cad_import_router.py:99`, multipart
  `UploadFile`) takes form fields incl. `item_id`, **`create_bom_job`**
  (default `False`, `:125`), `auto_create_part`, `cad_format`,
  `cad_connector_id`; **auth-gated** (`get_current_user`). It enqueues a
  `cad_bom` job (`services/cad_import_service.py:865`
  `_enqueue("cad_bom", …)`) → worker (`tasks/cad_pipeline_tasks.py:1428`)
  calls `import_bom`. Backend is reused **unchanged**.
- Response `CadImportResponse` (`web/cad_import_router.py:44`): `file_id`,
  `jobs: [{id, task_type, status}]`, optional `cad_bom_url` (absent until the
  worker persists BOM output). The `cad_bom` job is the entry with
  `task_type == "cad_bom"`.
- **Status / output surface selected for G1-C**:
  `GET /api/v1/cad/files/{file_id}/bom`
  (`web/cad_file_data_router.py:100`, prefix `/cad`, `CadBomResponse`). Polled
  by **`file_id`**: returns `job_status` = readiness (`completed` with the
  `bom` payload once `cad_bom_path` is set; otherwise the matched `cad_bom`
  `ConversionJob.status`; `404` if no `cad_bom` job for the file). **Memo
  correction:** generic `GET /api/v1/jobs/{job_id}` **does exist**
  (`api/routers/jobs.py:186`, registered in `api/app.py:284`), but G1-C does not
  choose it as the client-facing BOM readiness surface because it is a raw job
  API and does not return the persisted BOM payload. The document-sync route is
  separate (`/api/v1/document-sync/jobs/{job_id}`,
  `document_sync_core_router.py:208`), and `/file/conversion/{job_id}` remains
  excluded by the memo. So **`/cad/files/{file_id}/bom` is the single
  status+output surface** for G1-C — by `file_id`, not `job_id`.
- Helper today: 14 routes; G1-C would make it 15. `IPlmBusinessClient` has
  `PostAsync` (JSON), `GetAsync`, `PostMultipartAsync` (G1-B; **file part
  only**, no extra form fields).

## 3. Scope

- **One** new helper route `POST /document/bom-import` (multipart). Count
  **14 → 15**.
- Forwards the uploaded file to backend `POST /cad/import` with
  `create_bom_job=true` (+ `item_id`, and `auto_create_part=true` when no
  `item_id`); returns `file_id` + the selected `cad_bom` job handle.
- Reuses the `TryReadSession` uniform-session gate (zero backend call on
  missing session) and forwards the bearer.

## 4. Decision Points To Ratify (lock before implementing)

### 4.A Root item policy: item_id or explicit auto-create (RATIFY)

The backend `cad_bom` worker requires a root `item_id`
(`tasks/cad_pipeline_tasks.py:1397`), while `/cad/import` accepts either an
explicit `item_id` or `auto_create_part=true`
(`services/cad_import_service.py:583`, `:591`). G1-C locks this route-level
policy:

- if `item_id` is present, forward `item_id` and do **not** set
  `auto_create_part=true`;
- if `item_id` is absent, forward `auto_create_part=true` deliberately so the
  backend creates/updates the root Part before enqueueing `cad_bom`;
- do not silently omit both fields, because that turns into a backend validation
  failure instead of a usable BOM import request.

### 4.B Multipart seam: extend for form fields (RATIFY)

`/cad/import` needs form fields (`create_bom_job=true`, `item_id`,
`auto_create_part`) **in addition to** the file part. G1-B's
`PostMultipartAsync` sends **only** the file. Options:
- **Option A — extend `PostMultipartAsync` (recommended)** with an optional
  `IDictionary<string,string> formFields = null` parameter (backward
  compatible: G1-B callers pass none). The seam adds each field as a
  `StringContent` part alongside the file.
- **Option B — new method** `PostMultipartFormAsync(...)`. More surface; only
  if Option A is judged to muddy the G1-B seam.

Recommendation: **Option A**. Either way, the seam change is additive and must
not alter the G1-B `/document/checkin` behavior (pinned by the existing G1-B
tests).

### 4.C Response shaping (RATIFY)

The helper parses `CadImportResponse`, selects the **`cad_bom`** job
(`jobs[*].task_type == "cad_bom"`), and returns
`{ file_id, job: { id, task_type, status } }` (and `cad_bom_url` if present).
If `create_bom_job=true` yet **no `cad_bom` job** is returned, the helper maps
to a `PlmValidationFailed`-class error (do not silently succeed). The success
envelope is the fixed-200 `data` object (G1-A/G1-B contract); no new envelope
extension is required.

### 4.D Status polling — no new helper route in R1 (RATIFY)

G1-C adds **only** `/document/bom-import` (count 14 → 15). Status polling uses
the **existing backend** `GET /api/v1/cad/files/{file_id}/bom` (§2). The
taskbook decision: **G1-C does NOT add a helper status-proxy route** (that
would be 14 → 16 and is a separate concern). The client polls the backend BOM
surface with the returned `file_id`. If a helper-mediated status proxy is later
required, it is its **own** slice.

### 4.E Auth + no backend change + no walker

Uniform `TryReadSession`; backend `/cad/import` and `/cad/files/{file_id}/bom`
reused **unchanged**; **no** per-host assembly walker (server extracts the BOM);
**no** audit; checkin/lock routes untouched.

## 5. Contract Surface To Update (route count 14 → 15)

Update **all** together (assert `/document/bom-import` present), per the hard
rule that **every §6 static guard is a deliverable, not documentation**:

- Python: `verify_bridge_static.py` (`check_helper_route_count_after_g1b` → 15
  / rename to `…_after_g1c`), `verify_lisp_shell_static.py`,
  `verify_material_sync_static.py`;
- C#: `Helper.Tests/HelperBusinessAuditContractTests.cs`,
  `Helper.Tests/HelperSessionRoutesContractTests.cs`,
  `Helper.Tests/HelperResetLocalTokenContractTests.cs`,
  `Bridge.Tests/BridgeContractTests.cs`;
- material-sync **client** tests
  (`CADDedupPlugin.Client.Tests/MaterialSyncClientS8ContractTests.cs`);
- DEV/Verification doc.

A pre-implementation `grep` of the whole `clients/` tree for the old count
(`14` / `Assert.Equal(14`) and `MapGet(`/`MapPost(` count assertions is
mandatory (the G1-A/G1-B residual-scan discipline).

## 6. Mandatory Tests And Guards (named + assertion shape)

`[xUnit]` = behavior with a recording fake; `[static]` = Python verifier.

### 6.A Auth / forwarding

- **`test_g1c_bom_import_requires_plm_session_before_backend_call`** [xUnit] —
  missing session → `AuthPlmNotLoggedIn`, **zero** backend calls.
- **`test_g1c_bom_import_forwards_multipart_to_cad_import_with_create_bom_job`** [xUnit] —
  active session → the multipart seam is invoked to `/cad/import` with the
  file part **and** form field `create_bom_job=true` (+ `item_id` when given),
  bearer forwarded.
- **`test_g1c_bom_import_forwards_auto_create_part_when_item_id_absent`** [xUnit] —
  no `item_id` → the multipart seam includes `auto_create_part=true` (and still
  `create_bom_job=true`), rather than omitting the root-item policy and letting
  the backend fail later.

### 6.B Response shaping

- **`test_g1c_bom_import_returns_cad_bom_job_handle_and_file_id`** [xUnit] —
  given a `CadImportResponse` with a `cad_bom` job, the helper returns
  `file_id` + that job `{id, task_type, status}`.
- **`test_g1c_bom_import_errors_when_no_cad_bom_job_returned`** [xUnit] —
  `create_bom_job=true` but no `cad_bom` job in `jobs[]` → error (not a silent
  success).

### 6.C Static guards

- **`test_g1c_static_guard_counts_routes_at_fifteen_and_keeps_constraints`**
  [static] — count **== 15**; `/document/bom-import` present (alongside the 4
  prior `/document/*` routes); `/dedup/check` absent; bridge declares no routes.
- **`test_g1c_bom_import_builds_no_client_side_tree_or_walker`** [static] — the
  `/document/bom-import` route/handler contains **no** assembly-walk / tree
  construction (it forwards a file, not a tree); no SolidWorks/Inventor/AutoCAD
  SDK symbols.
- **`test_g1c_bom_import_does_not_read_local_filesystem`** [static] — mirrors
  the G1-B guard (multipart bytes; no `File.OpenRead` / `Path.Combine` /
  `form["path"]`).
- **`test_g1c_backend_cad_import_and_bom_routes_unmodified`** [static/diff] —
  the PR does not modify `web/cad_import_router.py` or
  `web/cad_file_data_router.py`.

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

The implementation PR additionally runs the three Python verifiers and (on
Windows CI) `dotnet build` + xUnit. No real-CAD signoff needed for G1-C
(helper-routes-only; the per-host walker is a later slice).

## 8. Explicit Non-Goals

G1-C does NOT: build a tree payload or include a per-host assembly walker (Path
A extracts server-side); add a server-side direct BOM route (that is Path B +
its own server taskbook); add a helper status-proxy route (§4.D); change
backend routes/auth; add a CAD-host command; read the local filesystem; audit;
relax the S10 guard; add new `ErrorCodes`; authorize any implementation.

## 9. Recommended Branch (after a separate opt-in)

```text
feat/cad-helper-bridge-g1-c-document-bom-import-r1-<date>
```

## 10. Reviewer Focus

1. Confirm scope is exactly one route (`/document/bom-import`); count 14 → 15.
2. Ratify §4.A root item policy (`item_id` when present; explicit
   `auto_create_part=true` when absent).
3. Ratify §4.B seam extension (form fields; Option A, additive, G1-B intact).
4. Ratify §4.C response shaping (select `cad_bom` job; error if absent).
5. Confirm §4.D: status via existing `/cad/files/{file_id}/bom` by `file_id`;
   **no** helper status-proxy route in R1; generic `/jobs/{job_id}` exists but
   is not the chosen client-facing BOM status+output surface.
6. Confirm §4.E no backend change / no assembly walker / no audit.
7. Confirm §5 lists the full 14 → 15 contract surface and §6 static guards are
   real verifier checks.

## 11. Status

Ready for review once: the doc exists at the canonical path;
`docs/DELIVERY_DOC_INDEX.md` references it (sorted); doc-index / R2 / Tier-B
drift checks pass; `git diff --check` is clean.
