# DEV & VERIFICATION — PLM-Collab V2 Seats S2 + ungated in-repo slices

**Date:** 2026-06-21
**Companion to:** `DEV_AND_VERIFICATION_V2_SEATS_S1_LICENSE_PROJECTION_20260620.md` (S1) and the
design of record `plm-collab-v2-seats-design-20260619.md` (Option A, owner-ratified).

This records **S2** of the seats ladder plus the **ungated, in-repo Yuantus slices** built in the
"complete the remaining development (per the plan)" push. Scope was owner-chosen: *ungated in-repo
slices, in parallel*. Gated / cross-repo / owner-decision items were deliberately **not** built and
are listed with their gates in §5 — that deferral *is* how this push honours "根据计划".

---

## 1. Seats ladder status (design → shipped)

| Stage | What | State |
|---|---|---|
| S0 | design + A-vs-B2 decision (Option A) | #813 MERGED |
| S1 | license `seats` → identity `TenantQuota.max_users` at import; `is_entitled()` seats-free; default-off via `QUOTA_MODE` | #817 MERGED (`80859d6b`) |
| S1+ | signer enforces `--seats >= 1` at mint; "omit = no projection / cap unchanged" operator wording | merged with S1 |
| **S2** | source-of-truth handshake + cap-projection audit (this doc) | **#820 OPEN, green** |

S1 detail is in the S1 companion doc and is not repeated here.

## 2. S2 + this push — what shipped

**S2 / #820** (`feat(entitlement): V2 seats S2 …`)
- `import_license` returns `LicenseImportResult(activated, payload, tenant_id)`; the CLI projects the
  cap from the **verified** `result.payload`, deleting the raw, unverified `license_obj["payload"]`
  re-read.
- `record_seat_cap_audit`: a **meta-side** `AuditLog` row on projection — meta-side because
  `audit_logs` is not guaranteed in the identity DB under `SCHEMA_MODE=migrations`, and a failed audit
  there would roll back the cap. Best-effort, in its own guard, decoupled from the cap write.
- **Phantom-audit fix:** `projected` is assigned only **after** the identity `with`-block commits, so a
  commit failure cannot leave a truthy `projected` → no audit for a cap that rolled back.
- **[P3]:** the audit records the **normalized** tenant (`result.tenant_id`), matching activation /
  projection (a padded `" acme "` never becomes a divergent audit key).
- **Slice A:** a **two-DB CLI integration test** (`license import` → activate (meta) + project
  (identity) + seat-cap audit under the normalized tenant; no-seats → no projection / no audit) —
  closes the orchestration coverage boundary #820 had named.

**Slice C / #823** (`feat(entitlement): yuantus license status …`)
- `yuantus license status --tenant-id <tenant>`: a **read-only** support-bundle view of a tenant's SKU
  entitlement (decided **only** via the centralized `is_entitled`) + a **whitelisted** license summary
  (`app_name / status / expires / plan / key`) — never the raw `license_data` or any key material.
- **Slice B:** the existing V1 controlled-pilot runbook already covers Phase 1 Slice 1A; this adds the
  `yuantus license status` acceptance step (a direct, no-HTTP entitlement check) to §6.1.

## 3. Verification evidence

**#820 (S2 + slice A + [P3])** — head `eb42dad9`:
- `contracts` PASS (9m16s): **1801 passed, 1 skipped** (1798 at the S2 baseline → +3: the two new
  two-DB integration tests + the [P3] normalization test). `test_license_import.py` is in the contracts
  explicit list, so these executed **in CI**, not only locally.
- `regression` PASS (4m9s); `playwright-esign`, `plugin-tests`, `detect_changes` PASS.
- Local: 18 passed no-DB (`test_license_import.py`).

**#823 (slice C + B)** — head `9b7f8a59`:
- Local: `test_license_status.py` **4 passed** no-DB (entitled / tenant-scoped / whitelist /
  tenant-normalization). End-to-end CLI smoke: `license status` lists `bom_multitable: ENTITLED` plus
  the active license, with no `license_data` / key leak.
- CI: _pending at time of writing; the test file is registered in the conftest no-DB allowlist + the
  ci.yml contracts path-filter + the explicit pytest list._

## 4. Invariants held

- `is_entitled()` is untouched and remains the **sole** entitlement gate (slice C routes through it and
  adds no second auth path).
- `QUOTA_MODE` stays default-`disabled` → seats ship inert until a deployment opts in.
- No key / `license_data` leak; entitlement stays centralized.
- The only meta↔identity hop is the import-time projection write; no cross-DB hot-path join.

## 5. Deferred — with gates (per the plan; NOT built)

The seats design §5 and the upgrade taskbook (`plm-collaboration-upgrade-development-todo-20260618.md`,
which "authorizes no implementation; each slice requires an explicit owner opt-in and its own PR") gate
the remainder:

- **D — provider pact fixtures (Phase 2 2A):** pact is **consumer-driven** (the provider verifier
  validates consumer-authored `*.json` pacts); the new interactions must be authored in the MetaSheet2
  consumer (2B) first → **cross-repo, owner-gated**. Provider-only fixtures cannot land in-repo alone.
- **B2 / user↔feature assignment subsystem:** only if the product sells **independent SKU seat packs**
  (owner decision, §11). The B1 "per-SKU limit over a tenant-wide count" remains the named **trap**.
- **multi-kid key rotation:** V1.2-embed-gated.
- **MetaSheet-consumer seat reconciliation:** cross-service, V1.2-embed era, advisory only.
- **Vendor-side license issuance CLI:** an out-of-repo **private** tool (Phase 4); never in the shipped
  repo.
- **In-PLM embed host (Phase 3), approval automation (Phase 5), SSO (Phase 6), write-back (Phase 7):**
  separate owner-gated product lines.
- **§11 owner decisions** (SKU packaging, pilot customer, signing-key custody, SSO timing, …) remain
  open.

## 6. Coverage boundary (honest)

Slice A makes the `cli.py` `license import` orchestration **integration-tested** end-to-end (the glue
was previously read / smoke / compile-verified only). The `license status` command's helper is
unit-tested and the command is smoke-verified; the remaining thin CLI formatting is consistent with the
repo's existing posture.

---

*PRs: #820 (S2 + slice A + [P3]), #823 (slice C + B). Design: `plm-collab-v2-seats-design-20260619.md`.
S1: `DEV_AND_VERIFICATION_V2_SEATS_S1_LICENSE_PROJECTION_20260620.md`.*
