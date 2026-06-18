# ECM Publish — Durable Worker↔Athena Reachability (taskbook)

Date: 2026-06-17
Status: **gate-3 complete (with G3.5 E2 symmetric override)** (D1 = i, D2 = C,
D3 = i, plus G1 producer+drainer wiring and G2 DNS evidence). Second-gate
request-changes applied in two rounds; third-gate boundary correction landed
with the Yuantus-side symmetric override:

- Round 1: **F1** api enqueue gate, **F2** drainer DB/tenancy baseline +
  `--tenant`/`--org`, **F3** SQL verification in §6 instead of docker-exec
  smoke.
- Round 2: **F4** §6 S1 env exports must precede `docker compose up` (api
  binds `YUANTUS_ECM_PUBLISH_ENABLED` at startup), **F5** SQL `-d` placeholder
  for multi-tenant `DATABASE_URL_TEMPLATE` rather than hardcoded `yuantus`,
  **F6** §6 S5 retry-wait observable corrected (state='pending' / reason=NULL
  per `service.py:369-371`, not preserved as 'remote_error').
- Polish: S1 exports decommented for direct copy-paste; S5
  `cd /path/to/Athena &&` prefixes on the ecm-core stop/up commands.
- **Round 3 (G3 boundary correction + E2 symmetric override)**:
  - **G3.1 Athena override split** — Athena base compose stays
    ECM-publish-unaware. The `ecm-publish-net` join + alias moves out of
    the base into a NEW `Athena/docker-compose.ecm-publish.yml` override
    applied via `-f docker-compose.yml -f docker-compose.ecm-publish.yml`;
    the base keeps only the no-semantic `ecm-network: {}` list→dict
    conversion.
  - **G3.2 idempotent network create** — `docker network inspect … >/dev/null
    2>&1 || docker network create …` replaces the bare `create` so the
    recipe is safe in deploy scripts and re-run-safe.
  - **G3.3 pre-execution config checks** — §5.2(c) + §5.3(d) + §6 S1 + §7
    receipt include `docker compose config | grep` + `jq` sanity tests
    that prove each base is clean and each override produces the right
    merged result, run on the deploy host BEFORE bringing up.
  - **G3.4 Yuantus profile-gating sanity** — superseded / expanded by
    G3.5 below. The earlier single-check version (unprofiled `config`
    does NOT include `ecm-publication-worker`) is now part of the
    triple-check in G3.5.
  - **G3.5 Yuantus symmetric override (E2)** — applies the same boundary
    principle on the Yuantus side: drainer service + top-level
    `ecm-publish-net: external` decl move out of base into a NEW
    `Yuantus/docker-compose.ecm-publish.yml` override (mirrors the Athena
    override). Base only carries the api `YUANTUS_ECM_PUBLISH_ENABLED`
    gate env. Eliminates the compose-version sensitivity of the prior
    asymmetric structure (different Compose versions validate external
    networks at different stages; service-absence-in-unprofiled-config
    did NOT guarantee that vanilla `up` succeeds with the network
    absent). §5.3(d) / §6 S1 now triple-check: base lacks
    `ecm-publish-net`, base lacks `ecm-publication-worker`, and
    base+override+profile drainer is attached to BOTH `default` AND
    `ecm-publish-net`.

A/B/C/D landed in parallel before G3 (Athena main `09cc5a0`+`34f0240`,
Yuantus main `5ae1f583`+`cc733755`) carry the P0 "base compose always-on
service joins external network" issue. Athena's variant is superseded by
A2/D2 commits; Yuantus's variant is superseded by E2. Squash plan pairs
each P0-v1 commit with its v2 fix before push, so origin/main never sees
the intermediate P0 state. Verification S1-S5 remains owner-executed on
the deploy host per §6.
Follows:

- `docs/DEV_AND_VERIFICATION_ECM_PUBLISH_P1E_LIVE_CLOSEOUT_AND_WORKER_E2E_PLAN_20260617.md`
  (§6 reachability bullet, §8 live-ready gate conditions)
- `docs/DEV_AND_VERIFICATION_ECM_PUBLISH_WORKER_E2E_SMOKE_20260617.md`

## 1. Why this slice exists (the gap)

PR #774-#776 declared ECM publish **live-ready for controlled rollout**, subject
to the three gates listed in `…P1E_LIVE_CLOSEOUT_…20260617.md` §8: restart-only
kill-switch caveat, per-tenant `ecm_publish` entitlement, and the
`ECM_PUBLISH_ENABLED` global gate. Worker→Athena reachability was **not** listed
as a gate; it sits as an operational note in §6 of the same doc.

The live worker E2E run on 2026-06-17 (outbox row `cbee4abd…`, Athena document
`8dd4c6be…`, §7) passed only after two ad-hoc, runtime-only acts on the deploy
host:

1. `docker network connect <yuantus-default-net> athena-ecm-core-1`
2. `export YUANTUS_PUBLICATION_ECM_BASE_URL=http://athena-ecm-core-1:8080`

Neither act is encoded in either repo's `docker-compose.yml`, the Yuantus
RUNBOOK, an `.env` example, or a deploy script. **Container recreate on the
deploy host recreates the broken state**, the worker correctly enters
`inconclusive_retrying` (a Phase-0 design success), and live publishing silently
stalls until ops manually replays both ad-hoc steps. The §8 "live-ready" claim
is therefore brittle: it survives the *operator's* knowledge of §6, not config.

This slice closes that gap by encoding the reachability path as compose +
RUNBOOK config, and by adding a verification recipe that proves it survives a
`down + up` cycle without manual `docker network connect`.

## 2. Scope and boundary

**In scope**

- A shared external Docker network (`ecm-publish-net`), declared `external: true`
  in both `Athena/docker-compose.yml` and `Yuantus/docker-compose.yml`.
- Athena `ecm-core` service joins the shared network with an explicit alias
  (`athena-ecm-core`) so the URL is decoupled from the auto-generated container
  name (`athena-ecm-core-1`, project-name-dependent).
- A new **dedicated, profile-gated** `ecm-publication-worker` service on the
  Yuantus side that runs `yuantus ecm-publication-worker` (not the generic
  `yuantus worker`) with its own ECM publish env block. Profile-gated so a
  routine `docker compose up` never accidentally spins a live drainer.
- A RUNBOOK section that documents the rollout, with explicit guidance to set
  `YUANTUS_PUBLICATION_ECM_BASE_URL` (not lean on the legacy
  `YUANTUS_ATHENA_BASE_URL` fallback in `adapter_registry.py:29-33`).
- A verification recipe (§6) that proves the path survives a container recreate
  with **no** `docker network connect` step and **with** DNS evidence from
  inside the drainer container.

**Out of scope** (explicit — do not expand the slice)

- Kill-switch model upgrade. `ECM_PUBLISH_ENABLED` stays restart-only per §8
  caveat; that's a separate slice.
- Transfer Receiver BASIC-auth hardening (e.g. service-account migration). The
  P1D retarget locked BASIC for live; out of this scope.
- "fail-open virus scan is global" (#19 carry-over flagged in
  `docs/CLAMAV_STAGING_TRIAGE_20260527.md` §4). Product track paused; not opened
  here.
- Promoting the ad-hoc `yuantus-latest-check` compose project name to a
  long-lived variant. Canonical project is `yuantusplm` per
  `docs/RUNBOOK_RUNTIME.md:55`; today's smoke project name was one-off.
- Semantic changes to `settings.py` / `application.yml`. No code change in this
  slice; pure compose + RUNBOOK + receipt.

## 3. Gate decisions (acknowledged)

### D1 — compose project name: `yuantusplm`

Per `docs/RUNBOOK_RUNTIME.md:55`. The `yuantus-latest-check` project name from
today's smoke is a one-off and is not promoted by this slice. All compose
commands in §5-§6 use `-p yuantusplm`.

### D2 — shared external network with alias (option C, corrected)

A single external network named `ecm-publish-net`, created once per deploy host
via the idempotent guard (full form + rationale in §5.1):

```bash
docker network inspect ecm-publish-net >/dev/null 2>&1 \
  || docker network create ecm-publish-net
```

Both compose stacks declare it `external: true`. **Athena gives `ecm-core` an
explicit alias on the shared network only**, so the in-cluster URL does not
depend on Docker's auto-generated container name (which is project-name- and
replica-index-dependent and brittle):

| Object | Value |
|---|---|
| Network name (both sides) | `ecm-publish-net` |
| Driver | bridge (default) |
| Athena service joining it | `ecm-core` |
| Athena alias on shared net | `athena-ecm-core` |
| Yuantus service joining it | `ecm-publication-worker` (new, see G1) |
| In-cluster URL | `http://athena-ecm-core:8080` |

Only the two services above join `ecm-publish-net`; the rest of each stack stays
on its own default network. Minimal cross-stack blast radius.

### D3 — env var persistence is documentation, not a code default

The URL goes in the operator env block and the RUNBOOK example — **not** as a
hardcoded default in `settings.py`. Per `adapter_registry.py:29-33`:

```python
base = (
    getattr(s, "PUBLICATION_ECM_BASE_URL", "")
    or getattr(s, "ATHENA_BASE_URL", "")
    or ""
).strip()
```

The resolver is fail-CLOSED to Null only when **both** `PUBLICATION_ECM_BASE_URL`
and `ATHENA_BASE_URL` are empty (along with `TARGET_SYSTEM` and the row's
`target_system` matching). The rollout block in §5.3 / §5.4 therefore
**explicitly sets `YUANTUS_PUBLICATION_ECM_BASE_URL`** and **does not carry the
legacy `YUANTUS_ATHENA_*` vars at all** on the drainer service, so there is no
path by which an inherited `ATHENA_BASE_URL` silently routes ECM publish
traffic through an unintended host.

## 4. Added gate conditions

### G1 — Every ECM-publish participant must be wired explicitly

The integration has **two** in-process participants, and both must be wired in
compose; missing either silently degrades the path.

**Producer (api process).** `release()` is invoked from the api request
handler; per `src/yuantus/meta_engine/version/service.py:617`, it short-circuits
when `ECM_PUBLISH_ENABLED` is false. The current `api` service env block
(`docker-compose.yml:66-125`) carries the legacy `YUANTUS_ATHENA_*` vars and
many CAD/dedup vars, but has **no** `YUANTUS_ECM_PUBLISH_ENABLED`. Without that
flag flowing into the api process, `release()` returns early and **no outbox
row is ever enqueued** — the drainer would have nothing to drain, and any
smoke would fail at the `blocked` precondition (no due-pending row).

**Drainer (ecm-publication-worker process).** `Yuantus/docker-compose.yml:200-261`
defines a service named `worker` whose `command:` is `yuantus worker
--poll-interval 5 …` (line 245-255): that is the **generic** Yuantus job
worker. Its env block carries the legacy `YUANTUS_ATHENA_*` vars
(line 222-228) but has **no** `YUANTUS_PUBLICATION_ECM_*` and **no**
`YUANTUS_ECM_PUBLISH_ENABLED`. The CLI `yuantus ecm-publication-worker`
(declared at `src/yuantus/cli.py:221`, covered by
`src/yuantus/meta_engine/tests/test_ecm_publication_worker_cli.py`) is **never
invoked** by the current compose set. The live smoke that passed today ran the
drainer as a one-shot script
(`scripts/ecm_publish_phase0/worker_e2e_smoke.py`) — not as a persistent
service. The CLI does accept `--tenant`/`--org` (`cli.py:228-229`) which set
the request contextvars (`tenant_id_var.set` / `org_id_var.set`,
`cli.py:242-244`); in `db-per-tenant` / `db-per-tenant-org` modes
`get_db_session()` requires those vars (`database.py:292-294`), so a drainer
spawned without `--tenant`/`--org` raises `MissingTenantContextError` on the
first DB call.

**Decision.** Two compose changes (both detailed in §5.3):

1. Add `YUANTUS_ECM_PUBLISH_ENABLED` to the **api** service env block —
   producer gate only. The Transfer Receiver secret and base URL stay on the
   drainer service, never on the api (api only needs to know whether to
   enqueue).
2. Introduce a new **`ecm-publication-worker`** service, **profile-gated**
   behind a new `ecm-publish` compose profile so a routine `docker compose up`
   does not spin a live drainer. The new service uses the existing `worker`'s
   DB / tenancy / storage env block as a baseline (so multi-tenant modes get
   the right session context), **drops the legacy `YUANTUS_ATHENA_*` and the
   CAD/dedup env block** (drainer never reads them; carrying
   `YUANTUS_ATHENA_BASE_URL` would re-open the `adapter_registry.py:31`
   fallback), adds the `YUANTUS_PUBLICATION_ECM_*` block, and passes
   `--tenant`/`--org` on the command line. The existing `worker` service is
   **untouched**.

### G2 — Receipt must include DNS evidence from inside the drainer container

The smoke result alone (state=`sent`) is insufficient to prove the alias path,
because a host-network shortcut could pass the smoke while leaving the in-cluster
DNS broken for the next container recreate. The receipt (§7) therefore requires,
from **inside the drainer container** (not the host):

1. `getent hosts athena-ecm-core` → resolves to an IP on `ecm-publish-net`
2. `curl -sS -o /dev/null -w '%{http_code}\n' http://athena-ecm-core:8080/actuator/health`
   → `200`
3. No `docker network connect` invocation appears anywhere in the setup chain
   (S1 of §6).

## 5. Plan

### 5.1 Shared external network — one-time per deploy host (idempotent)

```bash
docker network inspect ecm-publish-net >/dev/null 2>&1 \
  || docker network create ecm-publish-net
```

Idempotent guard: the `inspect` short-circuits when the network already exists,
so the command is safe to embed in deploy scripts and to re-run after host
reboots without manual special-casing.

### 5.2 Athena compose change — split base + override file

The Athena change is **split** so the base compose stays ECM-publish-unaware
(not every Athena deployment runs ECM publish). Three parts:

(a) **`Athena/docker-compose.yml` `ecm-core` service `networks:` list → dict —
no semantic change.** Convert the list form `- ecm-network` (line 87-88) to
dict form. No new network reference is added in the base compose; the
conversion exists solely so the override file in (b) can MERGE additional
network entries into the service's `networks:` map (compose merges dict-form
network maps across `-f` overrides, but list-form is replaced wholesale).

```yaml
    networks:
      ecm-network: {}
    restart: unless-stopped
```

(b) **NEW `Athena/docker-compose.ecm-publish.yml`** — override file that
scopes the cross-stack reachability. Apply via `-f docker-compose.yml -f
docker-compose.ecm-publish.yml`; the override can be appended after any other
overrides (e.g. `docker-compose.prod.yml`, ghcr-image overrides) so it
composes cleanly with existing deploy invocations.

```yaml
services:
  ecm-core:
    networks:
      ecm-publish-net:
        aliases:
          - athena-ecm-core

networks:
  ecm-publish-net:
    external: true
    name: ecm-publish-net
```

(c) **Pre-execution config checks** — sanity tests that confirm the scope is
clean and the override merge does not silently replace `ecm-core.networks`.
Documented in receipt §7 as a commit-precondition; the operator runs them on
the deploy host (no Docker daemon on the authoring dev box):

```bash
# 1) Athena base ALONE must NOT reference ecm-publish-net (proves the base
#    stays ECM-publish-unaware).
docker compose -f docker-compose.yml config \
  | grep -q ecm-publish-net \
  && { echo "FAIL: base compose leaks ecm-publish-net"; exit 1; } || true

# 2) Base + ECM publish override MUST keep BOTH ecm-network AND add
#    ecm-publish-net on ecm-core in the merged config. A grep-alias check
#    alone is insufficient: it would pass even if the override REPLACED
#    (rather than merged) ecm-core.networks — leaving ecm-core off
#    ecm-network and unable to reach postgres / elasticsearch / redis /
#    rabbitmq / minio / keycloak. This jq assertion catches that
#    failure mode at config-resolution time.
docker compose -f docker-compose.yml -f docker-compose.ecm-publish.yml \
    config --format json \
  | jq -e '.services["ecm-core"].networks | has("ecm-network") and has("ecm-publish-net")' \
    >/dev/null \
  || { echo "FAIL: override broke ecm-core networks (lost ecm-network or missing ecm-publish-net)"; exit 1; }

# 3) Override must also produce the alias athena-ecm-core (belt-and-suspenders
#    semantic check on top of the structural check above).
docker compose -f docker-compose.yml -f docker-compose.ecm-publish.yml config \
  | grep -q athena-ecm-core \
  || { echo "FAIL: override missing athena-ecm-core alias"; exit 1; }
```

`jq` is widely available; install it on the deploy host if missing
(`apt-get install jq` / `brew install jq`).

No env block change on `ecm-core`. No other Athena service joins
`ecm-publish-net`; only `ecm-core` is reachable cross-stack. `docker-compose.prod.yml`
and ghcr-image overrides are not modified by this slice — they compose with
the new override file via the `-f` chain.

### 5.3 Yuantus compose change — split base + override file (symmetric with Athena)

Same boundary principle as Athena (§5.2): not every Yuantus deployment runs
ECM publish, so the base compose must stay ECM-publish-unaware. The drainer
service and the top-level external network move to a NEW override file; only
the `api` producer gate env (a single env var with default false, no compose
coupling) stays in base. **G3.5 second-gate correction**, post-G3.1 Athena
split: the prior asymmetric structure (Yuantus base carrying the top-level
`ecm-publish-net: external: true` decl + profile-gated drainer) was
version-dependent — different Compose versions validate external networks at
different stages, and service-absence-in-unprofiled-config does **not**
guarantee that vanilla `docker compose -p yuantusplm up -d` succeeds when the
external network is absent. Symmetric override eliminates the sensitivity
entirely.

Four parts:

(a) **`api` service — keep the producer enqueue gate** (one env line in the
api `environment:` block, before the `# Athena (optional)` comment at line
101). The api process is where `release()` decides whether to enqueue; the
gate is a single env var with default false and no compose coupling — it
stays in base. The Transfer Receiver secret + base URL live on the drainer
service in the override file (c) below, never on api.

```yaml
      # ECM publish enqueue gate — read by release() at
      # src/yuantus/meta_engine/version/service.py:617. Producer-side only;
      # the Transfer Receiver secret and base URL live on
      # ecm-publication-worker (in the override file), never here.
      YUANTUS_ECM_PUBLISH_ENABLED: ${YUANTUS_ECM_PUBLISH_ENABLED:-false}
```

(b) **Base removals — drainer + top-level networks leave base.** The
previously-in-base `ecm-publication-worker` service (profile-gated) and the
top-level `networks:` block (containing the external `ecm-publish-net`
decl) are both **REMOVED** from `Yuantus/docker-compose.yml`. After E2 the
base contains zero ECM publish coupling beyond the api gate env in (a).
A vanilla `docker compose -p yuantusplm up -d` brings up postgres / minio /
api / worker / scheduler / dedup-vision / cad-extractor with no dependency
on `ecm-publish-net` existing.

(c) **NEW `Yuantus/docker-compose.ecm-publish.yml`** — override file that
scopes the drainer + the cross-stack network. Mirrors the Athena override
(§5.2b). Apply by appending `-f docker-compose.ecm-publish.yml` to the
canonical invocation; the drainer remains `profiles: ["ecm-publish"]` as a
second explicit gate so even with the override file applied, a routine
`up -d` without `--profile ecm-publish` keeps the drainer off.

```yaml
services:
  ecm-publication-worker:
    profiles: ["ecm-publish"]
    build:
      context: .
      dockerfile: Dockerfile.worker
      args:
        PIP_INDEX_URL: ${PIP_INDEX_URL:-}
        PIP_TRUSTED_HOST: ${PIP_TRUSTED_HOST:-}
    environment:
      # --- DB / tenancy / storage baseline (mirrors `worker` env in base) ---
      YUANTUS_DATABASE_URL: postgresql+psycopg://yuantus:yuantus@postgres:5432/yuantus
      YUANTUS_SCHEMA_MODE: migrations
      YUANTUS_TENANCY_MODE: ${YUANTUS_TENANCY_MODE:-single}
      YUANTUS_DATABASE_URL_TEMPLATE: ${YUANTUS_DATABASE_URL_TEMPLATE:-}
      YUANTUS_IDENTITY_DATABASE_URL: ${YUANTUS_IDENTITY_DATABASE_URL:-postgresql+psycopg://yuantus:yuantus@postgres:5432/yuantus_identity}
      YUANTUS_PLATFORM_ADMIN_ENABLED: ${YUANTUS_PLATFORM_ADMIN_ENABLED:-false}
      YUANTUS_PLATFORM_TENANT_ID: ${YUANTUS_PLATFORM_TENANT_ID:-platform}
      YUANTUS_STORAGE_TYPE: s3
      YUANTUS_S3_ENDPOINT_URL: http://yuantus-minio:9000
      YUANTUS_S3_PUBLIC_ENDPOINT_URL: http://localhost:59000
      YUANTUS_S3_BUCKET_NAME: yuantus
      YUANTUS_S3_ACCESS_KEY_ID: minioadmin
      YUANTUS_S3_SECRET_ACCESS_KEY: minioadmin
      YUANTUS_QUOTA_MODE: ${YUANTUS_QUOTA_MODE:-disabled}
      # --- ECM publish — REQUIRED for live rollout ---
      # Legacy YUANTUS_ATHENA_* deliberately NOT carried here.
      # adapter_registry.py:29-33 would fall back from PUBLICATION_ECM_BASE_URL
      # to ATHENA_BASE_URL, which could silently route ECM publish to an
      # unintended host. The drainer is explicit: PUBLICATION_ECM_BASE_URL or
      # nothing (resolver fails CLOSED to Null adapter).
      YUANTUS_ECM_PUBLISH_ENABLED: ${YUANTUS_ECM_PUBLISH_ENABLED:-false}
      YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM: ${YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM:-}
      YUANTUS_PUBLICATION_ECM_BASE_URL: ${YUANTUS_PUBLICATION_ECM_BASE_URL:-}
      YUANTUS_PUBLICATION_ECM_TRANSFER_USER: ${YUANTUS_PUBLICATION_ECM_TRANSFER_USER:-}
      YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET: ${YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET:-}
      YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID: ${YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID:-}
      YUANTUS_PUBLICATION_ECM_SOURCE_REPOSITORY_ID: ${YUANTUS_PUBLICATION_ECM_SOURCE_REPOSITORY_ID:-yuantus-plm}
    command:
      [
        "yuantus",
        "ecm-publication-worker",
        "--poll-interval",
        "5",
        "--worker-id",
        "${YUANTUS_ECM_PUBLICATION_WORKER_ID:-ecm-publication-worker-1}",
        "--tenant",
        "${YUANTUS_ECM_PUBLICATION_WORKER_TENANT_ID:-tenant-1}",
        "--org",
        "${YUANTUS_ECM_PUBLICATION_WORKER_ORG_ID:-org-1}",
      ]
    depends_on:
      api:
        condition: service_healthy
    networks:
      - default
      - ecm-publish-net
    restart: unless-stopped

networks:
  default: {}
  ecm-publish-net:
    external: true
    name: ecm-publish-net
```

(d) **Pre-execution config checks (Yuantus — symmetric to Athena §5.2c)**:

```bash
# 1) Yuantus base ALONE must NOT reference ecm-publish-net (proves the base
#    stays ECM-publish-unaware; vanilla `docker compose -p yuantusplm up -d`
#    has no dependency on the external network existing).
docker compose -p yuantusplm -f docker-compose.yml config \
  | grep -q ecm-publish-net \
  && { echo "FAIL: Yuantus base leaks ecm-publish-net"; exit 1; } || true

# 2) Yuantus base ALONE must NOT reference the drainer service.
docker compose -p yuantusplm -f docker-compose.yml config \
  | grep -q ecm-publication-worker \
  && { echo "FAIL: Yuantus base leaks ecm-publication-worker"; exit 1; } || true

# 3) Base + override + profile MUST resolve the drainer AND its network
#    attachments (default AND ecm-publish-net). jq structural check guards
#    against a merge surprise that drops one of the two networks.
docker compose -p yuantusplm \
    -f docker-compose.yml -f docker-compose.ecm-publish.yml \
    --profile ecm-publish config --format json \
  | jq -e '.services["ecm-publication-worker"].networks | has("default") and has("ecm-publish-net")' \
    >/dev/null \
  || { echo "FAIL: drainer missing default or ecm-publish-net in merged config"; exit 1; }
```

Notes:

- The generic `worker` service in base is **not modified** — its legacy
  `YUANTUS_ATHENA_*` env block stays as-is for whatever still uses it.
- The drainer in the override file joins both `default` (postgres / minio /
  api reachability) and `ecm-publish-net` (Athena reachability via
  `athena-ecm-core`). It does not need `extra_hosts: host.docker.internal`
  because all targets resolve via Docker DNS on the joined networks.
- It carries no `YUANTUS_ATHENA_*` env at all — the only ECM target is via
  the `PUBLICATION_ECM_*` block, and an unset `PUBLICATION_ECM_TARGET_SYSTEM`
  keeps the resolver at the Null adapter (fail-CLOSED), so a half-configured
  rollout cannot publish anywhere.
- `--tenant`/`--org` defaults match `bootstrap` (`tenant-1` / `org-1`) and
  the existing `worker` service so dev/staging keep parity; production
  must override via `YUANTUS_ECM_PUBLICATION_WORKER_TENANT_ID` /
  `_ORG_ID`.
- The drainer remains `profiles: ["ecm-publish"]` even inside the override
  file — a second explicit gate so a routine `up -d` against base+override
  (without `--profile ecm-publish`) still keeps the drainer off.

### 5.4 RUNBOOK + env example update

Add a section "ECM publish — controlled rollout" to `docs/RUNBOOK_RUNTIME.md`
(or a new `docs/RUNBOOK_ECM_PUBLISH_ROLLOUT.md` if the main RUNBOOK is too long
to extend), covering exactly these steps:

```bash
# One-time per deploy host (idempotent — safe to embed in deploy scripts)
docker network inspect ecm-publish-net >/dev/null 2>&1 \
  || docker network create ecm-publish-net

# Bring up Athena WITH the ECM publish override (append after any pre-existing
# overrides like docker-compose.prod.yml / docker-compose.ghcr.yml; the base
# compose alone does NOT join ecm-publish-net — that join lives in the
# override file added by §5.2(b)).
cd /path/to/Athena && docker compose \
  -f docker-compose.yml -f docker-compose.ecm-publish.yml up -d

# Bring up Yuantus WITH the ECM publish override (symmetric with Athena per
# G3.5; the base alone has zero ECM publish coupling beyond the api gate env)
cd /path/to/Yuantus

# REQUIRED for live ECM publish. Notes:
# - YUANTUS_ECM_PUBLISH_ENABLED flows into BOTH the api process (release()
#   enqueue gate at service.py:617) and the ecm-publication-worker process
#   (CLI). Both must see it as true; `docker compose up -d` picks the env var
#   up via the passthrough in each service's `environment:` block.
# - PUBLICATION_ECM_BASE_URL is the live receiver URL. Do NOT lean on
#   YUANTUS_ATHENA_BASE_URL; adapter_registry.py:29-33 falls back to it, but
#   that legacy path is for older Athena callers and can route ECM publish to
#   an unintended host.
export YUANTUS_ECM_PUBLISH_ENABLED=true
export YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM=athena
export YUANTUS_PUBLICATION_ECM_BASE_URL=http://athena-ecm-core:8080
export YUANTUS_PUBLICATION_ECM_TRANSFER_USER=<...>
export YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET=<...>
export YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID=<...>
export YUANTUS_PUBLICATION_ECM_SOURCE_REPOSITORY_ID=yuantus-plm

docker compose -p yuantusplm \
  -f docker-compose.yml -f docker-compose.ecm-publish.yml \
  --profile ecm-publish up -d

# Live kill-switch (restart-only, per …P1E… §6 / §8 — unchanged by this slice)
docker compose -p yuantusplm \
  -f docker-compose.yml -f docker-compose.ecm-publish.yml \
  --profile ecm-publish stop ecm-publication-worker
```

The Transfer Receiver secret is never written into compose / RUNBOOK / receipts.

An Athena-side cross-reference doc
(`Athena/docs/ATHENA_ECM_PUBLISH_RECEIVER_NETWORK_<date>.md`, one page,
points at the new `docker-compose.ecm-publish.yml` override file) is
**recommended** per second gate (Commit D in §9) — lightweight but protects
future Athena reviewers' context when they read the §5.2 base list→dict
conversion (which is no-semantic by itself) and the override file that
actually carries the cross-stack reachability.

## 6. Verification recipe (post-gate; deploy host)

The dev box that authored this taskbook has no Docker daemon and no access to
the deploy host, so verification is owner-executed. The recipe is sequenced to
make ad-hoc shortcuts impossible (S4 is the persistence proof).

```text
S1. Initial setup (clean state, no prior linkage)
    # G3.2: idempotent network create (safe in deploy scripts, re-run safe).
    docker network inspect ecm-publish-net >/dev/null 2>&1 \
      || docker network create ecm-publish-net

    # G3.3: pre-execution config checks (Athena). Confirm scope is clean
    # BEFORE bringing anything up — (1) base must be ECM-publish-unaware,
    # (2) override must MERGE (not replace) ecm-core.networks, (3) override
    # must produce the alias.
    cd /path/to/Athena
    docker compose -f docker-compose.yml config \
      | grep -q ecm-publish-net \
      && { echo "FAIL: Athena base compose leaks ecm-publish-net"; exit 1; } \
      || true
    docker compose -f docker-compose.yml -f docker-compose.ecm-publish.yml \
        config --format json \
      | jq -e '.services["ecm-core"].networks | has("ecm-network") and has("ecm-publish-net")' \
        >/dev/null \
      || { echo "FAIL: Athena override broke ecm-core networks (lost ecm-network or missing ecm-publish-net)"; exit 1; }
    docker compose -f docker-compose.yml -f docker-compose.ecm-publish.yml config \
      | grep -q athena-ecm-core \
      || { echo "FAIL: Athena override missing athena-ecm-core alias"; exit 1; }

    # Bring up Athena WITH the override (append any pre-existing overrides
    # like docker-compose.prod.yml / docker-compose.ghcr.yml before
    # ecm-publish.yml).
    docker compose down
    docker compose -f docker-compose.yml -f docker-compose.ecm-publish.yml up -d

    cd /path/to/Yuantus && docker compose -p yuantusplm down

    # G3.4 / G3.5 (symmetric override): three Yuantus config checks. Base
    # must not reference ecm-publish-net or ecm-publication-worker (both
    # moved to override docker-compose.ecm-publish.yml); base + override
    # + profile must produce the drainer attached to BOTH default and
    # ecm-publish-net (jq structural check guards against merge surprise).
    docker compose -p yuantusplm -f docker-compose.yml config \
      | grep -q ecm-publish-net \
      && { echo "FAIL: Yuantus base leaks ecm-publish-net"; exit 1; } \
      || true
    docker compose -p yuantusplm -f docker-compose.yml config \
      | grep -q ecm-publication-worker \
      && { echo "FAIL: Yuantus base leaks ecm-publication-worker"; exit 1; } \
      || true
    docker compose -p yuantusplm \
        -f docker-compose.yml -f docker-compose.ecm-publish.yml \
        --profile ecm-publish config --format json \
      | jq -e '.services["ecm-publication-worker"].networks | has("default") and has("ecm-publish-net")' \
        >/dev/null \
      || { echo "FAIL: drainer missing default or ecm-publish-net in merged config"; exit 1; }

    # CRITICAL ORDER (F4 second-gate correction): the api process reads
    # YUANTUS_ECM_PUBLISH_ENABLED at startup; if api comes up before the env
    # is in scope, release() will never enqueue regardless of the drainer
    # state. Run the export block BELOW (or `source .env.ecm-publish` if the
    # operator keeps a file) BEFORE the api `up`. Lines without `#` are
    # directly copy-pasteable; fill <...> with the live secret values.
    export YUANTUS_ECM_PUBLISH_ENABLED=true
    export YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM=athena
    export YUANTUS_PUBLICATION_ECM_BASE_URL=http://athena-ecm-core:8080
    export YUANTUS_PUBLICATION_ECM_TRANSFER_USER=<...>
    export YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET=<...>
    export YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID=<...>
    export YUANTUS_PUBLICATION_ECM_SOURCE_REPOSITORY_ID=yuantus-plm
    docker compose -p yuantusplm \
      -f docker-compose.yml -f docker-compose.ecm-publish.yml \
      --profile ecm-publish up -d
    # NO 'docker network connect' must be invoked anywhere in S1.

S2. DNS evidence (G2) — from INSIDE the drainer container
    docker exec yuantusplm-ecm-publication-worker-1 \
        getent hosts athena-ecm-core
    docker exec yuantusplm-ecm-publication-worker-1 \
        curl -sS -o /dev/null -w '%{http_code}\n' http://athena-ecm-core:8080/actuator/health
    # expect: a real IP for athena-ecm-core, and 200 from /actuator/health

S3. Live drain via daemon — SQL verification (no docker exec smoke; F3)
    # The smoke script (scripts/ecm_publish_phase0/worker_e2e_smoke.py) was the
    # one-shot validator for live readiness (WORKER_E2E_SMOKE_20260617). In
    # this slice the persistent daemon IS the production path; verifying via
    # the smoke would spawn a separate Python process that does not inherit
    # the daemon's tenant_id_var/org_id_var (set by the CLI's --tenant/--org)
    # and would raise MissingTenantContextError under db-per-tenant. Verify
    # via SQL against the same assertion bar instead.
    #
    # F5 second-gate correction: SQL must hit the SAME database the drainer
    # uses (database.py:292-298 resolves the session by tenant/org context):
    #   - TENANCY_MODE=single: the default database 'yuantus' is correct.
    #   - db-per-tenant / db-per-tenant-org: the database resolved from
    #     YUANTUS_DATABASE_URL_TEMPLATE for the drainer's --tenant (+ --org).
    # Set the placeholder before running the SQL below:
    export YUANTUS_VERIFY_DB_NAME=yuantus     # multi-tenant: override per row

    # operator prepares + releases a disposable controlled STEP file through
    # the normal release path (api → release() → outbox enqueue).
    # operator gets the new row's id:
    docker exec yuantusplm-postgres-1 psql -U yuantus -d "${YUANTUS_VERIFY_DB_NAME:-yuantus}" -c "
      select id, item_id, file_id, state, target_system, created_at
      from meta_ecm_publication_outbox
      where target_system = 'athena' order by created_at desc limit 3;
    "
    # daemon polls every 5s; wait ~10-30s then verify the row reached terminal
    # 'sent' with Athena properties (mirrors the smoke assertion bar in
    # docs/DEV_AND_VERIFICATION_ECM_PUBLISH_WORKER_E2E_SMOKE_20260617.md §1):
    docker exec yuantusplm-postgres-1 psql -U yuantus -d "${YUANTUS_VERIFY_DB_NAME:-yuantus}" -x -c "
      select state, reason, attempt_count,
             properties->>'remote_id'           as remote_id,
             properties->>'athena_document_id'  as doc_id,
             properties->>'athena_disposition'  as disposition,
             (properties->>'conflict_after_sent') is null as no_conflict
      from meta_ecm_publication_outbox
      where id = '<outbox-id>';
    "
    # Pass bar:
    #   state           = 'sent'
    #   reason          = NULL
    #   attempt_count   >= 1
    #   remote_id       NOT NULL
    #   doc_id          NOT NULL  (a Null run lacks doc_id; Null 'sent' never passes)
    #   disposition     IN {CREATED, RENAMED, OVERWRITTEN, UNCHANGED, SKIPPED}
    #   no_conflict     = t  (properties.conflict_after_sent absent/NULL)
    # Capture outbox_id <-> doc_id for the receipt.

S4. Persistence proof — the actual gate
    docker compose -p yuantusplm \
      -f docker-compose.yml -f docker-compose.ecm-publish.yml \
      --profile ecm-publish rm -sf ecm-publication-worker
    docker compose -p yuantusplm \
      -f docker-compose.yml -f docker-compose.ecm-publish.yml \
      --profile ecm-publish up -d ecm-publication-worker
    # repeat S2 (DNS evidence) -- must pass with NO operator action between
    # S3 and S4 (no docker network connect, no env re-export beyond what
    # compose picks up automatically).
    # operator releases a SECOND disposable controlled file (api → release() →
    # outbox enqueue, identical mechanism to S3).
    # repeat the S3 SQL verify against the new outbox row -- must pass.

S5. (optional, recommended) Resilience proof
    # Explicit cd so the operator can run S5 from any pwd (S3/S4 left them in
    # Yuantus; ecm-core lives in the Athena stack). Keep the -f override
    # flags on every Athena command so ecm-core stays attached to
    # ecm-publish-net through the stop/up cycle (a bare `docker compose up
    # -d ecm-core` without -f would recreate the container WITHOUT the
    # ecm-publish-net join).
    cd /path/to/Athena && docker compose \
      -f docker-compose.yml -f docker-compose.ecm-publish.yml stop ecm-core
    # operator releases a THIRD disposable
    # poll the new outbox row; expected interim state (NOT 'sent'):
    docker exec yuantusplm-postgres-1 psql -U yuantus -d "${YUANTUS_VERIFY_DB_NAME:-yuantus}" -x -c "
      select state, reason, attempt_count, next_attempt_at,
             (next_attempt_at > now()) as future_retry
      from meta_ecm_publication_outbox
      where id = '<outbox-id>';
    "
    # Expected retry-wait observable (F6 second-gate correction):
    #   state              = 'pending'
    #   attempt_count      >= 1
    #   next_attempt_at    > now()   (future_retry = t)
    #   reason             = NULL    (NOT 'remote_error': the retry reschedule
    #                                 explicitly clears row.reason +
    #                                 row.error_message at service.py:369-371;
    #                                 the failed/remote_error state is only an
    #                                 in-flight transient, not the stable
    #                                 observable)
    # This is the inconclusive_retrying design from WORKER_E2E_SMOKE_20260617
    # §1, proving an unreachable receiver surfaces as retry — never as a
    # silent 'sent'.
    cd /path/to/Athena && docker compose \
      -f docker-compose.yml -f docker-compose.ecm-publish.yml up -d ecm-core
    # daemon resumes on next tick (~5s); re-run the S3 SQL verify against
    # the third outbox row -- must reach state='sent' with full properties.
```

## 7. Receipt skeleton (for the close-out commit)

Owner returns this redacted; no secret values, no Transfer Receiver token, no
DB credentials.

```text
Gate decisions acknowledged (D1=i / D2=C / D3=i / G1 / G2 + F1..F6 + G3 override-split): ___
Shared network created idempotently (docker network ls | grep ecm-publish-net): ___

Athena compose change — base (no-semantic list→dict, file + line + commit): ___
Athena compose change — override file (Athena/docker-compose.ecm-publish.yml, commit): ___
Athena cross-reference doc (Athena/docs/ATHENA_..._20260617.md, commit): ___

Yuantus compose change — base (api gate flag only; drainer + networks
  moved to override per G3.5, file + line + commit): ___
Yuantus compose change — override file (Yuantus/docker-compose.ecm-publish.yml,
  drainer + top-level networks, profiled drainer, commit): ___
RUNBOOK rollout section (file + section anchor + commit hash): ___

Pre-execution config checks (G3.3 + G3.4 + G3.5 — operator on deploy host BEFORE up):
  Athena base alone does NOT contain ecm-publish-net    => ___ (pass / fail)
  Athena base+override KEEPS ecm-core on ecm-network AND
    adds ecm-publish-net (jq structural check, not just
    alias grep — guards against merge→replace surprise) => ___ (pass / fail)
  Athena base+override produces athena-ecm-core alias   => ___ (pass / fail)
  Yuantus base alone does NOT contain ecm-publish-net   => ___ (pass / fail)
  Yuantus base alone does NOT contain
    ecm-publication-worker                              => ___ (pass / fail)
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

Persistence proof S4 (container recreate, no operator intervention):
  DNS evidence #2 still passes      => ___
  SQL verify #2 outbox <-> doc_id   => ___ <-> ___

Optional S5 resilience (Athena down/up):
  Interim row state non-'sent' with future_retry=t  => ___
  Recovered to 'sent' after Athena up: outbox <-> doc => ___ <-> ___
```

## 8. Boundary reminder (mirrors §2 out-of-scope)

- Kill-switch model: stays restart-only. The new drainer service inherits the
  same caveat (`docker compose … stop ecm-publication-worker` is the pause).
- BASIC auth on Transfer Receiver is preserved (no auth change in this slice).
- The fail-open virus-scan path (#19 §4 "noted, not opened") is **not** touched.
- `yuantus-latest-check` compose project name is **not** promoted; canonical
  remains `yuantusplm`.
- The generic `worker` service env block does **not** receive
  `YUANTUS_ECM_PUBLISH_ENABLED`. If any background/scheduled path triggers a
  `release()` from the worker process, ECM publish enqueue will silently
  short-circuit there (fail-quiet: the release itself returns the version
  unchanged; only the ECM enqueue is skipped). The api path is the canonical
  caller; extending the gate to `worker` is a follow-on slice if
  background-release publishing is required.
- No `settings.py` / `application.yml` change. This slice is pure compose +
  RUNBOOK + verification.

## 9. Execution / second-gate handoff

This doc is the read-only taskbook. Code/config landing requires the **second
gate**:

1. Owner reviews this doc; confirms §5 diffs match intent and §6 recipe is
   sufficient.
2. After second gate, the executor (not the verifier) lands:
   - Commit A: Athena `docker-compose.yml` change per §5.2.
   - Commit B: Yuantus `docker-compose.yml` change per §5.3.
   - Commit C: Yuantus RUNBOOK addition per §5.4.
   - **Commit D (recommended per second gate)**: Athena-side cross-reference
     doc (`Athena/docs/ATHENA_ECM_PUBLISH_RECEIVER_NETWORK_<date>.md`,
     one-page pointer back to this taskbook). Lightweight but protects future
     Athena reviewers' context when they read the §5.2 compose diff.
3. Verification (S1-S5 in §6) is **owner-executed on the deploy host**; this
   dev box has no Docker daemon and no access to the host.
4. Owner returns the §7 receipt; a closing `docs/DEV_AND_VERIFICATION_ECM_PUBLISH_DURABLE_REACHABILITY_<date>.md`
   captures the receipt and marks the slice closed.

Until that close-out doc lands, the slice is **OPEN**; §8 of the P1E live
closeout doc remains accurate ("live-ready for controlled rollout") with the
known §6 reachability caveat unchanged.
