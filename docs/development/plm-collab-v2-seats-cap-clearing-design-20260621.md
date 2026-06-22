# PLM-Collab V2 — Seats: explicit cap clearing / lowering (design, docs-first)

**Status:** ✅ **Implemented (#836)** — Option 2.1 (`seats: null` clears `TenantQuota.max_users` →
unlimited). This doc was docs-first (the choice changed **commercial / ops semantics**, so it was
decided before code); the owner chose 2.1 and #836 shipped it. Parked item from
`plm-collab-v2-seats-design-20260619.md` §5 and the #819 archive; surfaced by the #817 [P2] review.

## 1. The gap (and the part that already works)

Today `project_license_seats` (security/auth/seat_projection.py) is a **no-op** on absent or
invalid `seats`: it *raises or overwrites* `TenantQuota.max_users` from a valid positive
`seats`, but it **never clears** an existing cap.

- **Lowering to a smaller positive cap already works** — the license is the source of truth, and
  a re-import with a smaller `seats` overwrites `max_users` (idempotent upsert). No new mechanism
  needed for "20 → 10".
- **The real gap is *clearing* (un-cap → unlimited).** Once a seat-bearing license has set
  `max_users = N`, there is no payload that returns the tenant to "no cap": omitting `seats` is a
  no-op (it preserves N), and `seats < 1` is rejected. So "remove the seat limit entirely" has no
  expression.

The design question is therefore narrowly: **how does an operator/vendor express "clear the seat
cap"**, given `max_users = NULL` is the model's "unlimited" (per the seats design §7 invariant).

## 2. Options

### 2.1 — Explicit `seats: null` in the signed payload → clear
Distinguish **absent** `seats` (legacy / no-op, backward-compatible) from an **explicit `null`**
(clear → set `max_users = NULL`). Requires the payload schema + signer + projection to tell
"key absent" from "key present and null" (a JSON `null`, not a missing key).
- **Pro:** the license stays the single source of truth; "clear" is a vendor-signed, tamper-evident
  act, auditable like any other license state.
- **Con:** a license that *says* "unlimited seats" is a real **commercial object** that must exist
  (an unlimited-seat SKU / entitlement). Needs the absent-vs-null distinction plumbed through
  `canonical_payload_bytes` + verification (subtle: signature must cover the explicit null).

### 2.2 — `seats: 0` as an "un-cap" sentinel
Repurpose `0` to mean "clear".
- **Con (disqualifying as-is):** `0` is currently a **hard-rejected invalid value** in *both* the
  signer (`_seats_arg`/`build_and_sign`) and the projection (fail-open no-op), precisely so an
  operator can't mint a self-verifying-but-inert license (the #817 [P2] fix). Re-purposing `0` as a
  meaningful "uncap" directly reverses that contract and is easy to confuse with "zero seats =
  lock everyone out." **Not recommended.**

### 2.3 — A separate admin action (CLI/API) to set/clear `max_users`
Decouple cap management from the license: an admin endpoint sets or clears `TenantQuota.max_users`
directly (it is identity-side state already).
- **Pro:** clean for **operational** overrides (support lifts a cap without re-issuing a license);
  no payload/signature change.
- **Con:** **dual source of truth** — license vs admin. Must define precedence: does the next
  license re-import re-assert its cap and clobber the admin clear? (Per the seats design's Option-A
  tradeoff, re-import *does* overwrite, so an admin clear is transient unless the license is also
  reissued.) Risk of drift between "what was sold" and "what is enforced."

## 3. The deciding axis (the owner call)

The choice hinges on **what "uncap" *is***:

- If **uncap is a commercial state** ("this tenant bought unlimited seats") → it belongs in the
  **license** → **2.1** (`seats: null`).
- If **uncap is an ops override** ("temporarily lift enforcement during an incident / migration")
  → it belongs in an **admin action** → **2.3**, with an explicit precedence rule vs. the next
  re-import.

These are not mutually exclusive — 2.1 (commercial) and 2.3 (ops) can coexist if precedence is
defined (license re-import wins; admin clear is an interim ops lever). What must **not** ship is
2.2.

## 4. Recommendation

1. **Do not** repurpose `seats: 0` (keep the #817 invalid-seats contract intact).
2. **Default recommendation: 2.1 (`seats: null` = clear)** *if* an unlimited-seat entitlement is a
   real SKU — it keeps the license authoritative and tamper-evident, matching every other seat
   semantic. Scope: payload absent-vs-null plumbing + signer flag + a projection branch that sets
   `max_users = NULL`; tests mirror the existing seat_projection suite (explicit-null clears; absent
   still no-ops; lowering via re-import unchanged).
3. **Add 2.3 only if** ops needs a license-independent lever, and only with a written precedence
   rule (re-import re-asserts the license cap).
4. ✅ **Shipped (#836):** 2.1 implemented — explicit `seats: null` clears `max_users` (→ unlimited);
   **absent** stays a no-op; `seats: 0` stays illegal; the dogfood signer gained `--clear-seats`
   (mutually exclusive with `--seats`); the audit records `max_users=cleared` (never a false `=N`).

## 5. Non-goals / already-settled

- **Lowering** to a smaller positive cap — already works via re-import (overwrite). Not in scope.
- **Enforcement** — unchanged; `QUOTA_MODE`-gated, `is_entitled()` stays seats-free.
- **Vendor issuance tool** — minting a `seats: null` license still touches the vendor-private
  issuance tool (out of clean in-repo scope), same caveat as the seats DEV/V doc.
