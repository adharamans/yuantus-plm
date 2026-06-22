# ECM Publish — Conflict-After-Sent Handling (Item B) — DESIGN-LOCK PROPOSAL

Status: **PROPOSAL — owner ratification gates any build. No code written.**
Date: 2026-06-21
Line: Yuantus→Athena PLM-release-to-ECM publish (functionally complete + #826-verified).

## 1. The gap (code-grounded)

`EcmPublicationOutboxService._enqueue_existing()` (`ecm_publication/service.py:181-205`):
when `enqueue_release` re-evaluates a controlled file whose `(item,version,file,role,target)`
row is **already SENT** and the recomputed `payload_fingerprint` **differs** from what was
sent, it records conflict-as-audit and **stops there**:

```
existing.properties = {..., "conflict_after_sent": True,
                       "conflict_fingerprint": fp, "conflict_basis": ...}
# does NOT raise (call site is release()) and does NOT re-enqueue a publish
```

**Consequence:** the controlled document's *content* changed after it was published, but
the new content is **never sent to Athena**. The ECM record holds the old bytes; PLM has
newer bytes. The audit flag is set, but nothing — worker or ops — acts on it. The memory's
"auto-new-version deferred to an `ecm_publication_links` follow-up" is this gap.

## 2. When does it actually fire? (scoping the severity)

In a correct PLM, a **released** version's controlled files are immutable — you revise via
a NEW version, which enqueues its own fresh row (not a conflict). So `conflict_after_sent`
fires only when the SAME (item,version,file,role) is re-released with **changed bytes**:
a re-release path, a post-release file edit, or a fingerprint-basis change. That makes B
partly a **process-integrity signal**, not only a "please re-publish" trigger — which is
the core design fork below.

## 3. Options (each forces an owner decision)

**Opt-1 — Auto-republish via `ecm_publication_links`.**
On conflict-after-sent, enqueue a NEW outbox row linked to the original
(`replay_of`/a new `ecm_publication_links` table), so the worker publishes the new content
as a new Athena document/revision.
- PRO: ECM record self-heals to current content.
- CON: needs the link model + dedup (don't loop on repeated conflicts) + interacts with
  Item A's identity question (new Athena doc vs revision-in-place → same sourceNodeId fork
  as A §2). Couples B to A.

**Opt-2 — Surface for ops, manual replay (status quo + visibility).**
Keep conflict-as-audit; add an ops signal (the new skip-style log + a list filter / count
in the ops router) so an operator sees conflicts and chooses to act (existing `replay`
already resets failed rows — extend to a conflict-driven re-enqueue action).
- PRO: small; no auto-loop risk; human in the loop for an anomalous event.
- CON: not self-healing; needs an operator.

**Opt-3 — Treat as a process violation (reject the mutation).**
Define released controlled files as immutable; a post-release content change is a bug to
surface loudly (warn/audit + metric), not to auto-publish. Pair with B-adjacent guard that
discourages re-release-with-changed-bytes.
- PRO: enforces PLM correctness; cheapest behaviorally.
- CON: doesn't help legitimate re-publish needs; may mask a real workflow.

## 4. Recommendation (for ratification)

**Opt-2 now, Opt-1 later — and explicitly gate Opt-1 on Item A.** Conflict-after-sent is
rare + anomalous; the immediate win is *visibility* (Opt-2: it's currently invisible — no
log, no ops surface), which is cheap and risk-free. True auto-republish (Opt-1) shares A's
unresolved Athena-identity question (new doc vs revision-in-place), so it should not be
built before A is ratified. Opt-3's "immutable" stance is a good **documentation** addition
regardless.

## 5. Open questions before a taskbook

1. Is conflict-after-sent a **process violation to surface** (Opt-2/3) or a **legitimate
   re-publish to automate** (Opt-1)? — the core fork.
2. If automate (Opt-1): new Athena doc per conflict, or revision-in-place? — **this is
   the same Q1 as Item A**; ratify A first.
3. Visibility shape for Opt-2: a `conflict_after_sent` log (mirrors the #837/skip-log
   pattern) + an ops `?conflict=true` list filter — acceptable minimal scope?

## 6. Build gate

Nothing built here. Opt-2's visibility slice is small and Yuantus-only (a log + an ops
filter) and could be the first ratified increment; Opt-1 is gated on Item A. Same
design-lock → ratify → build loop.
