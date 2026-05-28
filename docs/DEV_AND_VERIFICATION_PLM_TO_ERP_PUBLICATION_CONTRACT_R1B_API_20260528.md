# DEV & Verification: PLM→ERP Publication Contract — R1-B Read-Only API

Date: 2026-05-28

Implements the R1-B read-only `publication-readiness` API per the merged R1-A
contract taskbook
(`DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R1A_TASKBOOK_20260527.md`) and the
G2 program plan (#663). First **code** slice of G2. No ERP write, no real-ERP
connection, no Odoo dependency, no purchase/sale surface.

## 1. What changed

- `src/yuantus/meta_engine/web/plm_erp_publication_router.py` (new) — `GET
  /api/v1/plm-erp/items/{item_id}/publication-readiness`.
- `src/yuantus/api/app.py` — import + `include_router(publication_readiness_router,
  prefix="/api/v1")` alongside the release routers (real registration point).
- `src/yuantus/meta_engine/tests/test_plm_erp_publication_router.py` (new) — **16**
  tests covering the R1-A §8 catalog (incl. the formula-edge and exception-chain
  `__cause__` cases below).
- `conftest.py` — added the new test file to `_ALLOWLIST_NO_DB` (see §4).
- `.github/workflows/ci.yml` — added the new test file to the `contracts`
  job's explicit pytest list so CI executes it (see §5a).
- `src/yuantus/meta_engine/tests/test_breakage_design_loopback_metrics.py` —
  aligned its secondary app route-count pin to the new global count (`678`).

## 2. Implementation (faithful to R1-A, grounded)

- **Wraps** `ReleaseReadinessService.get_item_release_readiness` and reuses
  `release_readiness_router._build_response` / `ReadinessSummary` /
  `ReadinessResource` — no readiness re-derivation.
- **Eligibility** is the R1-A formula **directly**: `eligible =
  latest_released_ok ∧ suspended_ok ∧ readiness.summary.ok ∧ esign_ok` — NOT
  `len(blocking_reasons) == 0` (so a `summary.ok == false` with no per-resource
  errors is still ineligible). `blocking_reasons` is the explanation, not the
  verdict.
- **Guards**: `LatestReleasedGuardService` / `SuspendedGuardService` raise
  `NotLatestReleasedError` / `SuspendedStateError` (both `ValueError` subclasses);
  caught into `blocking_reasons` (`not_latest_released` / `suspended`); a generic
  `ValueError` maps to `HTTPException(400) from exc` (exception-chaining).
- **esign** mirrors `release_orchestration_router._plan_steps` exactly: `dict ∧
  "is_complete" in m ∧ not bool(m["is_complete"])` blocks; `None` / missing-key
  do not.
- **Payload**: `item{}` + `version{}` (from `Item.current_version`; `version.generation`
  = `ItemVersion.generation`) + `file_refs[]` (from the version's `version_files`)
  + `summary` / `resources` / `esign` / `blocking_reasons` + echoed `ruleset_id` /
  `limits`. `version` is `Optional` (None when no current version).
- **Params** `ruleset_id`(readiness) / `mbom_limit` / `routing_limit` /
  `baseline_limit`(20, ge=0 le=200), echoed into the response.

## 3. Two deliberate decisions (flagged for review)

- **Auth = `require_admin_permission`** (not un-gated). The endpoint wraps the
  SAME readiness data the sibling admin-gated `/release-readiness` endpoint
  exposes; un-gating it would be an access downgrade. A dedicated ERP-adapter
  principal / permission is an **R2** (adapter) concern. Tested via
  `denies_non_admin` → 403.
- **No `enforcement` (guard-enabled) field** was added. R1-A locked the payload
  schema and it has no such field; adding one unilaterally would extend the
  locked contract. Implemented as locked. **Candidate R1-A amendment**: an
  outbound consumer cannot currently tell whether `eligible=true` resulted from
  guards passing vs. guards being feature-disabled — worth deciding in a future
  R1-A amend, not silently here.

## 4. Test-harness / environment facts (honest)

- The repo FastAPI app uses PEP 604 `X | Y` unions, so `create_app()` requires
  **Python ≥ 3.10**. The local default interpreter is 3.9 (cannot build the app);
  the existing `release_readiness_router` tests have the identical constraint.
  These tests were executed in a throwaway **Python 3.11** venv.
- The `AuthEnforcementMiddleware` enforces only when `AUTH_MODE == "required"`
  (the `Settings` default IS `"required"`). The test file is **self-contained**:
  an autouse fixture `monkeypatch.setattr`s **only the middleware's**
  `get_settings` to return `AUTH_MODE="optional"` (the bypass branch reads only
  `.AUTH_MODE`). It does **not** mutate the global `get_settings` lru_cache and
  does **not** rely on an externally-exported `YUANTUS_AUTH_MODE` — so the file
  runs correctly under CI's default env (e.g. in `ci.yml`'s contracts list).
- `conftest.pytest_ignore_collect` **ignores** any non-allowlisted `.py` under
  `meta_engine/tests` when the DB is off. The new test file was added to
  `_ALLOWLIST_NO_DB` (mirroring `test_release_readiness_router.py`) so a no-DB
  directory/suite run collects it (without this it would be silently skipped).

## 5. Verification

- `<python3.11> -m pytest src/yuantus/meta_engine/tests/test_plm_erp_publication_router.py`
  (**no external env**) → **16 passed** — including the formula-edge case
  (`summary.ok == false`, `resources == []` → `eligible == false`) and the
  direct-handler exception-chaining case (`HTTPException.__cause__ is the
  original ValueError`). Self-contained run confirms CI-default robustness.
- Full `meta_engine/tests` directory with `YUANTUS_AUTH_MODE=optional`
  (the env the OTHER router tests need) → **325 passed** (323 baseline + the 2
  new tests) — no collateral from the conftest/fixture/ci.yml changes.
- Targeted stale route-count secondary pin:
  `test_breakage_design_loopback_metrics.py::test_prometheus_surface_exposes_three_gauges_no_new_route`
  → pass after aligning `677 → 678`.
- doc-contract pytests (delivery-doc-index references + sorting, DEV/verification
  index completeness + sorting, doc-index sorting) — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass
  (unchanged; no client/helper change).
- `git diff --check` clean.

## 5a. CI enforcement (gap closed)

The behavioral TestClient tests **are** executed by the PR `contracts` CI job:
the test file was added to `ci.yml`'s explicit pytest list (alphabetically,
between `test_phase6_*` and `test_quality_*`), and the autouse fixture (§4) makes
it self-contained on auth so it passes under CI's default env. This closes the
gap noted in the first draft (where these behavioral tests, like the merged
sibling readiness router tests, were not CI-executed). The fixture patches only
the middleware's `get_settings`, so it does not affect the other contract tests
in the list.

## 6. Status

R1-B read-only API implemented and validated locally (16 targeted tests + 325
suite tests passed). No
external side effects. Next in G2: R2 adapter/outbox (dry-run/replay; real ERP
connector its own taskbook) — separate opt-in. The export route
(`/publication/export`) remains a later, separable slice.
