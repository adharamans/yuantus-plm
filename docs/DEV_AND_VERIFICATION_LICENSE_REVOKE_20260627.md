# L4 Fork C — License revoke (append-only) (impl + verification)

> Task: backlog line 4 (V2 seats/licensing), Fork C. Branch `claude/license-revoke` · base `origin/main` (route 724). Sibling of Fork B (#889).

## What
`POST /api/v1/admin/licenses/{license_key}/revoke` (superuser) — revoke a license. Sets `AppLicense.status='Revoked'` for the row(s) matching `license_key`; since `EntitlementService.is_entitled` requires `status=='Active'`, revoking flips the feature off. Writes a meta-side LICENSE audit row.

## Decision (owner-ratified principle: append-only, no implicit rollback)
- Revoke is **append-only**: it sets status + writes an audit row. It does **NOT** clear the seat cap (`TenantQuota.max_users`). Cap rollback, if ever wanted, is a separate explicit operation with its own previous-state record — so a revoke can never silently reduce enforcement.
- **Idempotent**: re-revoking an already-Revoked license is a no-op that still returns the rows (and records another audit line).
- **Not a seal**: re-importing the same signed license re-activates it (revoke is an admin operator action). Documented as expected.
- Reason is required (`min_length=1`) and audited (url-quoted into the audit `path`, bounded to the 500-char column).

## route-count (design-lock)
New route ⇒ app-route count **724 → 725**, lockstep across all four pins (phase4 authoritative, breakage, metrics `EXPECTED_TOTAL_ROUTES`, tier_b_3 string-pin).

## anti-false-green wiring
`test_license_revoke_router.py` added to the `ci.yml` contracts list (sorted) **and** the new router + service + test added to the `detect_changes` entitlement case (license-surface changes → `run_contracts`). DEV/V doc registered in `DELIVERY_DOC_INDEX.md`.

## Verification (local)
`PYTHONPATH=worktree/src YUANTUS_PYTEST_DB=1 pytest` — new router test (7 cases: revoke sets Revoked + audits; unknown key → 404; requires superuser (403, not revoked); reason required → 422; idempotent re-revoke; append-only — only a status flip + one audit row, no cap write; one route) + all four route-count pins at 725 + ci order/change-scope + doc-index completeness/sorting/references.

## Scope / follow-up
Service + endpoint (+ audit). A CLI `yuantus license revoke` and an admin UI over revoke/cap-history (Fork B) are separate follow-ups. Un-revoke is intentionally not provided (re-import re-activates).
