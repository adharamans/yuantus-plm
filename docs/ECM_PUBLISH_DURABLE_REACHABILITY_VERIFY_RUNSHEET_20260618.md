# ECM Publish — Durable Reachability Verify Runsheet

Date: 2026-06-18
Status: §6 run on staging -> PARTIAL (S4 persistence NOT established). B partial
committed as a checkpoint (branch `docs/ecm-durable-reachability-closeout`,
`0005a7ca`, NOT on main; see `DEV_AND_VERIFICATION_ECM_PUBLISH_DURABLE_REACHABILITY_20260619.md`).
Next step = §6b S4 re-run (C), then amend partial -> full PASS, then merge/PR.
Authoritative spec: `docs/DEVELOPMENT_ECM_PUBLISH_DURABLE_REACHABILITY_TASKBOOK_20260617.md`
(taskbook §6 = recipe, §7 = receipt skeleton).

This runsheet does **not** duplicate the taskbook. It adds:

- A §0 prerequisite checklist (post Yuantus repo transfer to `adharamans/yuantus-plm` on 2026-06-18).
- An ergonomic fillable copy of the §7 receipt the owner returns when S1-S5 are done.
- The owner-side window/execution-model choice that the taskbook leaves implicit.

The Transfer Receiver secret + DB credentials are NEVER written into this file, returned in this receipt, or echoed in any shell history captured here.

## §0 Prerequisites (owner runs on deploy host before §6 S1)

These items were prepped only on the dev box; only owner can confirm they hold on the deploy host:

1. **Yuantus clone origin re-point** (post-transfer). On the deploy host's Yuantus checkout:

   ```bash
   git -C <yuantus-path> remote -v
   # If origin still points at git@github.com:zensgit/Yuantus.git (pre-transfer),
   # re-point it to the new canonical:
   git -C <yuantus-path> remote set-url origin git@github.com:adharamans/yuantus-plm.git
   git -C <yuantus-path> fetch origin
   ```

   This must be done before any `git pull origin main` on the host. Athena's repo did not move; its `origin` is unchanged.

2. **Repos on the expected SHAs** (origin/main verified on dev box 2026-06-18):

   | Repo | origin/main HEAD | Carries |
   |---|---|---|
   | Athena | `067bd03` (PR #25) | `docker-compose.ecm-publish.yml` override + base list→dict |
   | Yuantus | `f77d749d` (post-transfer cleanup, after PR #796 `fcc9528a`) | `docker-compose.ecm-publish.yml` override + base api gate env |

   On the host:

   ```bash
   git -C <athena-path>  fetch origin && git -C <athena-path>  checkout main && git -C <athena-path>  pull --ff-only origin main
   git -C <yuantus-path> fetch origin && git -C <yuantus-path> checkout main && git -C <yuantus-path> pull --ff-only origin main
   git -C <athena-path>  log --oneline -1   # expect 067bd03 …
   git -C <yuantus-path> log --oneline -1   # expect f77d749d … (or later main HEAD)
   ```

3. **`jq` available** (the override-merge structural checks in taskbook §6 S1 require it):

   ```bash
   command -v jq >/dev/null || sudo apt-get install -y jq   # or: brew install jq
   ```

4. **Idempotent shared network** (taskbook §5.1):

   ```bash
   docker network inspect ecm-publish-net >/dev/null 2>&1 \
     || docker network create ecm-publish-net
   ```

5. **Live env block staged in owner's shell, not in any file in either repo** (taskbook §6 S1):

   `YUANTUS_PUBLICATION_ECM_TRANSFER_USER`, `_SECRET`, `_ROOT_FOLDER_ID` are operator-managed and never committed. Owner exports them in the session shell that runs S1.

## §6 execution model — owner picks (A) solo or (B) co-execution

The recipe in taskbook §6 has owner-only steps baked in (S3/S4/S5 each call for "operator prepares + releases a disposable controlled STEP file through the normal release path: api → release() → outbox enqueue"). That cannot be fabricated from a shell — it requires a real release in the PLM UI/API against a real tenant/org/item. So full autonomous execution is off the table regardless of host access. The choice is whether co-execution helps:

**Model A — owner solo on deploy host.** Owner runs S1, all three triggered releases for S3/S4/S5, all SQL verifies, DNS evidence collection, persistence proof, optional resilience proof. Returns the filled §7 receipt below. Best when host SSH from dev box is not desired or scheduled separately.

**Model B — co-execution over SSH.** Owner triggers the three releases, exports secrets, and answers go/no-go for window. Dev box (Claude) drives the deterministic, non-secret deploy-host steps: pre-execution config checks (G3.3/G3.5 grep + `jq`), `docker compose up -d` calls, DNS `docker exec` evidence collection, SQL state queries, persistence proof container recreate, optional resilience proof. The Transfer Receiver secret never passes through the dev-box tool layer — owner exports it directly in the live host shell. Best when owner wants to compress wall-clock for a brittle window and have a written transcript of every command.

Pick before §6 S1.

## §6 disruptive-window caveat

S1 opens with `docker compose down` on both stacks. This is a disruptive redeploy on a shared host, not a read-only check. S4 forcibly recreates the drainer container. S5 (optional) stops `ecm-core` long enough for an in-flight release to land in `state='pending'` with future `next_attempt_at`. Owner picks the window; any other live consumer of either stack on that host is briefly unavailable.

## §6b — S4 re-run (C): turnkey for the next host window

The §6 pass on staging established everything EXCEPT S4: the two outbox rows were
co-released (both `created_at` 11:22:00, ~100ms apart), so neither was a release
issued AFTER the worker recreate — the persistence gate is not met. B partial is
committed as a safe checkpoint (branch `docs/ecm-durable-reachability-closeout`,
`0005a7ca`, NOT on main). To earn full PASS, re-run ONLY S4 with correct
sequencing — the release in step 6 MUST happen after the recreate in step 4.

```bash
set -euo pipefail   # any compose/exec/SQL step failing aborts the run, not silently continues
cd /home/mainuser/yuantus-latest-check
PROJ=yuantus-latest-check
W=${PROJ}-ecm-publication-worker-1
PG=${PROJ}-postgres-1
# the deploy host's actual staging chain - MUST match the original §6 up exactly.
# the deploy host runs the staging-local override; omitting it would recreate the
# drainer with the wrong service/network definition:
COMPOSE="docker compose -p $PROJ -f docker-compose.yml -f docker-compose.staging-local.yml -f docker-compose.ecm-publish.yml"

# 1) confirm the prior S3 row drained (sanity, not the gate)
docker exec $PG psql -U yuantus -d yuantus -x -c "
  select id, state, dispatched_at from meta_ecm_publication_outbox
  where target_system='athena' order by created_at desc limit 3;"

# 2) BEFORE: capture worker identity into shell vars (two space-separated fields)
BEFORE=$(docker inspect -f '{{.Id}} {{.State.StartedAt}}' $W)
BEFORE_ID=${BEFORE%% *}; BEFORE_STARTED=${BEFORE##* }
echo "BEFORE_ID=$BEFORE_ID BEFORE_STARTED=$BEFORE_STARTED"

# 3) recreate (NO docker network connect anywhere)
$COMPOSE --profile ecm-publish rm -sf ecm-publication-worker
$COMPOSE --profile ecm-publish up -d ecm-publication-worker

# 4) AFTER: Id MUST differ, StartedAt MUST be later
AFTER=$(docker inspect -f '{{.Id}} {{.State.StartedAt}}' $W)
AFTER_ID=${AFTER%% *}; AFTER_STARTED=${AFTER##* }
echo "AFTER_ID=$AFTER_ID AFTER_STARTED=$AFTER_STARTED"
[ "$AFTER_ID" != "$BEFORE_ID" ] && echo "PASS: container id changed" || { echo "FAIL: id unchanged (recreate did not happen)"; exit 1; }

# 5) repeat S2 DNS+health from inside the recreated drainer (curl, else python3 fallback)
docker exec $W getent hosts athena-ecm-core
docker exec $W sh -c 'if command -v curl >/dev/null 2>&1; then curl -sS -o /dev/null -w "%{http_code}\n" http://athena-ecm-core:8080/actuator/health; else python3 -c "import urllib.request as u; print(u.urlopen(\"http://athena-ecm-core:8080/actuator/health\").getcode())"; fi'

# 6) >>> operator: trigger a NEW release now (api -> release() -> outbox enqueue),
#     strictly AFTER step 4. Reusing an existing STEP object is fine (no MinIO upload).

# 7) gate SQL: assert IN-SQL that the new row was created after the recreate.
#    after_recreate is machine-checked (created_at > AFTER_STARTED) - no eyeballing.
docker exec $PG psql -U yuantus -d yuantus -x -c "
  select id, created_at, state, reason, attempt_count,
         properties->>'athena_document_id' as doc_id,
         properties->>'athena_disposition' as disposition,
         (properties->>'conflict_after_sent') is null as no_conflict,
         (created_at > '${AFTER_STARTED}'::timestamptz) as after_recreate
  from meta_ecm_publication_outbox
  where target_system='athena' order by created_at desc limit 1;"
```

Pass bar (the step-7 row): `after_recreate=t` (SQL-asserted: created_at > AFTER_STARTED)
- this is the column that closes the original false-green gap, machine-checked not
eyeballed; `AFTER_ID` != `BEFORE_ID`; `state=sent`; `reason=NULL`; `attempt_count>=1`;
`doc_id` NOT NULL; `disposition` in {CREATED,RENAMED,OVERWRITTEN,UNCHANGED,SKIPPED};
`no_conflict=t`. (docker StartedAt is RFC3339Nano; Postgres ::timestamptz parses it and
rounds to microseconds on the same host clock - harmless.)

Return for the partial -> full-PASS amend of `0005a7ca`: new outbox `id` + `created_at`,
the `after_recreate` boolean from step 7 (must be `t`), BEFORE/AFTER `Id` + `StartedAt`,
`doc_id`, `disposition`. Optionally run S5 in the same window. Then the doc is amended to
full verified and the branch merges/PRs to main.

## §7 receipt — fillable

The owner returns the filled block below (or pastes it as a `Yuantus/docs/DEV_AND_VERIFICATION_ECM_PUBLISH_DURABLE_REACHABILITY_<date>.md` close-out). Mirrors taskbook §7 verbatim; placeholders use `___`. Redact: never write the Transfer Receiver secret, DB credentials, or any per-tenant identifiers that policy says stay off-doc.

```text
Gate decisions acknowledged (D1=i / D2=C / D3=i / G1 / G2 + F1..F6 + G3 override-split): ___
Shared network created idempotently (docker network ls | grep ecm-publish-net): ___

Athena compose change — base (no-semantic list→dict, file + line + commit): docker-compose.yml line 88-89, commit 067bd03
Athena compose change — override file (Athena/docker-compose.ecm-publish.yml, commit): 067bd03
Athena cross-reference doc (Athena/docs/ATHENA_ECM_PUBLISH_RECEIVER_NETWORK_20260617.md, commit): ___ (already in repo, owner fills the in-tree SHA)

Yuantus compose change — base (api gate flag only; drainer + networks
  moved to override per G3.5, file + line + commit): docker-compose.yml line 108, commit fcc9528a (#796)
Yuantus compose change — override file (Yuantus/docker-compose.ecm-publish.yml,
  drainer + top-level networks, profiled drainer, commit): fcc9528a (#796)
RUNBOOK rollout section (file + section anchor + commit hash): ___

Pre-execution config checks (G3.3 + G3.4 + G3.5 — operator on deploy host BEFORE up):
  Athena base alone does NOT contain ecm-publish-net    => ___ (pass / fail)
  Athena base+override KEEPS ecm-core on ecm-network AND
    adds ecm-publish-net (jq structural check, not just
    alias grep — guards against merge→replace surprise) => ___ (pass / fail)
  Athena base+override produces athena-ecm-core alias   => ___ (pass / fail)
  Yuantus base alone does NOT contain ecm-publish-net   => ___ (pass / fail)
  Yuantus base alone does NOT contain
    ecm-publication-worker (comment lines are stripped
    by `docker compose config`; the check is against
    the rendered config, not the raw file)              => ___ (pass / fail)
  Yuantus base+override+profile drainer attaches to BOTH
    default AND ecm-publish-net (jq structural check)   => ___ (pass / fail)

DNS evidence from inside drainer container (G2):
  getent hosts athena-ecm-core      => <ip> athena-ecm-core
  curl /actuator/health             => 200
  No 'docker network connect' used  => ___ (operator attests / shell history evidence)

SQL verify #1 after initial S1 up (mirrors smoke assertion bar):
  outbox_id <-> doc_id              => ___ <-> ___
  state / reason / attempt_count    => sent / NULL / ___
  disposition / no_conflict         => ___ / t

Persistence proof S4 (container recreate, no operator intervention between S3 and S4):
  DNS evidence #2 still passes      => ___
  SQL verify #2 outbox <-> doc_id   => ___ <-> ___

Optional S5 resilience (Athena down/up):
  Interim row state non-'sent' with future_retry=t  => ___
  Recovered to 'sent' after Athena up: outbox <-> doc => ___ <-> ___
```

## Post-receipt closeout (owner + dev-box co-execute)

Once the receipt is returned filled:

1. `Yuantus/docs/DEV_AND_VERIFICATION_ECM_PUBLISH_DURABLE_REACHABILITY_<date>.md` lands as the close-out commit on Yuantus side (records Athena #25 / Yuantus #796 merge SHAs, the canonical URL `adharamans/yuantus-plm`, the S1-S5 results, the filled §7 receipt). The current "live-ready for controlled rollout" claim in `DEV_AND_VERIFICATION_ECM_PUBLISH_P1E_LIVE_CLOSEOUT_AND_WORKER_E2E_PLAN_20260617.md` §8 upgrades to "verified durable reachability".
2. Athena-side memory `project_plm_ecm_publish_integration.md` refresh — drops the OUTDATED 2026-06-02 narrative (CMIS Browser, Keycloak service account, Phase 0 CMIS, `Yuantus-ecm-publish` worktree) and adopts the actual line (Transfer Receiver + BASIC auth + symmetric opt-in override + this receipt as the close-out evidence). MEMORY.md line 29 OUTDATED guard is removed in the same commit.
3. If verification fails: per the P0/P1/P2/P3 ladder, scope stays narrow — fix in place (compose merge / network alias / env / BASIC auth / drainer DNS / SQL state / worker retry / persistence), re-run S1-S5, do not expand into adjacent slices.
