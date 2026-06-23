# ECM Publish — B Opt-1 (conflict-after-sent auto-republish) — DESIGN-LOCK

Status: **PROPOSAL — owner ratifies the fork (§3) + the watermark decision (§4) before build.**
Date: 2026-06-22. Supersedes the Opt-1 section of `DESIGN_ECM_PUBLISH_CONFLICT_AFTER_SENT_PROPOSAL_20260621.md`
now that **Item A is done** (the gate "Opt-1 gated on A" is lifted) and Item B Opt-2 (visibility:
`#840` log + `#844` ops filter) has landed.

## 1. The gap (code-grounded)
`EcmPublicationOutboxService._enqueue_existing()`: when `enqueue_release` re-evaluates a controlled
file whose `(item,version,file,role,target)` row is ALREADY `SENT` and the recomputed
`payload_fingerprint` differs, it records `conflict_after_sent` (audit) and **stops** — the changed
content is NEVER re-sent to Athena. B Opt-2 made this visible (log + `?conflict=true`); B Opt-1
would make the ECM record self-heal to the new content.

## 2. When it fires (severity)
In a correct PLM, a RELEASED version's controlled files are immutable (you revise via a NEW
version, which enqueues a fresh row — not a conflict). So `conflict_after_sent` is **anomalous**:
the SAME (item, version, file, role) re-released with changed bytes / a post-release edit. This is
the heart of the fork.

## 3. The fork — owner's call
- **Opt-1 (automate):** auto-republish the changed content. PRO: ECM self-heals. CON: masks a
  process anomaly (an immutable released file changed); risk of a republish loop on repeated conflicts.
- **Opt-2 (surface only — SHIPPED):** keep conflict-as-audit + the log + the ops filter; a human
  decides. PRO: no auto-loop, human-in-the-loop for an anomaly. CON: not self-healing.
- **Opt-3 (reject/guard):** treat a post-release content change as a violation to block/loudly warn.

## 4. The watermark wrinkle (MUST decide if Opt-1) — A1 interaction
A1 revisions the Athena doc only when `sourceLastModifiedAt` **differs** from the stored one
(`Objects.equals`). But `conflict_after_sent` is a **same-version** content change → the version's
`released_at` is UNCHANGED → a naive re-enqueue would hit Athena `UNCHANGED` and **NOT revision** →
the new bytes never land. So Opt-1 must give the re-publish a **distinct, newer watermark**, and that
choice collides with `#849`'s latest-wins guard (which compares snapshot `released_at`):
- **4-a — content-derived bump:** carry a monotonic `republish_seq` (or the new content fingerprint
  hashed to a sub-second offset) on top of `released_at`. Must stay **monotonic** so `#849` still
  orders correctly and Athena still sees "newer".
- **4-b — re-publish at `now()`:** stamp the re-publish watermark = current time. Simple + always
  newer, BUT it makes the watermark no longer == the PLM release instant, so `#849`'s
  `(item,role)` latest-wins would treat the re-publish as the newest even vs a genuinely newer
  *version* — needs care (compare by version generation/revision, not just the stamped watermark).
- **4-c — block instead:** if Opt-3 wins, no watermark change; just guard/audit.

**Recommendation:** if the owner wants self-healing, **Opt-1 with 4-a (a monotonic `republish_seq`
folded into the watermark)** — it preserves `#849`'s ordering semantics. But given conflict-after-sent
is anomalous + rare, a defensible **default is Opt-2 (already shipped) + Opt-3's loud-warn**, and to
NOT auto-republish until there is a real operational need. This is the owner's product call.

## 5. Build scope (only if Opt-1 ratified)
- `_enqueue_existing`: on the SENT-conflict branch, re-snapshot → `PENDING` with a bumped watermark
  (4-a) + a `republished_of`/`republish_seq` marker + a dedup guard (don't loop on identical content).
- **Worker/adapter: NOT presumed unchanged — the taskbook MUST define the effective-watermark
  contract.** Today both the adapter's `sourceLastModifiedAt` AND `#849`'s latest-wins read
  `snapshot["released_at"]`. A bumped / `republish_seq` watermark must therefore specify: (i) which
  field it lands in, (ii) what the adapter sends as the *effective* `sourceLastModifiedAt` (so
  Athena does NOT judge `UNCHANGED` and actually revisions), and (iii) which ordering key
  `#849._superseded_by_newer_sent` compares (so latest-wins is not broken). A naive re-enqueue that
  leaves these unchanged either no-ops at Athena or corrupts `#849` ordering.
- Tests: same-version content change → re-published + Athena revisions (watermark newer); repeated
  identical conflict → no loop; interaction with a genuinely newer version (latest-wins still holds).

## 6. Open questions
1. **Fork (§3):** automate (Opt-1), surface-only (Opt-2, shipped), or reject (Opt-3)?
2. **If Opt-1, watermark (§4):** 4-a monotonic seq (recommended) vs 4-b now() vs other?
3. Loop protection threshold (max republishes per lineage)?

## 7. Build gate
Nothing built here. Ratify §3 (fork) + §4 (watermark, if Opt-1) → taskbook → code. The watermark
wrinkle makes Opt-1 NOT a trivial re-enqueue, which is why it stops at a design-lock for sign-off.
