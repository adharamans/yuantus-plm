# ECM Publish — P1D Transfer Receiver Phase-0 Smoke Kit (Dev & Verification)

Date: 2026-06-16

Follows:
- `docs/DEVELOPMENT_ECM_PUBLISH_P1D_RETARGET_TRANSFER_RECEIVER_TASKBOOK_20260616.md`
- `docs/DEV_AND_VERIFICATION_ECM_PUBLISH_P1D_TRANSFER_RECEIVER_ADAPTER_IMPL_20260616.md`

## 1. What this adds

This slice adds an operator-run live verifier:

`scripts/ecm_publish_phase0/transfer_receiver_smoke.py`

The script is the executable Phase-0 kit for Athena Transfer Receiver U1-U5:

1. `GET /api/v1/transfer/receiver/verify?folderId=...`
2. `POST /api/v1/transfer/receiver/folders` for the smoke item folder.
3. `POST /api/v1/transfer/receiver/folders` for the smoke version folder.
4. `POST /api/v1/transfer/receiver/documents` and expect `CREATED`.
5. Replay the same `sourceNodeId + sourceLastModifiedAt` and expect `UNCHANGED`.
6. Create a second version folder and upload with a new version identity, expecting `CREATED`.

The script defaults to dry-run and performs **no network I/O** unless `--yes-live` is
provided. It accepts both `YUANTUS_`-prefixed and unprefixed setting names.

## 2. Required live inputs

Live mode requires:

- `YUANTUS_PUBLICATION_ECM_BASE_URL` or `YUANTUS_ATHENA_BASE_URL`
- `YUANTUS_PUBLICATION_ECM_TRANSFER_USER`
- `YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET`
- `YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID`
- `YUANTUS_PUBLICATION_ECM_SOURCE_REPOSITORY_ID` (defaults to `yuantus-plm`)
- `YUANTUS_PUBLICATION_ECM_CONFLICT_POLICY` (defaults to `SKIP`)
- `YUANTUS_PUBLICATION_ECM_PHASE0_FILE` or `--file`

Example dry-run:

```bash
python3 scripts/ecm_publish_phase0/transfer_receiver_smoke.py
```

Example live run:

```bash
python3 scripts/ecm_publish_phase0/transfer_receiver_smoke.py \
  --yes-live \
  --file /path/to/disposable-controlled-file.step \
  --prefix phase0-operator-20260616
```

The script emits JSON and never prints the transfer secret.

## 3. Safety boundaries

- Dry-run mode is the default.
- Live mode requires explicit `--yes-live`.
- The generated smoke identity uses a unique prefix so repeated operator runs do not collide
  with production-controlled records.
- The script uses the same Transfer Receiver headers and identity folding as the production
  `AthenaTransferReceiverAdapter`.
- U4 is strict: replay must return `UNCHANGED`, otherwise the smoke fails.
- U5 is strict: a new PLM version identity must return `CREATED`, otherwise the smoke fails.

## 4. Verification in this PR

Local verification:

```bash
python3 -m pytest src/yuantus/meta_engine/tests/test_ecm_transfer_receiver_phase0_smoke.py -q
```

Result:

```text
5 passed
```

The tests use `httpx.MockTransport` and do not call Athena. They cover:

- dry-run does not require live env and does not print secrets;
- live mode fails closed when required env is missing;
- U1-U5 request order and Transfer headers;
- U4 requires `UNCHANGED`;
- U1 requires the expected `repositoryId`.

The new test is registered in `.github/workflows/ci.yml` and `conftest.py`.

## 5. Current live status

This PR does **not** claim live Athena U1-U5 completion. The current local environment does
not provide the required `YUANTUS_PUBLICATION_ECM_*` / `YUANTUS_ATHENA_*` variables. When a
live Athena receiver registration and disposable controlled file are available, run the
script with `--yes-live` and archive the emitted JSON as the Phase-0 evidence.
