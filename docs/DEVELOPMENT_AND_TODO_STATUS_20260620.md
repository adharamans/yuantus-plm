# Development & TODO Status — ECM Publish Durable Reachability + Yuantus Follow-ups

Date: 2026-06-20
Updated: 2026-06-21 after #826 merged to main
Scope: the parallel tracks worked this session — ECM publish durable reachability
(P0), MinIO storage (P1), Yuantus roadmap hygiene (P2), and the post-closeout
Athena memory refresh (#26). This is a status + TODO snapshot; the authoritative
recipes/specs live in the cross-referenced docs at the bottom.

> Honesty note: P0's persistence gate is verified on staging AND the closeout is now
> MERGED to main (#826 / `e2537da4`). #26 (Athena memory) is done. P1 (MinIO) is the one
> open ops tail; S5 resilience is optional/not-run. Claims here stay scoped to the evidence.

## Track status at a glance

| Track | Item | Status | Gate / blocker | Next action |
|---|---|---|---|---|
| **P2** | Roadmap reclassification | ✅ DONE (merged) | — | — (#818 → main `87efa7c5`) |
| **P0** | ECM durable reachability | ✅ DONE (on main) | — | — (#826 → main `e2537da4`) |
| **P1** | MinIO `XMinioStorageFull` | ⏸ BLOCKED | live-host ops access | bucket/volume triage |
| **#26** | Athena memory refresh | ✅ DONE | — | — |

## P2 — Yuantus roadmap hygiene ✅ DONE

- Reclassified the three landed CAD-PDM C3 follow-ups from out-of-scope / optional
  → **landed** in `DEVELOPMENT_ROADMAP_AND_TODO_20260617.md` (§3 + §5.2 + a dated §6 note):
  `find_effective_version` NULL-start → **#804** (`bd867d4a`); C3 BOM-line
  (`item_id`-scoped) effectivities → **#806** (`8e98698b`); standalone C3 worker-daemon
  CLI → **#803** (`fc034f30`).
- §5.2 closure **narrowed to the three-item list** — does NOT falsely close the other
  open tails (§2 `MES_INGEST_TENANT_ID` attribution; #804's "no-Date = always-effective?"
  ratifiable semantic note). Baseline kept at `main@1eebc293` (no full reconciliation).
- Branch `docs/roadmap-refresh`, rebased clean onto `origin/main`, commit `013686da`.
- **PR #818 MERGED → main** (squashed to `87efa7c5`, 2026-06-20T17:31Z): https://github.com/adharamans/yuantus-plm/pull/818 — doc-only.
- ✅ Track closed — nothing else here.

## P0 — ECM publish durable reachability ✅ DONE (closeout on main)

- **Verified on staging** (host `23.254.236.11`, project `yuantus-latest-check`):
  static config A1–A3 / Y1–Y3; shared `ecm-publish-net`; in-drainer DNS + `/actuator/health`
  200; **S3 initial drain** (outbox → Athena doc, disposition CREATED); **S4 post-recreate
  drain** (`after_recreate=t`, outbox `f7ef781fcd5d4ba396a5e3d0250f69ac` → Athena doc
  `cd70d79b-1744-4dc6-87fa-adf20f94f0da`, disposition CREATED).
- **Closeout MERGED to main:** PR **#826** squash-merged as `e2537da4` (doc-only, verified;
  `DEV_AND_VERIFICATION_ECM_PUBLISH_DURABLE_REACHABILITY_20260619.md`, S4 PASS). Branch deleted.
- **Caveats retained:** MinIO fresh upload path remains unproven due `XMinioStorageFull`;
  S4 used a staging-only Ed25519 signed license imported through the real license-import path
  for `tenant-1 / plm.ecm_publish`; this is not production vendor-license issuance proof.
- ✅ Track CLOSED on main (#826 / `e2537da4`). #26 Athena memory refresh also done.

## P1 — MinIO `XMinioStorageFull` ⏸ BLOCKED (independent)

- Staging MinIO returned `XMinioStorageFull` on disposable upload; S3/S4 reused an existing
  STEP object (reachability proof preserved; fresh-upload path NOT proven).
- **Independent of the P0 persistence gate** — the S4 re-run reuses an object, so it does
  NOT wait on MinIO. This track only blocks the separate fresh-upload→publish verification.
- **Gate:** live-host ops access.
- TODO **[owner/ops, any time]**: check bucket/volume usage on staging MinIO; purge
  disposable/test objects or expand capacity; then verify a fresh-upload→publish path.

## #26 — Athena memory refresh ✅ DONE

- Added an ad-hoc memory update note recorded outside the repo (in the Codex memory store).
- The note supersedes the older point-in-time PLM-to-ECM assumptions (CMIS Browser,
  Keycloak service account, Phase 0 CMIS validation) with the current verified line:
  Transfer Receiver + custom credential headers (`X-Athena-Transfer-User` / `X-Athena-Transfer-Secret`, not HTTP BASIC) + symmetric opt-in override + #826 durable reachability closeout.
- `MEMORY.md` was not edited directly; the update follows the current memory extension
  convention of appending an ad-hoc note for the generator/registry to consume.

## Consolidated TODO (ordered)

1. ✅ **DONE** — roadmap PR **#818** merged to main (`87efa7c5`). — P2
2. ✅ **DONE** — runsheet **§6b** S4 re-run on staging; `after_recreate=t`, `sent`, doc id recorded. — P0
3. ✅ **DONE** — closeout PR **#826** merged to main (`e2537da4`). — P0
4. **[owner/ops, independent]** MinIO storage triage (bucket/volume; purge/expand). — P1
5. ✅ **DONE** — Athena memory refreshed via ad-hoc note; `MEMORY.md` left to the generator/registry path. — #26

No further Yuantus-local buildable line is open in this status set. Per the roadmap §5, the
remaining product items (jti revocation denylist, MetaSheet bridge) are owner/cross-repo
SSO-gated and are deliberately NOT auto-picked. The only open tail here is the independent
MinIO storage ops item.

## Authoritative cross-references

- Recipe/spec: `Yuantus/docs/DEVELOPMENT_ECM_PUBLISH_DURABLE_REACHABILITY_TASKBOOK_20260617.md` (§6 recipe, §7 receipt)
- Owner runsheet (turnkey §6b C re-run): `Yuantus/docs/ECM_PUBLISH_DURABLE_REACHABILITY_VERIFY_RUNSHEET_20260618.md`
- Verified closeout (on main): `Yuantus/docs/DEV_AND_VERIFICATION_ECM_PUBLISH_DURABLE_REACHABILITY_20260619.md` (#826 / `e2537da4`)
- Roadmap working doc + PR: `Yuantus/docs/DEVELOPMENT_ROADMAP_AND_TODO_20260617.md` · adharamans/yuantus-plm **#818**
