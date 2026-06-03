# Compose SKU Profiles

> **Update (2026-06-03 · PLM-COLLAB-P0-B, #697):** the original `YUANTUS_ENABLE_COLLAB` env key was
> compose-only and silently dropped by `Settings(extra="ignore")` — a runtime no-op. It has been
> **retired** in favor of the real `YUANTUS_ENABLE_METASHEET` Settings flag (PLM-COLLAB-P0-A, #695),
> which actually mounts the inert MetaSheet collaboration bridge seam. `base` sets it `false`;
> `collab`/`combined` set it `true`. The env values below reflect the current `ENABLE_METASHEET` wiring.

## Goal

Land the missing deployment artifacts promised by the PLM / Metasheet packaging plan:

- `base`
- `collab`
- `combined`

The implementation stays pragmatic:

- do not replace the existing Yuantus compose stack
- do not invent a shared runtime between Yuantus and Metasheet
- do add explicit overlay files that map the sales SKU matrix to concrete boot commands

## Design

### Base

`docker-compose.profile-base.yml` is a thin overlay over the existing Yuantus
stack.

It does not fork the runtime. It only pins packaging metadata:

- `YUANTUS_DELIVERY_PROFILE=base`
- `YUANTUS_ENABLE_METASHEET=false`

Command:

```bash
docker compose -f docker-compose.yml -f docker-compose.profile-base.yml up -d --build
```

### Collab

`docker-compose.profile-collab.yml` keeps the same Yuantus stack shape but
turns on the real MetaSheet collaboration bridge flag:

- `YUANTUS_DELIVERY_PROFILE=collab`
- `YUANTUS_ENABLE_METASHEET=true`

Command:

```bash
docker compose -f docker-compose.yml -f docker-compose.profile-collab.yml up -d --build
```

This is intentionally a bounded packaging overlay, not a second application
implementation.

### Combined

`docker-compose.profile-combined.yml` keeps Yuantus authoritative and adds a
Metasheet sidecar runtime:

- `metasheet-postgres`
- `metasheet-redis`
- `backend`
- `web`

The combined profile wires Metasheet to Yuantus through environment variables,
not database coupling:

- `PLM_BASE_URL=http://api:7910`
- `PLM_API_MODE=yuantus`
- `PRODUCT_MODE=plm-workbench`

The Metasheet build context is resolved from `METASHEET2_ROOT`, defaulting to
the sibling checkout `../metasheet2`.

Command:

```bash
METASHEET2_ROOT=../metasheet2 \
docker compose -f docker-compose.yml -f docker-compose.profile-combined.yml up -d --build
```

## Verification Assets

Files added:

- `docker-compose.profile-base.yml`
- `docker-compose.profile-collab.yml`
- `docker-compose.profile-combined.yml`
- `Makefile`
- `scripts/verify_compose_sku_profiles.sh`
- `scripts/verify_compose_sku_profiles_smoke.sh`
- `src/yuantus/meta_engine/tests/test_ci_contracts_compose_sku_profiles.py`

CI protection added:

- compose profile contract test added to `.github/workflows/ci.yml`
- shell syntax gate updated to include `verify_compose_sku_profiles.sh`
- shell syntax gate updated to include `verify_compose_sku_profiles_smoke.sh`

## Smoke Commands

Make targets:

```bash
make smoke-test-base
make smoke-test-collab
make smoke-test-combined
```

Direct script form:

```bash
bash scripts/verify_compose_sku_profiles_smoke.sh base
bash scripts/verify_compose_sku_profiles_smoke.sh collab
METASHEET2_ROOT=/path/to/metasheet2 \
  bash scripts/verify_compose_sku_profiles_smoke.sh combined
```

The smoke script:

- renders the compose profile first
- starts the stack under an isolated compose project name
- waits for `http://127.0.0.1:7910/api/v1/health`
- for `combined`, also waits for `http://127.0.0.1:7778/health` and `http://127.0.0.1:8899/`
- tears the stack down unless `KEEP_UP=1`

Implementation detail locked by tests:

- `verify_compose_sku_profiles.sh --render base|collab` must not require `METASHEET2_ROOT`
- `verify_compose_sku_profiles.sh --render base|collab` must not export an unset `METASHEET2_ROOT` under `set -u`
- `verify_compose_sku_profiles_smoke.sh PROFILE` must preflight only the requested profile, not all three overlays

## Local Verification

Commands executed:

```bash
pytest -q \
  src/yuantus/meta_engine/tests/test_ci_contracts_compose_sku_profiles.py \
  src/yuantus/meta_engine/tests/test_ci_contracts_ci_yml_test_list_order.py \
  src/yuantus/meta_engine/tests/test_ci_contracts_job_wiring.py \
  src/yuantus/meta_engine/tests/test_ci_shell_scripts_syntax.py

METASHEET2_ROOT=/path/to/metasheet2 \
  bash scripts/verify_compose_sku_profiles.sh

make -n smoke-test-base
make -n smoke-test-collab
make -n smoke-test-combined

curl -I --max-time 15 https://registry-1.docker.io/v2/
```

Observed outcome:

- `11 passed` for:
  - `test_ci_contracts_compose_sku_profiles.py`
  - `test_ci_contracts_ci_yml_test_list_order.py`
  - `test_ci_contracts_job_wiring.py`
  - `test_ci_shell_scripts_syntax.py`
- `4 passed` for:
  - `test_dev_and_verification_doc_index_completeness.py`
  - `test_dev_and_verification_doc_index_sorting_contracts.py`
  - `test_delivery_doc_index_all_sections_sorting_contracts.py`
  - `test_delivery_doc_index_references.py`
- `git diff --check` passed
- `bash -n scripts/verify_compose_sku_profiles.sh` passed
- `bash -n scripts/verify_compose_sku_profiles_smoke.sh` passed
- `verify_compose_sku_profiles.sh` rendered `base`, `collab`, and `combined` successfully
- all three `make -n smoke-test-*` targets resolved to the expected smoke commands

## Runtime Smoke Findings

Runtime smoke was attempted after Docker Desktop came up, but only partially
completed because the host could not pull required base images from Docker Hub.

Issues found and fixed before the external blocker:

1. `verify_compose_sku_profiles_smoke.sh base` incorrectly required
   `METASHEET2_ROOT` even though `base` and `collab` do not use Metasheet.
   Fix: gate `METASHEET2_ROOT` resolution behind `PROFILE=combined`.
2. `verify_compose_sku_profiles.sh --render base|collab` still failed under
   `set -u` because `render_profile()` always injected `METASHEET2_ROOT` into
   `docker compose config`.
   Fix: inject `METASHEET2_ROOT` only when it is defined.
3. `verify_compose_sku_profiles_smoke.sh` originally preflighted all overlays by
   calling `verify_compose_sku_profiles.sh` without `--render`.
   Fix: preflight only the requested profile with
   `verify_compose_sku_profiles.sh --render "${PROFILE}"`.

External blocker after the script fixes:

- `make smoke-test-base` advanced to real image pulls, then failed on:
  - `postgres:16-alpine`
  - `redis:7-alpine`
  - `minio/minio:latest`
- repeated `docker pull` retries failed with Docker Hub manifest `HEAD` request
  `EOF`
- host-level `curl -I --max-time 15 https://registry-1.docker.io/v2/` also
  failed with `LibreSSL SSL_connect: SSL_ERROR_SYSCALL`

Conclusion:

- the smoke harness itself is now structurally correct and CI-protected
- static verification is fully green
- full runtime smoke still depends on restoring outbound access to
  `registry-1.docker.io`

## Why This Shape

This closes the original execution gap without falling into the shared-kernel
trap:

- product packaging is now concrete
- runtime ownership stays separate
- upgrading `base` or `collab` to `combined` remains an overlay change, not a
  PLM data migration
