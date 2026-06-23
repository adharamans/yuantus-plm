# ECM Publish — A1 Disposition (supersede-with-successor) — CLOSEOUT

Status: **DONE — landed on main 2026-06-22.** Yuantus `#848` (core) + `#849` (ordering guard);
Athena `zensgit/Athena #26` (contract lock). All CI-green; squash scopes verified clean.
Ratified taskbook: `DEVELOPMENT_ECM_PUBLISH_DISPOSITION_A1_TASKBOOK_20260622.md`.

## What A1 does
On release of version N+1, its publish makes the SAME Athena document supersede the
version-N content in place, so the ECM record reflects the latest released version (was:
each version published as a DISTINCT Athena doc → superseded/obsoleted predecessors went
stale). Scope = **A1 (supersede-WITH-successor)** only; A2 below.

## Q1 (cross-repo) — CONFIRMED
Athena `TransferReceiverService.uploadDocument` revisions-in-place by `(sourceRepositoryId,
sourceNodeId)`: on a mapping hit with a DIFFERING `sourceLastModifiedAt` it calls
`versionService.createVersion(existingDoc, …)` → `OVERWRITTEN`; equal → `UNCHANGED`; no
mapping → `CREATED`. Locked by Athena #26 (`uploadDocumentRevisionsMappedDocumentWhenSourceModifiedNewer`).

## Build (Yuantus-side, no Athena API change)
- **Stable identity** (`#848`, `build_transfer_source_node_id`): `uuid5(item_id, file_role)` —
  was `item_id|version_id|file_id|file_role` (version-scoped = the staleness root).
- **Same-role fail-closed guard** (`#848`, `enqueue_release`): >1 controlled file of one role
  per version is skipped via `logger.warning`; **no outbox row written** (a dedicated SKIPPED
  audit row is a possible later enhancement). Structural: `VersionFile` constrains
  `(version_id, file_id, file_role)`, NOT `(version_id, file_role)`. B1.0 staging probe = 0 rows
  (≤1/role today); the guard covers future drift.
- **Microsecond watermark** (`#848`, `_local_datetime`): `timespec="microseconds"` (was
  `seconds`). Live probe: `released_at = timestamp(6)`, non-zero micros → confirmed.

## ⚠️ Cutover (B2-a, accepted — one-time)
The stable sourceNodeId does NOT match existing version-scoped Athena mappings, so the FIRST
stable publish of an already-published item is `CREATED` (a new Athena doc); the old
version-scoped docs are left as **one-time stale** (no Athena mapping rebaseline). Documented
discontinuity, accepted.

## Ordering — RESOLVED (this is the final state; supersedes #848's interim wording)
Athena `matchesSourceVersion` uses `Objects.equals` (**equality, not ordering**) — it revisions
on ANY watermark mismatch and does NOT reject a stale/older publish. Left bare, an out-of-order
same-lineage publish (an older version's outbox row draining AFTER a newer version's, e.g. via
retry) could overwrite the Athena doc with OLDER content.

**`#849` resolves this** with a Yuantus worker-side **latest-wins guard**: before dispatch,
`_superseded_by_newer_sent` skips (SKIPPED/not_eligible + `properties.superseded_by_newer_sent`)
any row whose `(item_id, file_role, target_system)` already has a SENT row with a strictly newer
snapshot `released_at`. So A1 no longer relies on in-order draining for correctness.
(NOTE: do NOT carry forward #848's standalone "latest-wins not yet enforced" wording — that was
the pre-#849 interim; #849 is merged.)

## ⚠️ Future constraint (NOT this round's blocker)
`#849`'s "check-before-dispatch" is sufficient for the **current single dedicated
`ecm-publication-worker`** (the compose/runbook deploys exactly one). **If ECM workers are later
scaled horizontally (>1 concurrent), add a per-lineage DB lock / claim ordering** — otherwise two
concurrent workers could each pass the check and dispatch out of order, bypassing the guard's
timing. Tracked as a scaling prerequisite, not a current gap.

## A2 (obsolete/withdraw with NO successor) — Opt-4 interim
No successor publish exists to trigger a revision, so obsolete-without-successor is **NOT
propagated** to ECM. Documented interim; an Athena withdraw API (Opt-1) is NOT built and is
revisited only if needed after A1 is stable.

## Verification
- Yuantus `#848`: 5 tests (stable-across-versions / differs-by-role / microseconds / guard-skips-2-same-role / keeps-1-per-role) + 11 enqueue regression — CI green.
- Yuantus `#849`: 6 tests (superseded→SKIPPED-no-dispatch / predicate / not-superseded→dispatch / no-sibling / equal-released_at-does-not-supersede / cross-lineage-ignored) + worker regression (32 passed) — CI green.
- Athena `#26`: `Backend Verify` compiled + ran the contract test; full gate (incl. Frontend E2E Core Gate) green.
- B1.0 multiplicity + B3 precision live probes run on staging (0 rows ≤1/role; `timestamp(6)`).

## Follow-ups (deliberately out of A1)
- Multi-worker per-lineage lock (only if horizontal scaling).
- Optional: a real SKIPPED audit row for the same-role guard (today = warning log).
- B Opt-1 (conflict auto-republish) — was gated on A; A1's identity model now informs it.
