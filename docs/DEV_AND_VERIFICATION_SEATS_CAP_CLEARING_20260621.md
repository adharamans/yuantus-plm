# DEV & VERIFICATION — Seats cap clearing (explicit `seats:null` → clear)

Date: 2026-06-21 · Branch `claude/seats-cap-clearing` · base `origin/main`.
Implements the docs-first design `docs/development/plm-collab-v2-seats-cap-clearing-design-20260621.md`
(#830) — **Option 2.1 (`seats: null`)**, owner-greenlit. Closes the gap the #817 [P2] left: a
seat-bearing license could *set/raise* a cap but never **clear** it.

## 1. Summary

A vendor-signed license payload's `seats` clause now has **three** meanings, not two:

| payload | outcome | `TenantQuota.max_users` |
|---|---|---|
| **absent** (no `seats` key) | `noop` (backward-compatible) | unchanged (prior cap preserved) |
| explicit **`seats: null`** | `clear` | → **NULL** (unlimited) |
| **positive int** | `set` | → that int |
| `0` / negative / bool / non-int | `noop` (fail-open) | unchanged |

`seats: 0` stays **illegal** (rejected at mint, no-op at import) — unchanged from #817.

## 2. What changed

- **`security/auth/seat_projection.py`** — `project_license_seats` now returns a
  `SeatProjectionOutcome(action, seats)` (`"noop"|"set"|"clear"`). It keys on **`"seats" in
  payload`** to tell **absent** (no-op) from **explicit null** (clear) — `payload.get` cannot.
  Clear routes through the same `ensure_tenant` + `upsert_quota({"max_users": None})` as set, so it
  shares the identical fail-open / raise-on-DB-failure contract.
- **`cli.py`** — branches the operator echo + the audit on `outcome.action` (set vs clear vs noop);
  the post-commit ordering is unchanged, so a *failed* clear leaves `projected = None` and writes
  **no** audit.
- **`license_import_service.record_seat_cap_audit`** — `max_users: Optional[int]`; `None` records
  `?max_users=cleared` (never a false `=N`).
- **`scripts/dev/sign_dogfood_license.py`** — `--clear-seats` emits an explicit `seats: null`
  (part of the signed canonical payload). Mutually exclusive with `--seats` at **both** the
  argparse layer (`add_mutually_exclusive_group`) and inside `build_and_sign` (direct callers /
  tests bypass argparse).

## 3. Design notes (the adversarial-review surface)

- **absent vs null** — the only safe discriminator is `"seats" in payload`; `payload.get("seats")`
  returns `None` for both. Verified by `test_absent_vs_null_are_distinct`.
- **Signature covers the null.** `canonical_payload_bytes` is `json.dumps(sort_keys=True)`, so
  `{"seats": null}` is signed deterministically and **distinct** from absent. The round-trip is
  asserted, not inferred: the signer test signs `--clear-seats` then `verify_license`-s it (sign
  side), and an import test asserts `result.payload["seats"] is None` after verification (import
  side).
- **Audit truthfulness** — a clear writes `max_users=cleared`, so the trail never claims a numeric
  cap that was not set (`test_record_seat_cap_audit_clear_records_cleared_not_a_number`). A clear
  whose identity commit fails writes no audit at all (CLI ordering, unchanged).
- **Clear with no prior quota** — a deliberate, documented, tested choice: `upsert_quota` creates a
  `TenantQuota` row with `max_users=None` (= unlimited = the default). Harmless, and symmetric with
  the set path, which also creates the row (`test_clear_with_no_prior_quota_creates_unlimited_row`).
- **Fail-open unchanged** — clear adds the same identity-DB touch as set; the CLI's best-effort
  guard treats a raised clear identically (license stays active, retry re-projects).

## 4. Verification

- **Projection** — set (`action=="set"`, max_users=N); absent → noop (cap preserved); explicit
  null → clear (max_users→None); absent-vs-null distinct; invalid (0/neg/bool/str/float) → noop;
  clear-with-no-prior-quota; re-import source-of-truth; FK enforced; provisioning enforcement.
- **Signer** — `--clear-seats` emits signed explicit-null + round-trips through `verify_license`;
  `seats` + `clear_seats` together → `ValueError`; argparse `--seats --clear-seats` → non-zero
  exit, no file; the existing `--seats >= 1` guards unchanged.
- **Audit/CLI** — clear records `max_users=cleared`; set still records `max_users=N`; import
  preserves explicit `seats: null` in `result.payload`.
- Local: `py_compile` clean (full suite via CI — system python 3.9 vs codebase 3.10+).

## 5. Out of scope

- The rejected design alternatives (`seats: 0` sentinel; a separate admin action) — see the design
  doc §2/§4. **Lowering** to a smaller positive cap already works via re-import (overwrite).
- Enforcement (`QUOTA_MODE`-gated, unchanged); `is_entitled()` stays seats-free.
- Minting a real `seats: null` license via the **vendor-private** issuance tool (the dogfood
  signer is the dev stand-in).
