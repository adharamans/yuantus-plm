# ECM Publish (PLM → Athena) — Line Development & Verification

Date: 2026-06-22. Scope: the Yuantus→Athena "publish released controlled documents to ECM"
line, cumulative through 2026-06-22. One-directional, lifecycle-triggered; NOT bidirectional.

## 1. State at a glance

**The line is functionally COMPLETE and on main.** Everything below is merged + CI-green.
The remaining items (§5) are design-gated (B Opt-1), direction-gated (A2, D/mail), or
scaling-gated (multi-worker lock) — none is silently unfinished code.

## 2. What is built (cumulative)

| Area | PRs | What |
|---|---|---|
| Durable reachability | Yuantus #796, #826 | Symmetric opt-in override (shared `ecm-publish-net` + `athena-ecm-core` alias); drainer profile-gated; **S3 initial drain + S4 post-recreate persistence VERIFIED on staging**. |
| Transport | (in #796) | Athena **Transfer Receiver** over custom credential headers `X-Athena-Transfer-User` / `X-Athena-Transfer-Secret` (NOT HTTP BASIC; a test asserts no `Authorization`). |
| Observability / hygiene | #832, #837 | Enqueue-skip DEBUG logs (kill-switch / not-entitled / entitlement-exception); stale P1D-connector comments corrected. |
| **C — outbox retention** | #839 | Default-OFF scheduler-driven prune of terminal SENT rows (`PUBLICATION_ECM_OUTBOX_RETENTION_DAYS`=0 disabled; `_BATCH_SIZE` bounds each run; preserves `conflict_after_sent`). Mirrors `audit_retention_prune`. |
| **B Opt-2 — conflict visibility** | #840, #844 | `logger.warning` where `_enqueue_existing` records `conflict_after_sent`; `GET /plm-ecm/publication-outbox?conflict=true|false` (SQLAlchemy `.as_boolean()`, cross-dialect). |
| **A1 — disposition (supersede-with-successor)** | #848, #849, Athena #26, #852 | See §3. |
| Records | #850 | A1 closeout + taskbook + A/B/C design-locks + runsheet landed on main. |

## 3. A1 disposition — mechanism

Goal: version N+1's publish makes the SAME Athena document supersede version N in place (was:
each version a distinct Athena doc → superseded predecessors went stale).

- **Stable identity** (#848 `build_transfer_source_node_id`): `uuid5(item_id, file_role)` (was
  `item|version_id|file_id|role` = version-scoped). **Q1 CONFIRMED** (Athena
  `TransferReceiverService.uploadDocument`): a mapping hit on `(sourceRepositoryId, sourceNodeId)`
  with a DIFFERING `sourceLastModifiedAt` → `versionService.createVersion` → `OVERWRITTEN`.
- **Same-role fail-closed guard** (#848 `enqueue_release`): >1 controlled file of one role per
  version → skipped via `logger.warning`, no outbox row (the stable identity would fold them into
  one Athena doc; `VersionFile` only constrains `(version_id, file_id, file_role)`).
- **Microsecond watermark** (#848 `_local_datetime`): `timespec="microseconds"` (was `seconds`),
  so same-second releases get distinct watermarks.
- **Latest-wins ordering guard** (#849 worker): Athena's `matchesSourceVersion` is `Objects.equals`
  (equality, not ordering) → an out-of-order same-lineage publish could regress the doc;
  `_superseded_by_newer_sent` skips (SKIPPED/not_eligible) any row whose `(item,role,target)` already
  has a SENT row with a newer snapshot `released_at`.
- **Cutover (B2-a, accepted):** first stable publish of an already-published item = `CREATED` (new
  doc); old version-scoped docs are one-time stale (no Athena mapping rebaseline).

## 4. Verification

**CI:** every PR above merged green (Yuantus `plugin-tests` + `regression`; Athena full gate incl.
`Backend Verify` + `Frontend E2E Core Gate`). Squash scopes verified clean per merge.

**Test matrices (representative):**
- A1 core #848 — 5: stable-across-versions / differs-by-role / microseconds / guard-skips-2-same-role / keeps-1-per-role; + 11 enqueue regression.
- A1 guard #849 — 6: superseded→SKIPPED-no-dispatch (spy adapter) / predicate / not-superseded→dispatch / no-sibling / equal-released_at-does-not-supersede / cross-lineage-ignored; + worker regression (32 total).
- Athena #26 — `uploadDocumentRevisionsMappedDocumentWhenSourceModifiedNewer`: mapping-hit + newer → `createVersion` + `OVERWRITTEN` (Backend Verify compiled + ran it green).
- C #839 / B Opt-2 #840+#844 — prune (disabled/age/limit/scheduler-task) and conflict-filter (`?conflict=true|false`, NULL-safe, cross-lineage) tests.

**Live staging probes:** durable-reachability S3/S4 (host `23.254.236.11`); A1 B1.0 multiplicity =
**0 rows** (≤1 controlled file/role today → `(item, file_role)` key safe); A1 B3 precision =
`released_at = timestamp(6)`, non-zero microseconds → microsecond watermark viable. **#828 fresh
upload -> publish is now proven**: after host disk headroom recovered from the prior
`XMinioStorageFull` condition, a new 5,562-byte controlled STEP object was written to MinIO through
Yuantus `FileService`, released through `VersionService.release()`, drained to outbox `sent`, and
materialized in Athena as document `b38a80d4-253e-4022-bcd5-3d620b5268ea` (`CREATED`, file size
5,562 bytes). Evidence: `RUNSHEET_ECM_PUBLISH_828_MINIO_CAPACITY_20260622.md`.

**Honest defect note (regression-net gap):** A1 #848's `build_transfer_source_node_id` change broke
the existing `test_ecm_transfer_receiver_adapter.py::test_build_payload_folds_identity_into_stable_source_node_id`
(it asserted the OLD version-scoped `!=`). The build's regression check ran the enqueue + worker
suites but NOT that direct test, so main went briefly red after #848 and was unblocked by **#852**
(assertions synced to A1: changed-version/file `==`, changed-role `!=`). Process lesson: when
changing a pure function, grep for + run its dedicated test, not only the call-site suites.

## 5. Remaining — design/direction/scaling-gated (NOT silently-unfinished code)

- **B Opt-1 — conflict auto-republish (ratify-needed).** Was gated on A (now done). On
  `conflict_after_sent`, re-enqueue a publish so the changed content revisions the Athena doc (A1's
  stable identity + #849's latest-wins make the mechanics clean). The open fork (owner's call): is
  conflict-after-sent a **process violation to surface** (today's Opt-2) or a **legitimate re-publish
  to automate** (Opt-1)? Design-lock: `DESIGN_ECM_PUBLISH_CONFLICT_AFTER_SENT_PROPOSAL_20260621.md`
  (+ the A1-informed update). **Not autobuilt — it picks a product semantic.**
- **A2 — obsolete/withdraw with NO successor (Opt-4 interim).** No successor publish → nothing to
  revision; not propagated. An Athena withdraw API (Opt-1) is cross-repo and deliberately NOT built.
- **Multi-worker per-lineage lock (scaling-gated).** #849's check-before-dispatch suffices for the
  current SINGLE dedicated `ecm-publication-worker`; >1 concurrent worker needs a per-lineage DB
  lock / claim ordering. Not needed at current scale.
- **D — status read-back / mail→PLM (direction-gated).** Would re-open the locked one-directional
  design; out of this line unless directionality is re-opened.
- **MinIO capacity / fresh-upload path (#828) — CLOSED on staging.** Root cause was host root disk
  full, not MinIO bucket/quota. After reclaim, a genuinely fresh controlled STEP upload was stored in
  MinIO and published to Athena end to end. Future disk maintenance remains ops hygiene, not an
  unfinished ECM-publish feature.

## 6. Conclusion

The PLM→ECM publish line is functionally complete and verified (durable reachability + C + B Opt-2
+ A1, all on main, CI-green). The next genuine feature is **B Opt-1** — design-locked, awaiting an
owner ratify on the automate-vs-surface fork. A2 / D / mail / multi-worker-lock are deliberately out
of scope (direction- or scaling-gated). No unfinished code remains on the built surface.
