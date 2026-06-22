# ECM Publish — Disposition Propagation (Item A) — DESIGN-LOCK PROPOSAL

Status: **PROPOSAL — owner ratification gates any build. No code written.**
Date: 2026-06-21
Line: Yuantus→Athena PLM-release-to-ECM publish (functionally complete + #826-verified).
This is the first of the deferred "decision-menu" items (A) turned into a ratifiable
design-lock, per the design-lock-first discipline. B/C/D are not design-locked here.

## 1. The gap (code-grounded)

`VersionService.release()` (`version/service.py:580-594`) supersedes the predecessor
**inside the release txn**: when a newer version of an item is released, the prior
released version gets `is_superseded=True` / `state="Superseded"`. Items can also go
**Obsolete** (lifecycle promote; e.g. the C3 date-obsolete worker).

The ECM enqueue hook `_enqueue_ecm_publication()` (`version/service.py:607-648`)
enqueues **only the newly-released version's** controlled files. It does **nothing** to
the superseded/obsoleted predecessor's already-published ECM record.

**Consequence:** the predecessor's controlled document, once published to Athena, stays
in Athena as if current. PLM has moved on (superseded/obsolete) but the ECM
controlled-record repo — which is meant to be the **system-of-record for the controlled
document** — shows a stale record. The publish story is one-directional and
release-only; it has no "the thing I published is no longer current" propagation.

## 2. Why Athena does not self-heal this (the load-bearing finding)

Each version publishes with a **version-scoped source identity**: `build_snapshot`
keys the snapshot off `version.id`, and `transfer_receiver_adapter._dispatch`
(`:337-345`) sends `sourceNodeId = payload["source_node_id"]` derived from the version.
So **each released version arrives at Athena as a DISTINCT document** (distinct
sourceNodeId) — Athena does not treat version N+1 as a new revision of version N's
document, and therefore does not auto-supersede the old one. The staleness is real and
will not resolve itself by "just publishing forward."

This is the crux: fixing A is **not** a small Yuantus-only enqueue tweak; it forks on a
cross-repo question about Athena's Transfer Receiver semantics.

## 3. Options (each with the owner decision it forces)

**Opt-1 — Withdraw/obsolete the old record (active propagation).**
On supersede/obsolete, enqueue a NEW outbox *action* row (an `action` discriminator:
`publish` vs `withdraw`) for the predecessor's published record; the worker calls a
**new Athena Transfer Receiver withdraw/obsolete endpoint**.
- PRO: ECM record accurately mirrors PLM lifecycle; works for obsolete-without-successor too.
- CON: **cross-repo** — needs an Athena-side withdraw API (Athena #?, not Yuantus-only) +
  a new outbox action type + "predecessor was never published" handling + idempotency for
  withdraw. Largest.

**Opt-2 — Item-level source identity so Athena versions them in place.**
Restructure the publish to use an **item-level (not version-level) sourceNodeId** so a
new version's publish lands as a new *revision* of the same Athena document, which Athena
supersedes in place.
- PRO: Yuantus-side; no Athena API change *if* Athena revisions by sourceNodeId.
- CON: changes the published identity model (every prior published doc's identity shifts);
  needs Athena to actually version-by-sourceNodeId (verify); does **not** cover
  obsolete-without-successor (withdraw with no new version).

**Opt-3 — Superseded marker (passive flag).**
On supersede, enqueue a lightweight property update on the old record
(`status=superseded`) rather than a withdraw.
- PRO: smaller; record stays but is flagged.
- CON: needs an Athena property-update path; record still present (partial fix).

**Opt-4 — Accept staleness (documented no-op).**
Declare ECM records point-in-time-at-release; PLM stays system-of-record for currency.
- PRO: zero work.
- CON: undercuts "ECM is the controlled-record system-of-record" — the reason the line exists.

## 4. Recommendation (for ratification, not a decision)

Split A and sequence it:
- **A1 (supersede-WITH-successor): Opt-2 — Yuantus-side ONLY (Q1 CONFIRMED 2026-06-22).**
  The Athena Transfer Receiver DOES revision-in-place by `(sourceRepositoryId, sourceNodeId)`:
  `TransferReceiverService.uploadDocument` (ecm-core), on a mapping hit with a CHANGED
  `sourceLastModifiedAt`, calls `versionService.createVersion(existingDocument, file, ...)`
  → `OVERWRITTEN` (a new Athena version of the SAME node). So A1 needs NO Athena change — it
  is a Yuantus-side source-identity change: send a **stable-across-versions sourceNodeId**
  (currently version-scoped — the staleness root cause) keyed on the logical controlled file
  `(item, file_role/lineage)`, plus a **monotonic `sourceLastModifiedAt`** (else the receiver
  matches the stored version and returns `UNCHANGED`, skipping the revision). Then version
  N+1's publish revisions version N's Athena doc in place → the ECM record always reflects the
  latest released version.
- **A2 (obsolete/withdraw with NO successor):** inherently needs **Opt-1** (an Athena
  withdraw endpoint) — there is no "publish-forward" to lean on. Treat as a separate
  cross-repo slice; **Opt-4** (accept + document) is the honest interim until Athena ships
  withdraw.

## 5. Open questions the owner must answer before a taskbook

1. **Cross-repo (Q1) — ANSWERED 2026-06-22: YES, Athena revisions-in-place.**
   `TransferReceiverService.uploadDocument` maps `(sourceRepositoryId, sourceNodeId)` →
   existing Athena node; on a hit with a newer `sourceLastModifiedAt` it
   `versionService.createVersion(...)` (OVERWRITTEN), else UNCHANGED; no mapping → CREATED.
   ⇒ A1 = Opt-2, Yuantus-side, **no Athena change**. (The remaining items are owner calls.)
2. **Scope of A2 (owner call):** obsolete-without-successor has no publish to revision, so it
   STILL needs Opt-1 (an Athena withdraw endpoint) OR Opt-4 (accept staleness + document) as
   the honest interim. Build A1 now and defer/accept A2, or scope A2's Athena API too?
3. **Ratify A1 + priority:** A1 changes the *published identity model* (version-scoped →
   stable per-file-lineage sourceNodeId) — a real, ratify-gated change. OK to taskbook A1
   now? And priority vs the other lines.

## 6. Build gate

NONE of §3-§5 is built here. On ratification (option choice + Q1-Q3 answers) this becomes a
taskbook (`DEVELOPMENT_ECM_PUBLISH_DISPOSITION_*_TASKBOOK_*.md`) and then code — same
design-lock → ratify → build loop used for the durable-reachability slice. The owner's
season-long discipline (no autobuild of unratified product semantics) is why this stops at
a proposal.
