# Claude Taskbook: PLM→ERP Publication Contract — R4 `/publication/export` (Read-only Pull, Scope-Lock)

Date: 2026-05-29

Type: **Doc-only taskbook (scope-lock for the export slice).** It locks
`/publication/export` — the read-only **pull** surface that returns an item's
publishable package — its inputs, auth, output format, snapshot source,
read-only/no-side-effect guarantee, ineligible handling, and its relationship to
the R3 connector. It changes no code. **Merging this taskbook does NOT authorize
the export implementation** — that requires its own explicit opt-in.

This is the **last planned G2 boundary** (#666 §9: "`/publication/export` is
explicitly OUT of R2 — a separate future slice"). R1 = readiness verdict/API;
R2 = outbox + manual routes + worker; R3 = real ERP connector (push). **R4 is the
read-only PULL side**: the caller fetches the publishable artifact instead of the
connector pushing it.

Parents: #663–#674 (the full G2 line through the R3 connector impl). Baseline
`main = d3372f69`.

## 0. What this is (and is not)

- **Read-only PULL.** A `GET` that returns "the package that would be published
  for this item, right now." No enqueue, no outbox row, no POST, no adapter, no
  DB write — pure read (like R1-B `/publication-readiness`).
- **Distinct from `/publication-readiness` (R1-B).** Readiness returns the
  *verdict* (publishable? why not — with per-resource diagnostics). Export returns
  the *publishable package* (the canonical snapshot artifact) when eligible. Same
  underlying machinery, different shape + purpose (decision-support vs the
  artifact itself).
- **Distinct from R3 (push).** R3's connector POSTs; R4 lets a caller pull the
  same publishable data. R4 touches **no adapter** (not even Null) and performs
  **no external I/O**.

## 1. Grounding (against `main = d3372f69`)

- `web/plm_erp_publication_router.py`: the read-only verdict route
  `GET /plm-erp/items/{item_id}/publication-readiness` and the shared, HTTP-
  agnostic `build_publication_readiness(db, item, item_id, *, ruleset_id,
  mbom_limit, routing_limit, baseline_limit) -> PublicationReadinessResponse`
  (router:150). Auth = `require_admin_permission` in-body.
- `erp_publication/service.py`: `build_snapshot(readiness, *, target_system,
  publication_kind) -> dict` — the canonical publishable snapshot (eligible,
  blocking_reasons, ruleset_id, limits, item, version, file_refs, summary, esign,
  generated_at). This is what the outbox stores and what R4 returns.
- `eligible` is the R1-A formula; `blocking_reasons` carry the "why not".
- Route-count pins live at 683 (phase4 + tier_b_3 + metrics-delta + breakage-
  metrics; full-tree `grep -rn 'len(app.routes)'` before moving).

## 2. Route + inputs (ratify)

`GET /plm-erp/items/{item_id}/publication/export` (admin-gated), with the R1-B
read params:

- path: `item_id`;
- query: `ruleset_id` (default `"readiness"`), `mbom_limit`/`routing_limit`/
  `baseline_limit` (default 20, `ge=0 le=200`);
- query: `publication_kind` (default `"readiness"`) — metadata stamped into the
  snapshot. **`target_system` is NOT required** — export returns the
  **target-agnostic** canonical snapshot (a specific ERP's wire-format is the
  connector's `build_payload`, a transport detail, not export's concern).

One new route → `len(app.routes)` **683 → 684** at IMPL (full-tree residual scan
first; this doc-only taskbook touches no pin).

Concretely: `build_snapshot(readiness, *, target_system, publication_kind)`
requires + stamps `target_system`, so export passes **`target_system=""`**
(empty = no specific target); the emitted snapshot's `target_system` is therefore
the empty string — the unambiguous "target-agnostic export" marker (the impl is
not left to guess a value).

## 3. Auth (ratify)

Admin — `require_admin_permission`, identical to `/publication-readiness` and the
outbox routes. Exposing the publishable package is at least as sensitive as the
readiness verdict.

## 4. Output format (ratify)

The export response surfaces the verdict + the canonical snapshot:

```
{
  item_id,
  eligible,                       # the R1-A verdict
  blocking_reasons: [{reason, detail}],
  generated_at,
  snapshot: <canonical snapshot> | null   # build_snapshot(...) when eligible, else null
}
```

`snapshot` is the **canonical, target-agnostic** publishable package (the same
structure the outbox stores and the connector serializes) — NOT an
adapter-specific wire payload. A target-specific export (a given ERP's exact
bytes) is a later, optional param/slice, explicitly OUT here.

## 5. Snapshot source — fresh, not the outbox (ratify)

Export builds a **fresh** snapshot from **current** readiness
(`build_publication_readiness` → `build_snapshot`), NOT a stored outbox row. It is
a read of *current* publishability ("what would be published now"); the outbox is
the delivery queue, not the source of truth for a fresh export. A
**by-outbox-id** export (return a stored row's snapshot as-sent, for audit) is a
possible secondary endpoint — noted as optional, OUT of this slice.

## 6. Read-only / no side effect (ratify)

Export performs **no** write of any kind: no outbox enqueue, no row mutation, no
adapter call, no POST, no external I/O — exactly like R1-B. It shares the
no-side-effect guarantee dry-run/readiness already hold.

## 7. Ineligible handling (ratify)

Export is read-only, so an ineligible item is **not** a 4xx. Return **200** with
`eligible=false`, the `blocking_reasons`, and `snapshot=null` (an ineligible item
has nothing publishable to export). This lets the caller see *why* it can't be
exported without an error. (404 only when the item itself does not exist; 400 for
an unknown ruleset, chained, as R1-B.)

## 8. Relationship to the R3 connector (ratify)

Export (PULL) and the connector (PUSH) share the **same canonical snapshot**
(`build_snapshot`) as the publishable data, but:

- export returns it read-only to a caller; the connector POSTs an adapter-shaped
  payload to an ERP;
- export touches **no** adapter / registry / outbox; the connector owns delivery,
  idempotency, retry. (`build_snapshot` is reused as a **pure** readiness→dict
  transform; it lives in the outbox service module but performs no outbox I/O, so
  reusing it does not couple export to the outbox.)
- a target wanting export's data PUSHED uses the connector; a target preferring to
  PULL uses export. They are complementary, not coupled.

## 9. Non-Goals

No write/enqueue/POST; no adapter or connector involvement; no target-specific
wire payload (canonical snapshot only); no by-outbox-id export (optional later);
no new publication semantics (export reuses R1-B + `build_snapshot`); no change to
the outbox / worker / connector.

## 10. Preconditions to enter the export IMPLEMENTATION

1. §2 route + inputs (target-agnostic, no `target_system` required) ratified;
2. §3 admin auth ratified;
3. §4 output format (verdict + canonical snapshot) ratified;
4. §5 fresh-current snapshot (not stored outbox) ratified;
5. §6 read-only / no-side-effect ratified;
6. §7 ineligible → 200 + null snapshot ratified;
7. §8 PULL-vs-PUSH relationship ratified;
8. the route-count move (683→684, full-tree residual scan) acknowledged.

A **separate explicit opt-in** then authorizes the implementation.

## 11. Reviewer Focus

1. §0/§6 — read-only PULL, no side effect, no adapter?
2. §2 — `GET …/publication/export`, target-agnostic (no `target_system` needed)?
3. §4 — output = verdict + canonical snapshot (not an adapter wire payload)?
4. §5 — fresh current readiness, not a stored outbox snapshot?
5. §7 — ineligible → 200 + `snapshot=null` (not 4xx)?
6. §8 — complementary to R3 (shared snapshot), not coupled?
7. §2/§9 — 683→684 at impl; by-outbox-id + target-specific export stay OUT.

## 12. Status

Doc-only scope-lock. Ready for review once the doc exists at the canonical path;
`DELIVERY_DOC_INDEX.md` references it + its DEV/verification record (sorted under
`## Development & Verification`); doc-index / sorting / completeness checks pass;
`git diff --check` clean. Ratifying §2–§8 sets the export implementation plan;
**a separate explicit opt-in authorizes the implementation.** With R4 landed, the
planned G2 line is complete; any vendor-specific adapter or a by-outbox-id /
target-specific export remain later, separately-opted slices.
