# DEV & VERIFICATION — V2 Seats S1 (Option A): license seat-cap projection

Date: 2026-06-20 · Landed on `main` via **#817** (squash `80859d6b`). Design of record:
`docs/development/plm-collab-v2-seats-design-20260619.md` (Option A, owner-ratified; status
closed-out in #821). This doc records the implementation + verification of the merged work; it
is a closeout, not a plan.

## 1. Summary

A paid "seats" cap (how many users a paid feature covers) is sourced from the **vendor-signed
license** and projected, at `yuantus license import` time, onto the identity-side
`TenantQuota.max_users` — the *same* cap the existing quota framework already enforces. No new
model or migration: seats is an **extension of the existing tenant-quota spine**, not greenfield.
`is_entitled()` is untouched — feature authorization and seat-cap enforcement stay separate code
paths.

## 2. What changed

- **`src/yuantus/security/auth/seat_projection.py`** (new) — `project_license_seats(identity_session,
  payload)`. Reads `payload["seats"]`; **absent or invalid** seats (not an `int` ≥ 1 — `bool`,
  non-int, `< 1` all rejected explicitly) is a **fail-open no-op** that returns `None` and logs,
  leaving `max_users` unchanged. A valid cap `ensure_tenant(...)` then `upsert_quota(tenant,
  {"max_users": seats})` (idempotent). Raises **only** on an actual DB failure.
- **`src/yuantus/cli.py`** (`yuantus license import`) — commits the license **first** (it is the
  commercial source of truth), then **best-effort** projects seats inside `get_identity_db_session()`.
  Any exception is caught: the license stays active and the operator is told the cap was "not
  applied (`TenantQuota.max_users` left unchanged)"; re-running the import re-projects.
- **`scripts/dev/sign_dogfood_license.py`** — dogfood signer gains `--seats`. **DEV stand-in only,
  not the vendor production issuance tool.** `_seats_arg` rejects `< 1` at argparse time;
  `build_and_sign` rejects `bool` / non-int / `< 1` before any file is written (mirrors the
  projection's contract, so a self-verifying-but-inert license cannot be minted).
- **`src/yuantus/meta_engine/tests/test_seat_projection.py`** (new, 11 tests) + **CI/conftest**
  registration (ci.yml contracts list + `conftest._ALLOWLIST_NO_DB`).
- **Review [P2] folded in** (#817, then refined in the closeout): the "omit = no cap" wording
  across the signer / `seat_projection.py` / CLI was corrected to "no projection → `max_users`
  left unchanged (an existing cap is preserved, not cleared)".

## 3. Design notes (the invariants)

- **`is_entitled()` stays seats-free.** Feature auth and seat enforcement are separate paths; this
  helper is the only meta↔identity hop, and it is a single import-time write — never a hot-path
  join (the limit and the active-user count both live identity-side, so enforcement stays single-DB).
- **`TenantQuota.max_users` is the identity-side enforcement cache.** It is enforced by the
  existing `QuotaService` / `_apply_quota_limits(..., {"users": 1})` provisioning gate at
  `POST /admin/users`, itself **`QUOTA_MODE`-gated (default `disabled` → ships inert)**.
- **License is the source of truth for the cap.** Re-import overwrites `max_users` (a manually-set
  value is overwritten — the documented Option-A tradeoff).
- **Fail-open by contract.** The caller projects *after* the license is committed and treats any
  raised exception as non-fatal: a transient identity-DB failure must never un-activate a valid
  paid license. With `QUOTA_MODE` default-off a missing cap is inert; `upsert_quota` is idempotent.

## 4. Verification

`test_seat_projection.py` runs in the **no-DB contracts job** (in-memory identity session; #817
reported 17 passed in no-DB mode across this + sibling assertions). Coverage:

- **Projection** — `test_projects_valid_seats_to_max_users`; `test_reimport_updates_cap_license_is_source_of_truth`
  (re-import overwrites → license is source of truth); `test_projected_cap_is_enforceable_at_provisioning`
  (the projected cap is actually enforced by `QuotaService.evaluate` under `QUOTA_MODE`).
- **Fail-open** — `test_absent_seats_projects_nothing`; `test_invalid_seats_is_fail_open_noop`
  (parametrized `0, -1, -5, True, "20", 1.5` → no-op, prior cap untouched).
- **FK integrity** — `test_fk_is_actually_enforced_in_this_env` (the tenant row must exist; the
  guard isn't a no-op in this env).
- **Signer fail-fast** — `test_build_and_sign_rejects_invalid_seats` (`0, -1, True`);
  `test_build_and_sign_mints_valid_seats_and_omits_none`; `test_seats_arg_validator_enforces_ge_1`;
  `test_cli_rejects_zero_seats_and_writes_no_file` (subprocess: `--seats 0` exits non-zero and
  writes **no** license file).

CI: #817 merged green (contracts + regression); no model/migration/baseline change to verify
(reuses `TenantQuota`).

## 5. Out of scope / future (parked — archived #819, named in the seats design §5)

- **Vendor-private license issuance tool** — minting a production seat-bearing license is out of
  clean in-repo scope; the dogfood signer is the dev stand-in.
- **B2 assignment subsystem** (per-SKU assigned-user seats); **MetaSheet-consumer reconciliation**;
  **explicit cap clearing / lowering** (omit/`null` `seats` → clear or reduce a prior cap — a
  separate design, since absent-seats-clears-cap could clobber an admin-set quota).
- **Standing notes:** Admin seat UX (no Yuantus frontend; API/CLI only); multi-kid (V1.2-embed-gated).
