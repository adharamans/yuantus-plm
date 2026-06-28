# Dev & Verification — L4 operator admin CLI: `license cap-history` + `license revoke`

> Date 2026-06-27 · branch `claude/license-admin-cli` · no route added (CLI only).

## What & why
The L4 licensing read/write surface shipped as HTTP routes — seat-cap change history
(`GET /api/v1/admin/license-cap-history`, #889), license revoke
(`POST /api/v1/admin/licenses/{key}/revoke`, #892), and entitlement/license status
(#881) — plus a pre-existing `yuantus license status` / `import` CLI. This slice
finishes the **operator surface** by handing the cap-history and revoke capabilities to
ops as CLI subcommands, the natural low-risk tool for a support/runbook context (no UI
framework, no new route, no new auth surface). Backend services are unchanged.

Two new subcommands under the existing `license` Typer group:
- `yuantus license cap-history --tenant-id <t> [--limit N]` — print a tenant's seat-cap
  change history, newest-first; unknown tenant → empty (no existence leak); blank → exit 1.
- `yuantus license revoke --license-key <k> --reason <r> [--revoked-by <uid>]` — flip the
  license to `Revoked` + append an audit row; a key matching nothing → exit 1 (so a typo
  is visible, not silent success); blank key → exit 1.

## Design decisions
- **Shared helper, no drift.** The cap-history audit-trail parse was extracted from the
  #889 router into `app_framework/license_cap_history.py::collect_seat_cap_history`; the
  HTTP route and the CLI now call the *same* function (the route maps its `ValueError`
  on blank → 400; the CLI → exit 1). #889's 9 router tests still pass unchanged, proving
  the refactor preserved behavior (400 detail string, `Cache-Control: no-store`, shape,
  unknown-tenant-empty).
- **Revoke reuses the append-only service** (`LicenseRevocationService.revoke_license`):
  it sets `status='Revoked'` (flipping `is_entitled` off) and writes the audit row, but
  does **not** clear the seat cap — cap rollback stays a separate explicit op. The CLI
  adds no new mutation logic; it materializes display fields inside the session before
  commit/close to avoid detached-instance access.
- **Tenancy口径 (revoke + cap-history) — fail-fast, not opaque.** Although the revoke
  service keys off the globally-unique `license_key`, the row still lives in a tenant's
  database, so `get_db_session()` requires tenant/org context first in
  `db-per-tenant`/`db-per-tenant-org`/`schema-per-tenant`. Rather than let it raise an
  opaque session-layer `RuntimeError`, revoke now takes optional `--tenant-id`/`--org`
  and applies the established operability guard (mirrors the `date-obsolete-worker` CLI):
  in a non-`single` mode without `--tenant-id` → clear message + **exit 2**;
  `db-per-tenant-org` without `--org` → exit 2; otherwise it sets the request-context vars
  and proceeds. `single` mode needs neither (the global key suffices). `cap-history`/
  `status` already set tenant context from their required `--tenant-id` (so they work in
  `single`/`db-per-tenant`/`schema-per-tenant`); `revoked_by` is `Optional[int]` (operator
  user id, recorded in the audit row).
- **`cap-history --limit` parity with the route** — constrained `>=1` (`typer.Option(min=1)`,
  i.e. click `IntRange`), matching the HTTP route's `Query(..., ge=1)`, so `0`/`-1` are
  rejected with a usage error (exit 2) instead of reaching the DB layer.

## Verification
- Local: `PYTHONPATH=<wt>/src YUANTUS_PYTEST_DB=1 pytest` →
  **32 passed** = 16 new CLI (`test_license_admin_cli.py` — incl. the multi-tenant revoke
  guard: non-single-without-tenant → exit 2, db-per-tenant-org-without-org → exit 2,
  with-tenant-proceeds → revoked; and `--limit 0` → exit 2) + 9 #889 router + 7 #892 router
  (the refactor's only regression surface — both green).
- The CLI tests are **DB-backed** (real in-memory SQLite via a redirected
  `get_db_session`), not mock-everything: they assert the genuine effect — `revoke`
  actually flips `AppLicense.status` to Revoked and writes an `admin:license/revoke`
  audit row (and emits **no** seat-cap audit, proving append-only); `cap-history` reads
  seeded seat-cap rows newest-first, excludes non-seat-cap LICENSE audits, and honours
  `--limit`. Plus source contracts pinning command registration + routing to the shared
  helper/service.
- Anti-false-green wiring: new test added to the `ci.yml` contracts list (sorted; list-order
  pin green) and to the `detect_changes` entitlement case (with `cli.py` + the new helper)
  so a CLI/helper change actually triggers the contracts job rather than falling through to
  `src/*` plugin-tests-only.

## Files
- `src/yuantus/meta_engine/app_framework/license_cap_history.py` (new — shared helper)
- `src/yuantus/meta_engine/web/license_cap_change_history_router.py` (refactor → call helper)
- `src/yuantus/cli.py` (new `license cap-history` + `license revoke` subcommands)
- `src/yuantus/meta_engine/tests/test_license_admin_cli.py` (new — DB-backed CLI tests)
- `.github/workflows/ci.yml` (contracts list + detect_changes entitlement case)
- `docs/DELIVERY_DOC_INDEX.md` (this doc registered)
