# #828 — ECM-publish staging MinIO capacity — TRIAGE + FIX RUNSHEET

Status: **VERIFIED 2026-06-22/23** (host `23.254.236.11` / `metasheet-main`, user `mainuser`).
Read-only triage found the root cause; post-reclaim verification proved fresh upload -> release ->
ECM publish. Future WRITE actions remain gated; do NOT run additional prunes without owner approval.
Scope: ops hygiene only; does NOT touch the ECM-publish product surface (line is functionally closed-out).

---

## 1. Root cause (confirmed, not assumed)

`XMinioStorageFull` on the ECM-publish path is **a host-disk-full symptom, NOT a MinIO bucket/quota problem.**

- Host root `/dev/vda2`: **77G total, 73G used, 704M avail, 100% full.**
- The ECM MinIO bucket is **nearly empty**: volume `yuantus-latest-check_minio_data` = **19.52 kB**; in-container `du -sh /data` = **152 K** (`/data/yuantus` = 20 K). All three MinIO buckets on the host are KB-scale (`yuantus-p2-observation` 1.79 MB, `athena_minio_data` 15 kB).
- So MinIO cannot write even a few KB because the underlying filesystem `/` is at 100%. Fixing capacity = **reclaiming host disk**, not purging MinIO.

## 2. Evidence (read-only `docker system df` + `df` + `du`, 2026-06-22)

| Category | Total | Reclaimable | Note |
|---|---|---|---|
| Host `/` | 77G | — | **73G used, 704M free, 100%** |
| Docker images | 42.91 GB | **13.92 GB (32%)** | 122 images, only **1 dangling** -> needs `image prune -a` (not plain `prune`) to reclaim |
| Docker local volumes | 14.48 GB | 2.228 GB (15%) | 12 unused volumes (see §3 caution) |
| Docker containers | 607.8 MB | 0 | all 38 active |
| journald | 224 MB | (negligible) | not worth touching vs the 14 GB |

**What the 13.92 GB actually is:** images with NO associated container. **All 38 host containers are
running (running=38/all=38, zero stopped), so every deployed image is protected** — `athena-ecm-core:latest`
3.06 GB, `odoo:16`, `collabora/code`, `elasticsearch:8.11.1` etc. are all IN USE (their containers run)
and are NOT reclaim candidates. The reclaimable set is purely orphaned builds; the largest identifiable
are 7+ old `ghcr.io/zensgit/metasheet2-backend:<sha>` CI images at **797 MB each (~5.6 GB)**. The prune
prompts before deleting — review the full `docker images` list there.

**Post-reclaim observation (2026-06-23T02:38Z):** host `/dev/vda2` had recovered to **77G total,
39G used, 35G available, 53%**. This is enough headroom for MinIO writes; no MinIO bucket purge was
needed.

## 3. Reclaim plan — asymmetric risk, do them in this order

**PRIMARY (safe, biggest win) — unused images -> ~13.92 GB.**
Removes images with NO associated container. **All 38 host containers are currently running
(running=38/all=38, zero stopped)**, so every deployed stack — full Athena (incl. `athena-ecm-core`
3 GB, odoo, collabora, ES, grafana, keycloak), both Yuantus projects, metasheet current+staging,
vemcad, p2-observation — KEEPS its images. What gets reclaimed is purely orphaned: old CI SHA builds
(the 7+ `metasheet2-backend:<sha>` at 797 MB each) and superseded layers. Only residual risk: rolling
back to an old SHA-tagged build would re-pull. This step ALONE takes the host from 100% (704 M free) to
~77% (~14 G free) — enough to un-stick MinIO.

**SECONDARY (optional, CAUTION — only if more headroom needed) — unused volumes -> ~2.2 GB.**
`docker volume prune` deletes the 12 currently-unused volumes. **2 of them are POSTGRES DATA volumes
from the metasheet line** — destroying them destroys those DBs:
- (!) `metasheet_metasheet-postgres-data`
- (!) `metasheet2_metasheet-staging-postgres-data`
The other 10 are monitoring/cache/import (`*-prometheus-data`, `*-grafana-data`, `*-alertmanager-data`,
`*-redis-data`, `*-attendance-import-data`, `athena_clamav_data`, `athena_prometheus_data`) — low-risk
but still review. **Do NOT blanket `volume prune`.** Confirm the metasheet stacks are permanently down
and their DB data is unwanted BEFORE pruning, or prune by name excluding the 2 postgres volumes.

**DO NOT:** purge any MinIO bucket (they are KB), and do not delete in-use images/volumes.

## 4. Exact commands (future use only; GATED)

```bash
# --- re-confirm state immediately before any prune (read-only) ---
ssh metasheet-main 'df -h /; docker system df'

# --- PRIMARY: reclaim unused images (~13.92 GB). Review the kill-list first: ---
ssh metasheet-main 'docker image ls --format "{{.Size}}\t{{.Repository}}:{{.Tag}}" | sort -h -r | head -30'
ssh metasheet-main 'docker image prune -a'          # prompts y/N; or -af to skip the prompt
ssh metasheet-main 'df -h /; docker system df'       # verify headroom recovered

# --- SECONDARY (only if still tight): review the 12 unused volumes, then prune SELECTIVELY ---
ssh metasheet-main 'docker volume ls -f dangling=true'
#   keep the 2 metasheet postgres volumes unless owner confirms they are disposable, e.g.:
#   docker volume rm <name> ...   (by name, excluding the postgres data volumes)
```

## 5. Fresh-upload -> publish verification — PASSED

Durable-reachability S3/S4 reused an existing disposable STEP object because MinIO was full. After
host headroom recovered, the fresh upload -> publish path was proven with a new controlled STEP
object generated through Yuantus' application services.

Proof run:

- Run: `20260623T024315Z`
- File: `ecm828-fresh-20260623T024315Z-fd8d2ac716.step`
- S3 key: `ecm-publish-828-fresh/20260623T024315Z-fd8d2ac716/ecm828-fresh-20260623T024315Z-fd8d2ac716.step`
- Size: **5,562 bytes**
- SHA-256: `5211202883fcadf2934aec568c3a01fdac3cbcc09e2284f1477be693271cd8ff`
- Item: `ecm828-20260623T024315Z-fd8d2ac716-item`
- Version: `6b489691-4362-498d-acf9-3c833c461bf2`
- File id: `ecm828-20260623T024315Z-fd8d2ac716-file`
- Outbox id: `16735daf19044065a4ba8c0a1d38f5c9`
- Athena document id: `b38a80d4-253e-4022-bcd5-3d620b5268ea`
- Athena disposition: `CREATED`

Evidence:

1. **MinIO stored genuinely new bytes.** S3 list after the run contained the proof object above
   with size **5,562 bytes**. Bucket totals were `object_count=3`, `total_size=6037` bytes: the
   original S3/S4 object (121 bytes), a 354-byte orphan from a failed first proof attempt whose DB
   transaction rolled back, and the final linked proof object (5,562 bytes).
2. **Yuantus outbox drained.** Row `16735daf19044065a4ba8c0a1d38f5c9` had
   `state='sent'`, `reason=NULL`, `attempt_count=1`, `created_at=2026-06-23 02:43:19.699430+00`,
   `dispatched_at=2026-06-23 02:43:30.315552+00`, and no `conflict_after_sent`.
3. **Athena materialized the document.** Athena `nodes` had
   `b38a80d4-253e-4022-bcd5-3d620b5268ea` as an active `DOCUMENT` named
   `ecm828-fresh-20260623T024315Z-fd8d2ac716.step`; Athena `documents` recorded `file_size=5562`;
   `versions` recorded version `1.0` with `file_size=5562`; `transfer_node_mappings` mapped
   `source_repository_id='yuantus-plm'` to that local node.

Pass = bucket grew with new bytes + outbox `sent`/`reason=NULL` + Athena doc materialized. This
closes #828's fresh-upload gap that durable-reachability deliberately did not cover.

## 6. Remaining caution

- #828 is proven for the staging `yuantus-latest-check` fresh upload -> release -> Athena publish path.
- **Still do not run blanket `docker volume prune`.** The metasheet Postgres volumes remain destructive
  cross-line risk unless an owner separately declares them disposable.
- Future disk maintenance can use §4 as a gated runbook, but no additional write action is required
  for ECM-publish #828 closure.
