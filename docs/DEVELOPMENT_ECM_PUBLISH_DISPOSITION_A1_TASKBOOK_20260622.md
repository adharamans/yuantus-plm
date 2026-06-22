# ECM Publish — A1 Disposition (supersede-with-successor) — TASKBOOK

Status: **TASKBOOK — A1 RATIFIED 2026-06-22 (owner): B1-a + runtime guard / B2-a accept-cutover / B3-a microseconds. 3 boundary decisions (B1-B3) + a test matrix (B4). Build gated on the B1.0 multiplicity + B3 precision LIVE probes.**
Date: 2026-06-22
Decision context (owner, 2026-06-22): A1 ratified as **Opt-2 (Yuantus-side, no Athena API change)**;
**A2 = Opt-4 interim** (document obsolete-without-successor as not-propagated; do NOT build an
Athena withdraw API now); A1 taskbook is the next-highest priority.

## 0. What A1 does (and the Q1 fact it rests on)

When item version N+1 is released, its publish should make the SAME Athena document supersede
the version-N content in place, so the ECM record always reflects the latest released version.

Q1 CONFIRMED (Athena `TransferReceiverService.uploadDocument`): on a `(sourceRepositoryId,
sourceNodeId)` mapping hit with a **newer** `sourceLastModifiedAt`, Athena calls
`versionService.createVersion(existingDoc, file, ...)` → `OVERWRITTEN`; equal modified-time →
`UNCHANGED`; no mapping → `CREATED`. So A1 is a **Yuantus-side source-identity change** — but
three boundaries (B1–B3) and a test matrix (B4) MUST be pinned first, per review 2026-06-22.

---

## B1 — Stable source identity (the lineage key) — **MUST DEFINE**

**Today:** `build_transfer_source_node_id` (`transfer_receiver_adapter.py:69`) =
`uuid5(item_id | version_id | file_id | file_role)`. **`version_id` is what makes the key
version-scoped**; `file_id` is NOT structurally fresh-per-version (`copy_files_to_version()`
copies file_id forward to the new version) and is NOT a reliable lineage key either (it can change
on file replacement). So the key changes every version (via `version_id`) → Athena never matches →
always CREATED → the staleness.

**`VersionFile` (`version/models.py:190`)** carries `file_role`, `sequence`, `is_primary` —
but **NO cross-version lineage id**. So the stable key is a genuine modeling decision:

| Option | Key | Risk |
|---|---|---|
| **B1-a** | `uuid5(item_id, file_role)` | **Collides** if a version has >1 controlled file of the same role (e.g. multi-sheet drawings) → two PLM files fold into one Athena doc. |
| **B1-b** | `uuid5(item_id, file_role, sequence)` | Works ONLY if `sequence` is stable across versions for the same logical file (it is an ordering, not a lineage — reorders/insertions break it). |
| **B1-c** | `uuid5(item_id, file_role, filename_stem)` | Stable if the controlled file keeps its name across revisions; breaks on rename. |
| **B1-d** | new **`lineage_id`** on `VersionFile`, carried forward on file revision | Cleanest + correct; costs a model field + migration + lineage-propagation on the revise path. |

**Taskbook task B1.0 (gating):** measure multiplicity — does a released version ever carry >1
controlled file (native_cad/drawing/geometry) of the SAME role? Query prod/staging.
- If **≤1 per role** in practice → **B1-a** (`item_id, file_role`) is the clean, zero-migration key.
- If **>1 per role** occurs → **B1-d** (lineage_id) is required; B1-b/c are too fragile to publish on.

**RATIFIED (owner 2026-06-22): B1-a + a MANDATORY runtime guard.** Run B1.0 first; if historical
≤1/role → `uuid5(item_id, file_role)`. **Regardless of the historical result** — because
`VersionFile` only constrains `(version_id, file_id, file_role)`, NOT `(version_id, file_role)`, so
same-role-multi-file is *structurally* allowed and could collide in future — A1 MUST add a
release/enqueue-level **fail-closed guard**: a released version with >1 controlled file of the SAME
role is rejected / skipped-with-audit (never fold two PLM files into one Athena doc). Only if B1.0
finds >1/role historically → switch to **B1-d** lineage_id (never sequence/filename).

---

## B2 — Cutover / legacy mapping — **MUST DEFINE**

Changing the sourceNodeId rule means **existing version-scoped Athena mappings will NOT match**
the new stable id. So the first stable publish of an already-published item is `CREATED` (a NEW
Athena doc), and the old version-scoped docs are left in place (stale/orphaned). My earlier
report over-promised "N+1 revisions N's doc" — that holds only AFTER the cutover, for items
published under the new rule.

| Option | Behavior | Cost |
|---|---|---|
| **B2-a (accept cutover)** | First stable publish per item = `CREATED`; thereafter `OVERWRITTEN`. Old version-scoped docs accepted as one-time stale. | Zero code; a documented one-time discontinuity. |
| **B2-b (rebaseline)** | Migrate Athena `TransferNodeMapping` (or a Yuantus re-publish) so the new stable id points at the current latest Athena doc. | **Cross-repo** (Athena mapping migration) or a Yuantus backfill; larger. |

**RATIFIED (owner 2026-06-22): B2-a (accept cutover), Yuantus-only.** First stable publish per item
= `CREATED`; old version-scoped docs accepted as one-time stale — **MUST be written into the
closeout / ops note**. No Athena mapping rebaseline (B2-b deferred).

---

## B3 — Monotonic watermark (`sourceLastModifiedAt`) — **MUST FIX**

**Today:** `_local_datetime` (`transfer_receiver_adapter.py:109`) returns
`isoformat(timespec="seconds")` — **second-truncated**. `released_at` is written at release
(`version/service.py:567`). With a STABLE sourceNodeId, two releases of one item in the **same
second** produce an EQUAL `sourceLastModifiedAt` → Athena `matchesSourceVersion` → `UNCHANGED` →
**the newer content is silently NOT published.** (Today this is masked because the id changes
every version; B1 unmasks it.)

| Option | Fix | Caveat |
|---|---|---|
| **B3-a** | drop truncation → `timespec="microseconds"` | Only helps if `released_at`'s stored precision is sub-second. **Verify the column type** (`released_at` DateTime precision); if second-only at the source, microseconds in the adapter are zeros. |
| **B3-b** | derive a **strictly monotonic** LocalDateTime watermark from a monotonic field (a publish sequence / `generation`+`revision`) mapped onto the timestamp, independent of wall-clock collisions | More robust; needs a deterministic, monotonic-per-publish source mapped to a `LocalDateTime`. |

**RATIFIED (owner 2026-06-22): B3-a microseconds + a LIVE precision gate.** `released_at` is set via
`datetime.utcnow()` (Python keeps microseconds) and SQLAlchemy `DateTime()` usually preserves them
on PG/SQLite, so B3-a is the path: change `_local_datetime` to `timespec="microseconds"`. **Build
prerequisite (do NOT skip): a live/staging probe** confirming a release-written `released_at`
round-trips WITH microseconds. Only if the live DB truncates to seconds → upgrade to **B3-b**
(monotonic watermark). The publish must never be dropped to a same-second collision.

---

## B4 — Test matrix — **MUST COVER**

Yuantus-side (unit + the worker e2e smoke):
1. **Cutover:** first stable publish of a previously-version-scoped item → `CREATED`.
2. **Steady-state supersede:** release N then N+1 (stable id) → N+1 = `OVERWRITTEN`, same Athena doc id.
3. **Same-second guard:** two releases in one second → distinct watermark → still `OVERWRITTEN` (no false `UNCHANGED`).
4. **Multi-file-per-role** (if B1-d): two drawings → two distinct lineage ids → two docs, no collision.
5. **Idempotent re-publish:** identical content/watermark → `UNCHANGED` (no spurious version).

Athena-side (owner's suggestion — the load-bearing branch is currently unlocked):
6. **`TransferReceiverService` regression:** mapping hit + **newer** `sourceLastModifiedAt` →
   `versionService.createVersion` called + `OVERWRITTEN` returned. (Existing tests lock `UNCHANGED`
   and plain overwrite, not this branch — `TransferReceiverService.java:239`.)

---

## Build steps (gated on B1–B3 ratification)

1. B1.0 multiplicity query → pin the stable key (B1-a or B1-d).
2. `build_transfer_source_node_id` → stable key; (if B1-d) add `VersionFile.lineage_id` + migration + revise-path propagation.
3. `_local_datetime` / snapshot → monotonic watermark (B3-a or B3-b); verify `released_at` precision.
4. Yuantus tests (B4 #1–5) + the Athena regression test (B4 #6).
5. Docs: B2-a cutover discontinuity note + A2 Opt-4 interim note.

## A2 (obsolete/withdraw, NO successor) — Opt-4 interim (ratified)

No successor publish exists to trigger a revision, so obsolete-without-successor is **NOT
propagated** to ECM in A1. Documented as a known interim; an Athena withdraw API (Opt-1) is
NOT built now and is revisited only after A1 is stable.

## Build gate

Ratify B1 (key), B2 (cutover stance), B3 (watermark) → then build. Same design-lock → ratify →
build loop. A1 changes the published identity model, so it stops here for owner sign-off on the
three boundary choices above.
