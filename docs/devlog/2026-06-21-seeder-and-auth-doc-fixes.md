# Dev & Verification — seed-data CLI repair + AUTH_MODE doc fix

- **Date:** 2026-06-21
- **Branch:** `fix/seed-data-cli-model-registration`
- **PR:** [#847](https://github.com/adharamans/yuantus-plm/pull/847) · branch `fix/seed-data-cli-model-registration`
- **Scope:** dev-only mock-data tooling + docs. No production runtime code paths changed.

This is the per-change development & verification record. Each change below lists the
problem, what was developed, and the concrete evidence it was verified working.

---

## Change 1 — `yuantus seed-data` CLI repaired

**Files:** `src/yuantus/cli.py`, `src/yuantus/scripts/mock_data.py`

### Problem
`yuantus seed-data` failed before producing any data, via two independent bugs:
1. `cli.py:seed_data()` never called `import_all_models()`, so the standalone CLI
   process had no ORM mapping for `meta_item_types`. Inserting `meta_items` (FK →
   `meta_item_types`) raised: `could not find table 'meta_item_types'`.
2. `mock_data.py:build_simple_bom().add_children()` did `relationships_count += 1`
   without a `nonlocal` declaration → `UnboundLocalError`, aborting BOM generation
   before the seed `commit()`. (Net effect: even parts/docs rolled back.)

### Development
1. Added `from yuantus.meta_engine.bootstrap import import_all_models` and called
   `import_all_models()` at the top of `seed_data()` — mirroring what `init_db()`
   does at app startup (the CLI does not boot the app).
2. Added `nonlocal relationships_count` as the first line of `add_children()`.

### Verification
- `yuantus seed-meta` → `Seeded meta schema: Part, Part BOM, Document`.
- `yuantus seed-data --part-count 30 --doc-count 10 --bom-roots 3 --bom-depth 2`
  → `BOM generation complete. Created 9 relationships.` / `Seeding completed successfully!`
- DB inspection (`yuantus_dev.db`): `meta_items = 49` (30 Part + 10 Document + 9 Part BOM).
- Live API read (authenticated): `GET /api/v1/search/?q=&limit=5` returns the seeded
  parts, e.g. `P-10000 "Aluminum Spring"` (rev A, Draft, cost 468.85).
- Regression risk: no test references `mock_data`/`run_seed`/`seed-data` (grep clean),
  so the fix cannot have broken a previously-passing test; the seeder was untested.

---

## Change 2 — README AUTH_MODE default corrected

**File:** `README.md` (Auth section)

### Problem
The README stated `Default is YUANTUS_AUTH_MODE=optional` and separately documented how
to opt into `required` — but the code default is `required`
(`config/settings.py`: `AUTH_MODE: str = Field(default="required")`). The doc was both
factually wrong and self-contradictory.

### Development
- Changed the default statement to `required`, naming the public routes and the
  enforcing `AuthEnforcementMiddleware`.
- Replaced the now-redundant "how to require" snippet with how to **relax** for local
  dev: `export YUANTUS_AUTH_MODE=optional   # or: disabled`.

### Verification
- Confirmed runtime value: `python -c "from yuantus.config import get_settings;
  print(get_settings().AUTH_MODE)"` → `required` (with no env var / no `.env` present).
- Confirmed behavior: tokenless request to a protected route returns
  `401 "Missing bearer token"`; the same request passes with `YUANTUS_AUTH_MODE=optional`
  (verified by re-running 37 router tests: all pass under `optional`).

---

## Status
Both changes are on `fix/seed-data-cli-model-registration`, opened as PR #847 (rebased
onto current `main`). A SQLite seed smoke test (`test_seed_data_smoke.py`) now guards the
seed-data fix. Full DB test suite was run and root-caused separately; all 509 failures are
environment/config/dependency-version artifacts (bash/CI tooling on Windows, AUTH_MODE
default, FastAPI/Starlette version drift, Postgres-only tenancy), not product defects.
