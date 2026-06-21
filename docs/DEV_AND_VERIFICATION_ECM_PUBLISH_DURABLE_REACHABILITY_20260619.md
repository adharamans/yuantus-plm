# ECM Publish Durable Reachability Closeout

Date: 2026-06-19; S4 re-run completed 2026-06-21
Status: VERIFIED - reachability + S3 initial drain + S4 post-recreate persistence gate passed; S5 not run
Authoritative taskbook: `docs/DEVELOPMENT_ECM_PUBLISH_DURABLE_REACHABILITY_TASKBOOK_20260617.md`

## Summary

Worker-to-Athena reachability, an initial live drain (S3), and the durable
persistence / recreate-survival gate (S4 re-run) are verified on the current
staging project `yuantus-latest-check`.

What is established: the static compose checks passed, the shared
`ecm-publish-net` path is in use, the Yuantus `ecm-publication-worker` resolves
and reaches Athena through the stable `athena-ecm-core` alias, an initial
release drained to an Athena document (S3), and a release issued AFTER the worker
container was recreated also drained to an Athena document (S4 re-run). The
original 2026-06-19 S4 candidate remains recorded as non-evidence because it was
co-released with S3; the 2026-06-21 re-run is the actual persistence proof.

The receipt has one accepted deviation from the canonical taskbook:

- D1 deviation: staging project name is `yuantus-latest-check`, not canonical
  `yuantusplm`.

S5 resilience was not executed. It remains optional/recommended.

## Scope

Repos and landed code:

| Repo | Canonical URL | Relevant merge |
|---|---|---|
| Athena | `zensgit/Athena` | `067bd03` / PR #25: ECM publish receiver opt-in override |
| Yuantus | `adharamans/yuantus-plm` | `fcc9528a` / PR #796: Yuantus api gate + opt-in publish override |

Deploy host facts observed during closeout:

| Item | Value |
|---|---|
| Host | `23.254.236.11` / `racknerd-0de8668` |
| Athena deploy dir | `/home/mainuser/Athena` |
| Yuantus deploy dir | `/home/mainuser/yuantus-latest-check` |
| Docker | `29.1.3` |
| Docker Compose | `v5.1.1` |
| jq | `1.6` |
| ECM env file | `/home/mainuser/yuantus-latest-check/.ecm-publish.env`, mode `600` |

No Transfer Receiver secret, DB credential, or secret-bearing environment value
is recorded in this document.

## Verification Receipt

Gate decisions acknowledged:

- D1 = staging deviation accepted (`yuantus-latest-check`)
- D2 = shared external network `ecm-publish-net`
- D3 = explicit env, no hardcoded settings default
- G1 / G2 + F1..F6 + G3 override split acknowledged

Shared network:

```text
ecm-publish-net exists: pass
```

### Static Config Checks

Athena:

```text
PASS A1: base lacks ecm-publish-net
PASS A2: merged ecm-core has ecm-network + ecm-publish-net
PASS A3: athena-ecm-core alias present
```

Yuantus:

```text
PASS Y1: base lacks ecm-publish-net
PASS Y2: base lacks ecm-publication-worker
PASS Y3: drainer has default + ecm-publish-net
```

These checks were run against the deploy-host compose chain, including the
staging-local Yuantus override and the Athena prod/ghcr overrides.

### Network Evidence

`ecm-publish-net` contains only the intended cross-stack endpoints:

```text
athena-ecm-core-1 172.23.0.2/16
yuantus-latest-check-ecm-publication-worker-1 172.23.0.3/16
```

The old ad-hoc linkage is absent: `athena-ecm-core-1` is no longer attached to
`yuantus-latest-check_default`.

From inside `yuantus-latest-check-ecm-publication-worker-1`:

```text
getent hosts athena-ecm-core => 172.23.0.2 athena-ecm-core
GET http://athena-ecm-core:8080/actuator/health => 200
```

### S3 Live Drain

```text
outbox_id = c9c39dd2571943698962cae4e7be5a08
state = sent
reason = NULL
attempt_count = 1
athena_document_id = fe3cbdfb-9951-4dd6-a895-6db337f544b9
athena_disposition = CREATED
no_conflict = true
created_at = 2026-06-19 11:22:00.749625+00
dispatched_at = 2026-06-19 11:22:19.721527+00
```

### S4 Original Candidate - NOT EVIDENCE

The 2026-06-19 S4 candidate did not establish the gate: it had `created_at`
11:22:00.850187, ~100ms after the S3 row and ~19s BEFORE S3 dispatched
(11:22:19.721527). That candidate is retained below as a false-green guard, not
as the persistence proof.

```text
outbox_id = 873fbe40c9de47d492d101f3f05b51f2
state = sent
reason = NULL
attempt_count = 1
athena_document_id = 58ff0fa4-6ff1-400c-9aaa-0b76db51031d
athena_disposition = CREATED
no_conflict = true
created_at = 2026-06-19 11:22:00.850187+00
dispatched_at = 2026-06-19 11:22:20.782159+00
after_recreate = false
```

### S4 Re-Run Persistence Proof - PASS

The S4 gate was re-run on 2026-06-21 with the hardened runsheet §6b sequence:
capture current worker identity, recreate the drainer, verify DNS/health from
inside the new drainer, then create a fresh disposable release whose outbox row
must satisfy the in-SQL `after_recreate` assertion.

Worker recreate evidence:

```text
BEFORE = cdae6790... / 2026-06-19T06:08:05Z
AFTER  = d04be601b6914df6ead6f2165ef7440ec39d1e618d4cb7e48f276fe6bdb8b7b6 / 2026-06-21T02:22:15.233419543Z
ID_CHANGED = PASS
getent hosts athena-ecm-core => 172.23.0.2 athena-ecm-core
GET http://athena-ecm-core:8080/actuator/health => 200
No docker network connect or ad-hoc linkage used during the re-run
```

Entitlement precondition restored for the re-run:

```text
meta_app_licenses contains tenant-1 / plm.ecm_publish / Active / expires=NULL
EntitlementService.is_entitled("ecm_publish") under tenant-1/org-1 => True
```

Post-recreate disposable release:

```text
item_id = durable-s4rerun-20260621T110004Z-25920ad7-item
version_id = durable-s4rerun-20260621T110004Z-25920ad7-version
file_id = durable-s4rerun-20260621T110004Z-25920ad7-file
released_at = 2026-06-21 11:00:05.222486
```

Gate SQL result:

```text
outbox_id = f7ef781fcd5d4ba396a5e3d0250f69ac
state = sent
reason = NULL
attempt_count = 1
athena_document_id = cd70d79b-1744-4dc6-87fa-adf20f94f0da
athena_disposition = CREATED
no_conflict = true
after_recreate = true
created_at = 2026-06-21 11:00:05.093292+00
dispatched_at = 2026-06-21 11:00:18.766265+00
```

### S5 Resilience

S5 was not executed in this pass.

```text
Status: not executed
Classification: optional/recommended, not required for this closeout
```

## Caveats

MinIO returned `XMinioStorageFull` during disposable upload. The S3/S4 releases
therefore reused an existing disposable STEP object as the file byte source
while creating new item/version/file/outbox release rows.

This preserves the worker-to-Athena reachability and persistence proof, but it
is not evidence of a fresh MinIO upload path.

The S4 re-run required restoring the `ecm_publish` entitlement on staging. A
staging-only Ed25519 signed license was generated with an ephemeral private key,
self-verified, and imported through `LicenseImportService` for
`tenant-1 / plm.ecm_publish`. This exercises the real signed-license import path
for staging, but is not evidence of production vendor-license issuance tooling.

## Final Status

```text
Static + network + DNS + health: PASS
D1 deviation: accepted for staging, project name = yuantus-latest-check
S3 SQL live-drain (initial): PASS
S4 persistence proof (post-recreate release survives): PASS
S5 resilience: not executed
Fresh MinIO upload path: not proven due XMinioStorageFull caveat
Production vendor-license issuance tooling: not proven; staging-only signed license used
```

The ECM publish reachability path is verified on the current staging project for
static config, network, DNS/health, an initial live drain (S3), and a
post-recreate live drain (S4 re-run), with the D1 staging deviation, MinIO
caveat, and staging-license caveat recorded. This closes the durable
worker-to-Athena reachability gate for controlled rollout.
