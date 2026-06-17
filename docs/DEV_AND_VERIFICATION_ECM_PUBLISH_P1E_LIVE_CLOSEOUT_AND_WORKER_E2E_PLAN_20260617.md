# ECM Publish — P1E Live Closeout + Worker E2E Plan (Dev & Verification)

Date: 2026-06-17
Status: Phase-0 Transfer Receiver live gate **passed**; worker end-to-end live
validation remains an operator-run follow-up.

Follows:

- `docs/DEVELOPMENT_ECM_PUBLISH_P1D_RETARGET_TRANSFER_RECEIVER_TASKBOOK_20260616.md`
- `docs/DEVELOPMENT_ECM_PUBLISH_P1D_READINESS_GATE_20260616.md`
- `docs/DEV_AND_VERIFICATION_ECM_PUBLISH_P1D_TRANSFER_RECEIVER_ADAPTER_IMPL_20260616.md`
- `docs/DEV_AND_VERIFICATION_ECM_PUBLISH_P1D_TRANSFER_RECEIVER_PHASE0_SMOKE_20260616.md`

## 1. What is now proven live

The Athena Transfer Receiver Phase-0 smoke was rerun against the live Docker-reachable
Athena instance after PR #773 (`61eba01d`, `fix(ecm): keep transfer receiver parent
scoped to root`).

Live receiver facts used by the operator run:

- receiver registration: `cli-smoke-20260617T010920Z`
- receiver registration id: `c4e60074-3da8-4408-9bee-f8f5f91854fd`
- auth type: `BASIC`
- root folder id: `67410d46-5711-4136-a928-18c239918656`
- root folder path: `/uploads`
- source repository id: `athena`
- disposable controlled file: `/tmp/disposable-controlled-file.step`
- smoke prefix: `phase0-operator-20260617b-20260617T014651Z`

The Transfer Receiver secret is deliberately **not** recorded here.

## 2. Live Phase-0 result

Command shape (with secret redacted):

```bash
docker exec \
  -e YUANTUS_PUBLICATION_ECM_BASE_URL=http://127.0.0.1:8080 \
  -e YUANTUS_PUBLICATION_ECM_TRANSFER_USER=chouhua \
  -e YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET='<redacted>' \
  -e YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID=67410d46-5711-4136-a928-18c239918656 \
  -e YUANTUS_PUBLICATION_ECM_SOURCE_REPOSITORY_ID=athena \
  athena-ecm-core-1 python3 /tmp/transfer_receiver_smoke.py \
  --yes-live --file /tmp/disposable-controlled-file.step \
  --prefix phase0-operator-20260617b-20260617T014651Z
```

Note: this historical operator run explicitly set `SOURCE_REPOSITORY_ID=athena`.
That value is acceptable for the smoke's idempotency namespace because it is stable, but
the worker checklist below uses the PLM sender identity default (`yuantus-plm`) to keep
the receiver repository id and sender/source identity clearly separated.

Result:

| Step | Assertion | Result |
|---|---|---|
| U1 | `/verify` reaches repository `athena` | passed |
| U2.item-folder | first folder create | `CREATED` |
| U2.version-folder | nested version folder create | `CREATED` |
| U3.document-created | first document upload | `CREATED` |
| U4.replay-unchanged | replay same source id + watermark | `UNCHANGED` |
| U5.version2-folder | second version folder create | `CREATED` |
| U5.version2-created | second version document upload | `CREATED` |

Conclusion: Gate 1 R1-R5 in
`docs/DEVELOPMENT_ECM_PUBLISH_P1D_READINESS_GATE_20260616.md` is now live-proven for
the Transfer Receiver surface used by the PLM adapter.

## 3. Why PR #773 mattered

The first live run failed at `U2.version-folder` with HTTP 403:

```text
Transfer receiver credentials do not permit folder: 380c7086-7f7a-4f01-bd4e-a088392108c9
```

The receiver authorizes the literal `parentFolderId` before it resolves
`sourceParentNodeId`. PR #773 changed both the production adapter and the smoke kit so
all folder/document calls keep `parentFolderId` scoped to the authorized receiver root,
while `sourceParentNodeId` carries the intended PLM nesting. The rerun above proves that
this is the correct live contract.

The smoke kit also keeps receiver and sender identities separate: U1 verifies the
receiver repository id (`PUBLICATION_ECM_EXPECTED_REPOSITORY_ID`, default `athena`),
while Transfer create/upload calls send `PUBLICATION_ECM_SOURCE_REPOSITORY_ID` as the
PLM-side source identity used by Athena idempotency mapping.

## 4. Current production-readiness state

Code now supports the real Transfer Receiver path:

- release path: `VersionService.release()` stamps provenance and enqueues controlled
  files when `ECM_PUBLISH_ENABLED=true` and `ecm_publish` is entitled.
- outbox: one row per controlled file in `meta_ecm_publication_outbox`.
- worker: `yuantus ecm-publication-worker` drains due outbox rows.
- adapter: `AthenaTransferReceiverAdapter` sends folders/documents to Athena Transfer
  Receiver when live ECM settings resolve.
- kill-switch: dispatch is target-scoped and restart-only, per
  `docs/DEVELOPMENT_ECM_PUBLISH_P1D_READINESS_GATE_20260616.md`.
- Phase-0 smoke: U1-U5 live passed after #773.

What is **not yet proven live** is the full PLM worker path:

```text
release() -> ECM outbox row -> ecm-publication-worker --once -> Athena Transfer Receiver -> outbox SENT
```

That path requires a live Yuantus deployment/database with a releasable item version,
`ecm_publish` entitlement, `ECM_PUBLISH_ENABLED=true`, and a controlled file whose
`system_path` can be read by `FileService.download_file`.

## 5. Worker E2E operator checklist

Run this only in an environment where the Yuantus worker can reach both its database and
the Athena Transfer Receiver endpoint.

1. Configure live ECM settings for the worker process:

```bash
export YUANTUS_ECM_PUBLISH_ENABLED=true
export YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM=athena
export YUANTUS_PUBLICATION_ECM_BASE_URL='<athena-transfer-base-url>'
export YUANTUS_PUBLICATION_ECM_TRANSFER_USER='<transfer-user>'
export YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET='<transfer-secret>'
export YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID='67410d46-5711-4136-a928-18c239918656'
export YUANTUS_PUBLICATION_ECM_SOURCE_REPOSITORY_ID=yuantus-plm
```

2. Prepare a disposable PLM item version with one controlled file role
   (`native_cad`, `drawing`, or `geometry`) whose file bytes are readable from the
   worker environment.

3. Ensure the tenant has the signed `ecm_publish` entitlement. The publish hook must
   use `EntitlementService.is_entitled("ecm_publish")`; do not bypass it for the live
   test.

4. Release the version through the normal release path.

5. Confirm a pending outbox row exists for the controlled file:

```sql
select id, item_id, version_id, file_id, file_role, target_system, state, reason
from meta_ecm_publication_outbox
where target_system = 'athena'
order by created_at desc
limit 5;
```

6. Run one worker batch:

```bash
yuantus ecm-publication-worker --once --worker-id ecm-live-smoke-20260617
```

7. Confirm the row moved to `sent` and carries Athena properties:

```sql
select id, state, reason, attempt_count, dispatched_at, properties
from meta_ecm_publication_outbox
where id = '<outbox-id>';
```

Expected:

- `state = 'sent'`
- `reason is null`
- `attempt_count >= 1`
- `properties.remote_id` is present
- `properties.athena_document_id` is present
- `properties.athena_disposition` is one of
  `{CREATED, RENAMED, OVERWRITTEN, UNCHANGED, SKIPPED}`

8. Re-run `yuantus ecm-publication-worker --once` with no new pending rows. Expected
   processed count: `0`.

## 6. Operational notes

- Do not write the Transfer Receiver secret to docs, PR bodies, screenshots, or CI logs.
- `ECM_PUBLISH_ENABLED` is restart-only in the current process model. Change the env and
  restart the worker; do not expect an already-running worker to hot-reload env changes.
- Keep the worker stopped when using the kill-switch to pause a live backlog. Rows already
  pending remain in the outbox until the worker resumes.
- `--tenant` and `--org` on `ecm-publication-worker` set context only; they do not scope
  which rows are drained.
- The live `127.0.0.1:8080` URL from the Athena container is container-local. From a
  host or a separate Yuantus deployment, use a network route that is actually reachable
  from that process.

## 7. Status

The Transfer Receiver adapter contract and Athena live receiver contract are now proven.
The remaining production-readiness evidence item is the worker E2E checklist in §5.
Once that checklist passes, ECM publish can be marked live-ready for controlled rollout
with the restart-only kill-switch caveat.
