# Dev & Verification — Phase-7 BOM write-back: CI wiring follow-up

> Date 2026-06-29 · branch `claude/phase7-ci-wiring-followup` (off `origin/main` after #905/#906) · **CI/anti-false-green follow-up only** — the provider implementation already landed.

## Context (already on main)
The Phase-7 governed BOM multi-table write-back **provider endpoint** (`PATCH /api/v1/bom/multitable/{part_id}/lines/{bom_line_id}` → `200 {ok, bom_line_id}`, ratified design #901) **landed via #905** (`7f4f99a`) with its model/migration/service/router + a 20-test acceptance suite `test_bom_multitable_writeback.py`; its dev-verification record landed via **#906** (`docs/development/plm-collaboration-phase7-writeback-provider-dev-verification-20260629.md`). The build conformance gates **G1–G5** are in #903's day2-resolution doc. **This follow-up does not re-implement any of that.**

## What this follow-up fixes
A **live false-green on main**: #905 merged the 20-test §7 suite + the writeback service/model **but did not wire them into CI** — `test_bom_multitable_writeback.py` was absent from the `ci.yml` contracts list, and `bom_multitable_writeback_service.py` / `meta_bom_writeback_audit.py` / the test were absent from `detect_changes`. So the 20 load-bearing tests existed on main but **never ran in CI**. This follow-up:
- adds `test_bom_multitable_writeback.py` to the `ci.yml` contracts pytest list (sorted; list-order pin green);
- adds the write-back service/model/test paths to the `detect_changes` entitlement case → `run_contracts=true` (so a future service/model/test-only change triggers the contracts job);
- registers the slice's DEV/V record in `docs/DELIVERY_DOC_INDEX.md`.

## G1–G5 conformance (audited against #903)
The **landed** impl passes all five: **G1** guard order (write-entitlement before 404; missing/empty Idempotency-Key → 400; locked → 409); **G2** write entitlement (`plm.bom_multitable_writeback` registered + pact-seeded); **G3** idempotency + audit (same-key cached 200, different-payload 409, audit+mutation one transaction); **G4** provider-side checked-in pact carries the `Idempotency-Key` interaction (live-broker re-add is the metasheet2 #3332 consumer follow-up); **G5** real `LifecycleState(version_lock=True)` 409 + Draft 200 tests.

## Verification
- Local (`YUANTUS_PYTEST_DB=1`): `test_bom_multitable_writeback.py` **20 passed**; `ci.yml` list-order pin + doc-index completeness/sorting/references green.
- Scope: **no impl/migration change** — `.github/workflows/ci.yml` + this doc + `DELIVERY_DOC_INDEX.md` only. Based on current `origin/main` (post-#905/#906/#903), so it neither conflicts with the landed impl nor reverts #903's G1–G5.

## Files
- `.github/workflows/ci.yml` (contracts list + detect_changes case)
- `docs/DEV_AND_VERIFICATION_PHASE7_BOM_WRITEBACK_20260629.md` (this doc) + `docs/DELIVERY_DOC_INDEX.md`
