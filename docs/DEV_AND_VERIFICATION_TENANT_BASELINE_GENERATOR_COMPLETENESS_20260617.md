# Dev & Verification: tenant-baseline generator completeness + drift guard

Date: 2026-06-17
Status: **IMPLEMENTED** — pending review + merge.
Supersedes the closed #790. Origin: a merge-train dry-run of the MES stack surfaced that the
committed tenant baseline was **incompletely generated** and its guard test was **order-fragile**.

## 1. Root cause (precise)

`scripts/generate_tenant_baseline.py` builds from the **ambient** `Base.metadata` via
`tenant_schema.build_tenant_metadata()`, which loads models through `import_all_models()` plus a
**hand-maintained supplement** of router-only model packages. That supplement had drifted: it
omitted `meta_engine/models/approval_automation.py`, whose table `meta_approval_automation_templates`
is imported **only** when `create_app()` loads its router.

So the generator's own metadata does **not** include `approval_automation` unless something else
(e.g. `create_app()`) already imported it into the ambient `Base.metadata`. The committed baseline
on `main` *does* contain the table (it was generated in a context where the app had been booted),
but the generator **can no longer reproduce that committed file** on its own. Consequences (both
confirmed empirically):

- **Regeneration drops a real table / order-fragile guard**: `build_tenant_metadata()` yields a
  *different* table set depending on whether `create_app()` ran first in the process. So
  `test_committed_baseline_matches_generator_output` (which generates **in-process**) flips on test
  order, and a plain regeneration (as the closed #790 did, in a process that had not booted the
  app) **silently drops `meta_approval_automation_templates`** from the baseline — a real
  per-tenant table a new tenant needs. This non-determinism is why #790, adding that in-process
  guard to CI's single shared process, would have turned CI **red**.
- **Separately, `main`'s baseline is stale**: it predates the merged `meta_ecm_publication_outbox`
  and `meta_erp_publication_outbox` tables. A faithful regeneration must add them.

Empirically (clean subprocesses): the generator collected 105 tables and a booted app registered
105 with exactly **one** table — `meta_approval_automation_templates` — that the app imports but
the generator's supplement did not. Regenerating with the fix **adds the stale ECM/erp outboxes
and drops nothing** (verified: net dropped tables = ∅).

## 2. Fix

- **Completeness** (`tenant_schema.py`): add `meta_engine.models.approval_automation` to the
  supplement, matching the existing pattern. The generator's tenant-table set now equals the
  booted app's (no omissions).
- **Regenerate** the baseline — it now includes `meta_approval_automation_templates` (and the
  guard is order-independent because the generator always imports it).
- **New drift guard** (`test_generator_metadata_covers_booted_app_tenant_tables`): runs
  `build_tenant_metadata()` and `create_app()` in **two clean subprocesses** and asserts the app
  has no per-tenant table the generator omits (the reverse — generator pinning lazily-loaded
  tables — is allowed). Subprocess isolation makes it **deterministic regardless of test order**,
  unlike the in-process guard.
- **CI**: register the test in `ci.yml`. Safe now (order-independent), which #790's version was not.

## 3. Verification

- Generator vs booted app: 105 == 105, **omitted = []** (was `[meta_approval_automation_templates]`).
- The existing guard now passes **both alone and after `create_app()`** (the order that broke #790).
- The new drift guard **passes with the fix and FAILS when the fix is reverted** (proven to catch
  the exact original omission) — a true guard, not a tautology.
- CI-like shared process (create_app contract tests → the baseline guard): green.
- CI test list stays sorted/unique.

## 4. Files

`src/yuantus/scripts/tenant_schema.py` · `migrations_tenant/versions/t1_initial_tenant_baseline.py`
· `src/yuantus/tests/test_tenant_baseline_revision.py` · `.github/workflows/ci.yml` ·
`docs/DELIVERY_DOC_INDEX.md` (this doc).

## 5. Boundary

Tenant-baseline generator + guard only. No model/schema change beyond making the baseline reflect
the already-existing `approval_automation` table. Independent of the MES stack (off `main`).
