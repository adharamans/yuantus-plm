# PLM-Collab V2 — Seats (per-license user limit): design for review

**Status:** ✅ **Implemented — Option A.** Design ratified (S0 · #813); the import-time
seat-cap projection landed (#817: `seat_projection.py`, best-effort `yuantus license import`
projection, dogfood signer `--seats`, `test_seat_projection.py`). `TenantQuota.max_users` is the
identity-side enforcement cache; `is_entitled()` stays seats-free. **Still future:** B2 assignment
subsystem, MetaSheet-consumer reconciliation (both §5), and the
**vendor-private license issuance tool** (the dogfood signer is a dev stand-in, not production
minting). The original design text below is preserved as the record.

**DECISION (2026-06-19, owner-ratified): Option A.** The paid seat cap is sourced from
the license and projected at **import time** into the identity-side
`TenantQuota.max_users`; `is_entitled()` stays seats-free. **No** new per-SKU quota
(that is the deferred B2 / assignment subsystem, §4 — explicitly not built now to avoid
the B1 masquerade). S1 builds this projection.

**Scope.** How a "seats" cap (a limit on how many users a paid feature covers) is
*modeled, counted, and enforced* for the collab SKU(s). Deliberately small and
in-repo. Out of scope, with reasons, in §5.

---

## 1. Key finding: seats is an EXTENSION of an existing spine, not greenfield

Yuantus already ships a tenant quota framework that does almost exactly what the
seats principles ask for. Grounding:

- **`TenantQuota`** — `src/yuantus/security/auth/models.py` (table `auth_tenant_quotas`,
  PK `tenant_id`, in the **identity DB**). Columns: `max_users`, `max_orgs`,
  `max_files`, `max_storage_bytes`, `max_active_jobs`, `max_processing_jobs` — all
  nullable, where **null = unlimited**.
- **`QuotaService(identity_db, *, meta_db=None)`** — `src/yuantus/security/auth/quota_service.py`:
  - `get_usage(tenant_id)` (L81–87) counts **active users** for the tenant:
    `count(AuthUser.id) where tenant_id == ? and is_active`.
  - `evaluate(tenant_id, deltas)` (L138–170) is **resource-name driven**: for each
    `resource` in the delta it compares `usage.<resource>` against
    `quota.max_<resource>`. Adding a new quota dimension = add `max_<x>` to
    `TenantQuota` + `<x>` to `QuotaUsage` + a count query. No new service.
  - `raise_if_exceeded(...)` (L172–178) exists as a helper, **but it is not the seam
    provisioning uses** (see next bullet) — don't wire seats to it by reflex.
  - **`QUOTA_MODE`** (L55, L197–201): `disabled | soft | enforce`, default
    `disabled` → the whole framework **ships off** until a deployment opts in.
- **It is already enforced at provisioning**, not at a feature gate. The real seam is
  in `src/yuantus/api/routers/admin.py`: `create_user` calls
  **`_apply_quota_limits(quota_service, identity.tenant_id, {"users": 1}, response)`**
  *before* `AuthService.create_user(...)`. That helper (`_apply_quota_limits`,
  L406–423) runs `quota_service.evaluate(tenant_id, deltas=...)` and then, by
  `QUOTA_MODE`: under `soft` appends a warning header
  (`append_quota_warning(...)`); under `enforce` raises
  `HTTPException(429, {"code": "QUOTA_EXCEEDED", ...})`.

This spine already embodies all four principles in the brief (enforce at
provisioning; `is_entitled()` untouched; cross-DB aware; extensible). **Seats =
one more resource dimension on this spine** — *if* the granularity is coherent (§2/§4).

---

## 2. The four questions, answered against the real code

### Q1 — License model: `seat_limit` on `AppLicense`, or an independent limits table?

The "independent limits table" **already exists** (`TenantQuota`, identity DB).
But the license (`AppLicense`, table `meta_app_licenses`) lives in the **meta DB**
(`src/yuantus/meta_engine/app_framework/store_models.py`). So:

- The **license stays the commercial source of truth** (vendor-signed; eventually
  carries a `seats` number — see §5/S2).
- The **enforceable limit lives identity-side** (on/near `TenantQuota`), in the same
  DB as the user count.
- Do **not** make the enforced number live *only* on `AppLicense`: that splits the
  limit (meta DB) from the count (identity DB) across the engine boundary — exactly
  the cross-DB counting hazard the brief warns about.

Which identity-side shape (tenant-wide vs per-SKU) is the open fork: **§4**.

### Q2 — Count source: the principle that decides everything

**Established source = identity active users** (`get_usage().users`). Same-DB as the
limit, already the basis of `max_users`, battle-tested.

**Principle: limit granularity MUST match count granularity — otherwise the limit is
a masquerade.** A per-SKU *limit* enforced against a *tenant-wide* count is **not**
per-SKU seats. Worked example:

> A tenant has **100** regular PLM users and buys only **5** BOM-Review seats. If
> that 5-seat cap is enforced against the tenant-wide active-user count, creating
> the **6th** user is blocked by the BOM cap — *even though that user never touches
> BOM*. With several purchased SKUs, the binding constraint is `min(purchased SKU
> caps)` applied to the **whole tenant headcount**: the *smallest* SKU cap throttles
> everyone.

So per-SKU dimensions over a tenant-wide count do **not** sell independently — they
only cohere as a **site-license** reading (each purchased SKU covers *all* active
users). The three resulting count sources:

- **identity active users** → coheres only with a **tenant-wide** limit (§4 A), or
  as a site-license per-SKU reading (§4 B1, the trap).
- **feature-assigned users** → the count source for **true** per-SKU seats ("5 BOM
  seats = 5 specific people"); requires a **user↔feature assignment table** — a new
  subsystem (§4 B2 / §5).
- **provisioned MetaSheet users** → consumer-side (the metasheet2 service), across
  the integration boundary; the provider cannot authoritatively count/enforce on the
  consumer's headcount. Defer to the V1.2-embed era (advisory at best).

### Q3 — Enforcement point (derived from Q2, not a free choice)

Because the count source is **account existence** (an active `AuthUser`), a seat is
**consumed when the account is provisioned** — an account that never logs in still
holds a seat. Therefore the enforcement seam is **provisioning**, *by construction*:

- Enforce at the **provisioning seam**, reusing the same
  `_apply_quota_limits(quota_service, tenant_id, {"<seat_resource>": 1}, response)`
  helper that gates `max_users` today in `create_user` (→ `evaluate()` → soft warning
  header / enforce 429). (For B2, the seam is **assignment**, not generic user
  creation — see §4.)
- **Login is the wrong seam** for a headcount model: the seat is already spent before
  first login; login enforcement would only fit a *session/concurrency* model the
  brief did not ask for. At most a **soft advisory** at login, never a hard block.
- **Never** per-request feature-gate counting.

### Q4 — `is_entitled()` stays a pure feature check

No change. `is_entitled()` (`entitlement_service.py` L67–107) answers "does this
tenant hold an active, in-window license for the feature?" — one indexed query on
`meta_app_licenses` scoped by `resolve_license_scope()` (`license_scope.py` L16–37).
Seats are a **separate** call at the provisioning/assignment seam. The two code paths
never touch; seats add **zero** to the entitlement hot path.

---

## 3. Cross-DB boundary — resolved (the strongest property of this design)

`AuthUser`/`TenantQuota` live in the **identity DB**
(`IDENTITY_DATABASE_URL or DATABASE_URL`, `auth/database.py` L20–22); `AppLicense`
lives in the **meta DB** (`DATABASE_URL`). Default: same URL → same DB. Split: set
`IDENTITY_DATABASE_URL`.

- **Enforcement is single-DB.** Both the limit and the count sit identity-side, so a
  seat check at provisioning needs only the **identity session** — identical to
  today's `max_users` check (`QuotaService(db)` in `create_user`, `db` = identity).
- **The boundary is crossed exactly once: at license import.** Projecting the
  license's seat number → identity-side limit is one controlled, infrequent
  **write**, never a hot-path join. (S2; see §5 on its coupling to issuance.)
- The design holds in **both topologies** (same-DB and split) because enforcement
  never joins meta↔identity.

---

## 4. The decision that gates the build: A vs B2 (and the B1 trap)

Per §2's granularity principle, only two models are coherent — plus one trap:

|  | **A — tenant-wide cap** | **B1 — per-SKU limit over tenant count** *(TRAP)* | **B2 — true per-SKU seats** |
|--|--|--|--|
| Limit | one paid cap on the tenant (reuse `max_users`) | `max_<sku>_users` per SKU | `max_<sku>_users` per SKU |
| Count | tenant active users | tenant active users (**mismatch**) | **assigned** users per SKU |
| Granularity | matched ✓ | **mismatched** ✗ | matched ✓ |
| Real behavior | total tenant headcount cap | `min(purchased caps)` throttles the **whole tenant** | each SKU's roster capped independently |
| Sells "BOM 5 / Collab 20" independently? | no (one cap) | **no — it only looks like it does** | yes |
| Build cost | ~none: mechanism already exists, just source the cap | small, but **commercially wrong / misleading** | new **assignment subsystem** (model + lifecycle + count source + enforce-at-assign) |

**Recommendation — honest, not over-engineered:**

- **If a single tenant-wide paid cap is acceptable now → A.** It is fully consistent
  with the existing `max_users` enforcement — the mechanism is *already built*; the
  only new work is sourcing the cap from the paid entitlement (folds into S2). It does
  **not** masquerade as per-SKU seats.
- **If the product must sell independent SKU seat packs ("BOM 5 / Collab 20") → B2**,
  and then the **assignment subsystem is promoted to a first-class design object** —
  it *is* the seats design, not a deferred footnote.
- **Do not ship B1.** A per-SKU dimension over a tenant-wide count is the failure mode
  S1 can accidentally become: it reads as per-SKU but enforces a global minimum cap.
  If you build per-SKU *limits*, you must also build per-SKU *counting* (B2) — there
  is no tenant-wide-count shortcut to independent SKU seats.

> My lean matches the reviewer's: take **A** as the first paid-seat cut unless
> independent SKU seat packs are a hard product requirement — in which case go
> straight to **B2** (assignment-first) and don't approximate it with B1.

---

## 5. Deferred / conditional — named, with reasons (not silently dropped)

- **User↔feature assignment subsystem** (= §4 B2): a new model + lifecycle + count
  source + admin surface. **Deferred under A**; **promoted to the first-class seats
  design under B2.** It is the *only* way to sell SKU seat packs independently — there
  is no tenant-wide-count shortcut (that is the B1 trap).
- **License `seats` payload + import-time projection** — ✅ **projection landed (#817):**
  `project_license_seats` lands the signed payload's `seats` onto `TenantQuota.max_users` at
  `yuantus license import` (best-effort, `QUOTA_MODE`-gated). **Still future:** minting a
  seat-bearing license via the **vendor-private issuance tool** (out of clean in-repo scope; the
  dogfood signer `sign_dogfood_license.py --seats` is the dev stand-in, not production minting).
- **MetaSheet-consumer seat reconciliation**: cross-service, V1.2-embed-era, advisory.
- **Admin seat UX**: no Yuantus frontend exists; surface limits via API/CLI only.
- **multi-kid**: V1.2-embed-gated, unchanged from the prior ladder note.
- **Explicit seat-cap clearing / lowering — ✅ Shipped (#836).** Resolved as the **`seats: null`**
  option (design: `plm-collab-v2-seats-cap-clearing-design-20260621.md`): an explicit `seats: null`
  in the signed payload clears `max_users` (→ unlimited), distinct from **absent** (still a no-op)
  and `seats: 0` (still illegal); the dogfood signer gained `--clear-seats`. **Lowering** to a
  smaller positive cap already worked via re-import. *(Provenance: #817 [P2] review; built #836.)*

---

## 6. Build plan (after this review; branches on the §4 decision)

- **S0 — this doc.** ✅ **done (#813).** Design + the **A vs B2** decision (Option A ratified).
- **S1 — ✅ Option A shipped (#817)** (the B2 reading below was *not* taken — it stays deferred, §5); both ship default-off via `QUOTA_MODE`:
  - **Under A:** almost nothing new — `max_users` is *already* enforced at the
    `_apply_quota_limits(..., {"users": 1}, response)` seam. The "paid seat cut" is
    just making that cap's *source* the paid entitlement (folds into S2). Tests:
    default-off, soft-vs-enforce, null = unlimited (mostly already covered).
  - **Under B2:** S1 *is* the assignment subsystem — a `user↔feature` table, its
    write/lifecycle, the per-SKU **assigned-user** count source, and enforcement at
    **assignment** time (not generic user creation). This is the real work; scope it
    as such, not as a `TenantQuota` column.
- **S2 — source-of-truth handshake: ✅ projection landed (#817).** Project the license seat
  number → identity-side limit at import. The **vendor-private issuance tool** it sequences with
  is **still future** (the dogfood signer is the dev stand-in).
- **S3 — deferred refinements.** Consumer reconciliation; (under A) the assignment
  subsystem if sub-tenant seats later become required.

---

## 7. Invariants to carry forward

1. `is_entitled()` stays a pure feature check — seats never enter the entitlement hot path.
2. Enforce **only** at provisioning (A) / assignment (B2); default-off via `QUOTA_MODE`; null limit = unlimited.
3. License = commercial source of truth; the identity-side limit is the enforcement
   cache, in the **same DB as the count**.
4. No cross-DB join on any hot path; the **only** cross-boundary hop is the
   import-time projection write (S2).
5. **Limit granularity must match count granularity.** Per-SKU limits require per-SKU
   (assignment-based) counting, or they collapse to a global minimum cap — the B1
   trap. Never label a tenant-wide-count cap as per-SKU seats.
