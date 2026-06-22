# ECM Publish — Outbox Retention / Pruning (Item C) — DESIGN-LOCK PROPOSAL

Status: **PROPOSAL — owner ratification gates any build. No code written.**
Date: 2026-06-21
Line: Yuantus→Athena PLM-release-to-ECM publish (functionally complete + #826-verified).
This is the **smallest + lowest-risk** deferred item — a default-off knob, not new semantics.

## 1. The gap (code-grounded)

`meta_ecm_publication_outbox` accumulates terminal **SENT** rows forever. The worker
(`worker.py`) only claims/processes **PENDING** rows; nothing prunes terminal rows, and the
ops list endpoint self-documents it: `plm_ecm_publication_outbox_router.py:137` —
"Bounded: the outbox accumulates SENT rows indefinitely (**no retention path yet**)." On a
busy tenant the table grows without bound (one row per controlled file per release, kept
after dispatch).

This is an **operational** concern (table growth / list-query cost), not a correctness or
publish-semantics gap — which is why it's the cheapest of A–E.

## 2. Options

**Opt-1 — Age-based prune, default-OFF (recommended).**
New setting `PUBLICATION_ECM_OUTBOX_RETENTION_DAYS` (default `0` = disabled, exactly the
kill-switch idiom used across this codebase). When `> 0`, the worker (or a scheduler tick)
deletes rows that are **terminal SENT** and older than N days. Never touches
PENDING/FAILED/SKIPPED, and **preserves `conflict_after_sent` rows** (audit). Off by
default → zero behavior change until ops opts in.

**Opt-2 — Keep-N-most-recent per (item/version).** Bound by count, not age.
- CON: less intuitive for an audit trail; per-key bookkeeping.

**Opt-3 — Archive-then-delete.** Copy to cold storage / an archive table before delete.
- CON: needs an archive target decision; heavier.

**Opt-4 — Keep-all (status quo).** Rely on external DB partitioning/retention.
- PRO: zero app code. CON: leaves the documented unbounded-growth note standing.

## 3. Recommendation (for ratification)

**Opt-1** — age-based, default-off, SENT-only, conflict-rows preserved. It matches the
repo's pervasive default-off-flag pattern, is reversible (flip to 0), deletes only terminal
non-audit rows, and directly closes the `router:137` note. This is small enough that, once
the **policy** is ratified, it is a single buildable increment (1 setting + 1 prune step in
the worker/scheduler + tests) — no cross-repo, no new semantics.

## 4. Open questions before build

1. Default age when enabled (e.g. 90 days)? And is the prune **SENT-only**, or also old
   terminal `SKIPPED`?
2. Always **preserve `conflict_after_sent`** rows (recommended — they're the audit signal
   for Item B), confirm?
3. Prune driver: the existing worker tick, or the lightweight `scheduler`? (scheduler is
   cleaner — prune is not per-batch drain work.)

## 5. Build gate

Nothing built here. Of A–C this is the one that, on a one-line policy ratification (answers
to §4), I can build directly as a small default-off slice without further design — say the
word ("C: age-prune, 90d, SENT-only, scheduler") and it becomes a normal small PR.
