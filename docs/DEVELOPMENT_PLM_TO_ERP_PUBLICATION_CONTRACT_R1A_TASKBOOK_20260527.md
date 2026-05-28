# Claude Taskbook: PLM→ERP Publication Contract — R1-A (Contract Lock)

Date: 2026-05-27

Type: **Doc-only taskbook (contract lock).** It pins the PLM→ERP
publication-readiness contract — eligibility formula, the concrete esign
sign-off-complete predicate, the `blocking_reasons` taxonomy, the minimal
response payload schema (with field→source mapping), the parameter contract, and
the R1-B API boundary / router-registration / exception-chaining / test catalog.
It changes no code. **Merging this taskbook does NOT authorize the R1-B
implementation** — that requires its own explicit opt-in.

Parent: `DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_PLAN_20260527.md` (#663,
`ec7daa87`). Baseline `main = ec7daa87`.

## 1. Scope

Lock the contract for `GET /api/v1/plm-erp/items/{item_id}/publication-readiness`.
It is an **outbound** readiness verdict over **existing** verdicts — it
**wraps** `ReleaseReadinessService.get_item_release_readiness()` +
`latest_released_guard` + `suspended_guard`; it does **not** re-derive MBOM /
routing / baseline readiness and introduces **no** fourth readiness notion.

## 2. Eligibility (fixed)

```
eligible =
      latest_released_guard passes
  AND suspended_guard passes
  AND get_item_release_readiness(...).summary.ok == True
  AND esign_ok        (see §3)
```

Grounded fields (`services/release_readiness_service.py`):
`summary{ok, error_count, warning_count, resources, ok_resources, by_kind}`,
`resources[]{kind ∈ {mbom_release, routing_release, baseline_release}, errors,
warnings, …}`, separate `esign_manifest`.

## 3. esign sign-off-complete predicate (PINNED — reuses existing release semantics)

Grounded against `esign/service.py` `get_manifest_status` (returns `None`, or a
dict `{manifest_id, item_id, generation, is_complete, completed_at,
requirements}`) and the existing release-orchestration semantics in
`web/release_orchestration_router.py` `_plan_steps`:

```
esign_incomplete =
      isinstance(esign_manifest, dict)
  AND "is_complete" in esign_manifest
  AND not bool(esign_manifest["is_complete"])

esign_ok = not esign_incomplete
```

This **mirrors `_plan_steps` exactly** — do not substitute `status ==
"completed"` (there is **no** `status` field; the real field is `is_complete`),
and do not use a bare `esign_manifest["is_complete"]` index (a manifest without
that key must not raise). Consequences, identical to existing release semantics:

- `esign_manifest is None` (no manifest) → **does NOT block**.
- dict with `is_complete == false` → **blocks** (`esign` reason).
- dict without `is_complete` → does **not** block.

**Default decision (R1-A): reuse the above existing semantics** (missing manifest
does not block). If the product wants **"no manifest also blocks"**, that is a
**NEW semantic** and R1-A must (a) state it explicitly as new, (b) not present it
as "reusing release semantics", and (c) keep `esign` in `blocking_reasons`. The
default keeps `esign` in the taxonomy for the `is_complete == false` case.

## 4. blocking_reasons taxonomy (only from existing verdicts)

| reason | source |
|---|---|
| `not_latest_released` | `latest_released_guard` fails |
| `suspended` | `suspended_guard` fails (item/version suspended) |
| `mbom_release` | a `resources[]` entry `kind == "mbom_release"` with non-empty `errors` |
| `routing_release` | a `resources[]` entry `kind == "routing_release"` with non-empty `errors` |
| `baseline_release` | a `resources[]` entry `kind == "baseline_release"` with non-empty `errors` |
| `esign` | `esign_incomplete` per §3 |

No reason originates outside these existing verdicts.

## 5. Minimal payload schema (field → type → source → mapping)

`publication-readiness` response:

| field | type | source | block / hint |
|---|---|---|---|
| `item` | obj | `{ item_id: request path, lifecycle_state: Item.state }` | informational |
| `version` | obj | the item's current version `Item.current_version` (via `Item.current_version_id`, `models/item.py:52,102`) — see the version sub-schema below | `is_released` informs the adapter; gating is via §2 guards, not this block |
| `eligible` | bool | §2 formula | the verdict |
| `generated_at` | datetime | `get_item_release_readiness().generated_at` | — |
| `ruleset_id` | str | echoed request param (§6) | — |
| `limits` | obj | echoed `{mbom_limit, routing_limit, baseline_limit}` | — |
| `summary` | obj | `get_item_release_readiness().summary` verbatim (`ok/error_count/warning_count/resources/ok_resources/by_kind`) | `summary.error_count>0` ⇒ block |
| `resources[]` | list | `get_item_release_readiness().resources` verbatim (`kind/resource_type/resource_id/name/state/errors/warnings`) | per-entry `errors` ⇒ block (`§4`), `warnings` ⇒ hint |
| `esign` | obj | mapped from `esign_manifest`: `{present: bool (manifest is not None), is_complete: bool\|null, completed_at: str\|null}` | `esign_incomplete` (§3) ⇒ block |
| `file_refs[]` | list | the `version{}` version's `version_files` (see below) | informational (publication package contents) |
| `blocking_reasons[]` | list | derived per §4: `[{reason, detail}]` | the block list |

**`version{}` sub-schema** — sourced from `Item.current_version` (via
`Item.current_version_id`); fields are `ItemVersion` columns
(`version/models.py`). Note `version.generation` is **`ItemVersion.generation`**,
which is **distinct from `Item.generation`** — the payload carries the *version*
generation, not the item one:

| version field | source column |
|---|---|
| `version_id` | `ItemVersion.id` |
| `generation` | `ItemVersion.generation` (NOT `Item.generation`) |
| `revision` | `ItemVersion.revision` |
| `version_label` | `ItemVersion.version_label` |
| `state` | `ItemVersion.state` (version lifecycle state) |
| `is_current` | `ItemVersion.is_current` |
| `is_released` | `ItemVersion.is_released` |
| `released_at` | `ItemVersion.released_at` |
| `primary_file_id` | `ItemVersion.primary_file_id` |

**`file_refs[]`** is **not** vague — each entry is exactly
(`version/models.py` `VersionFile`):

| file_refs field | source column |
|---|---|
| `file_id` | `VersionFile.file_id` |
| `file_role` | `VersionFile.file_role` (native_cad / preview / geometry / attachment / …) |
| `is_primary` | `VersionFile.is_primary` (and/or `== ItemVersion.primary_file_id`) |
| `sequence` | `VersionFile.sequence` |
| `snapshot_path` | `VersionFile.snapshot_path` |

`file_refs` comes **only from the `version{}` version's `version_files`**
relationship (the `Item.current_version`); `ItemVersion.primary_file_id`
identifies the primary file. R1-A pins this source; R1-B reads it (no new file
model).

## 6. Parameters (explicit, defaulted, echoed)

| param | default | passed to |
|---|---|---|
| `ruleset_id` | `readiness` | `get_item_release_readiness(ruleset_id=…)` |
| `mbom_limit` | `20` | `get_item_release_readiness(mbom_limit=…)` |
| `routing_limit` | `20` | `get_item_release_readiness(routing_limit=…)` |
| `baseline_limit` | `20` | `get_item_release_readiness(baseline_limit=…)` |

Defaults and any query override are **echoed into the response** (`ruleset_id` +
`limits`); R1 introduces **no** tenant-level ruleset config (silent hardcoding is
forbidden).

## 7. R1-B API boundary (for the next, separately-opted slice)

- `GET /api/v1/plm-erp/items/{item_id}/publication-readiness` (read-only).
- (later, separable) `GET /api/v1/plm-erp/items/{item_id}/publication/export`.
- **Router registration**: R1-B re-verifies the registration point at start of
  work (`api/app.py` `include_router` vs an existing registry) — route evidence
  spans `api/routers/` and `meta_engine/web/`.
- **Exception-chaining**: all `ValueError` / service-validation map to
  `HTTPException(...) from exc`, per the repo contract (`test_app_router_exception_chaining`
  + the `*_router_exception_chaining` family).
- No external side effects (read-only); no purchase/sale; no real-ERP; no Odoo
  runtime dependency.

## 8. R1-B test catalog (names + assertion shape)

R1-B must cover:

- `not latest released → blocked` (`blocking_reasons` contains `not_latest_released`).
- `suspended → blocked` (`suspended`).
- `readiness resource errors → blocked` (`mbom_release` / `routing_release` /
  `baseline_release` as applicable).
- `esign incomplete → blocked` (dict `is_complete == false`).
- `esign manifest None → not blocked by esign` (existing semantics, §3).
- `readiness warnings only → eligible with warnings` (no errors; warnings surfaced).
- `ruleset_id / limits passed through` to `get_item_release_readiness` and echoed.
- `unknown ruleset ValueError → chained HTTPException`.
- `version{} reflects Item.current_version` (version_id/generation/revision/version_label/state/is_current/is_released/released_at/primary_file_id; `version.generation` is `ItemVersion.generation`, not `Item.generation`).
- `file_refs sourced from the version's version_files` (file_id/file_role/is_primary/sequence/snapshot_path).
- `no external ERP HTTP / write side effect`.
- `response contains no purchase/sale transaction`.

## 9. Non-Goals

No purchase/sale order creation; no real-ERP connection; no Odoo runtime
dependency; no GPL/AGPL reuse; no bypass of latest-released / suspended /
release-readiness; no new readiness derivation; no R1-B implementation (separate
opt-in); no R2 adapter.

## 10. Guard surface R1-B must add (enumerated here, added in R1-B)

- A behavioral guard that the publication path **always** routes the verdict
  through `latest_released_guard` + `suspended_guard` + `summary.ok` + §3 esign
  (cannot emit `eligible=true` for a non-latest / suspended / not-ready / esign-
  incomplete item).
- The exception-chaining contract for the new router (§7).
- doc-contract: the R1-B DEV/verification doc indexed + sorted.

## 11. Preconditions to enter R1-B

1. §2 eligibility + §3 esign predicate ratified;
2. §5 payload schema (incl. `file_refs` source) ratified;
3. §6 parameter defaults ratified;
4. §8 test catalog accepted;
5. R1-B is Python/FastAPI (locally buildable/testable — no Windows-CI gate).

## 12. Status

Doc-only contract lock. Ready for review once the doc exists at the canonical
path, `DELIVERY_DOC_INDEX.md` references it (sorted), doc-index / sorting checks
pass, and `git diff --check` is clean. Committed locally; **not pushed** —
pending review. R1-B (the read-only API) needs its own explicit opt-in.
