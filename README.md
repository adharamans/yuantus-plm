# YuantusPLM (元图PLM)

[![Release](https://img.shields.io/github/v/release/adharamans/yuantus-plm?display_name=tag)](https://github.com/adharamans/yuantus-plm/releases)
[![License](https://img.shields.io/github/license/adharamans/yuantus-plm)](LICENSE)

Repository name: `yuantus-plm`  
Product name: **YuantusPLM / 元图PLM**

This repo contains the **core PLM service** (modular monolith) and contracts to integrate with:
- `Athena` (ECM/DMS)
- `cad-ml-platform` (CAD ML analysis)
- `dedupcad-vision` (drawing deduplication)

## Quick start (dev)

1) Start dependencies (infra only):
```bash
docker compose up -d postgres minio
```

2) Run API:
```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -e .
yuantus start --reload
```

API: `http://localhost:7910/api/v1/health`

Notes:
- Postgres (docker) exposed at `localhost:55432`
- MinIO (docker) exposed at `localhost:59000` (S3 API) / `localhost:59001` (Console)

## Quick start (docker)

Run API + Worker + Postgres + MinIO via docker compose:

```bash
docker compose up --build
```

## Shared-dev bootstrap (docker)

For a fresh shared-dev deployment, initialize tenant/org/users and the P2 observation fixtures once before running the regression scripts.

Use these as the canonical first-run entrypoints:

- `docs/P2_SHARED_DEV_FIRST_RUN_CHECKLIST.md`
- `bash scripts/print_p2_shared_dev_first_run_commands.sh`

Fresh shared-dev first-run is pinned to the tracked base compose file:

- `docker compose -f docker-compose.yml --env-file ./deployments/docker/shared-dev.bootstrap.env --profile bootstrap run --rm bootstrap`
- `docker compose -f docker-compose.yml up -d api worker`

Do not implicitly rely on any machine-local `docker-compose.override.yml` when initializing a fresh shared-dev.

The default dataset mode is now `p2-observation`; it seeds the minimal ECO set that should yield:
- baseline: `pending=1 / overdue=2 / escalated=0`
- after one `escalate-overdue`: `pending=1 / overdue=3 / escalated=1`
- write tri-state on `eco-specialist`: `401 / 403 / 200`

The bootstrap fixture step also materializes the local `RBACUser` records for both:
- `admin`
- `ops-viewer`

so a fresh shared-dev database does not need a separate local-dev-only seeding step.

Note:
- The bootstrap service now seeds the tracked P2 observation fixtures and writes a manifest path such as `./tmp/p2_observation_fixture_manifest.json`.
- The bootstrap output also prints the non-superuser `ops-viewer` credentials for the `403` branch of the observation smoke.
- Set `YUANTUS_BOOTSTRAP_DATASET_MODE=generic` if you only want generic Part/Document/BOM demo data.
- Set `YUANTUS_BOOTSTRAP_DATASET_MODE=none` if you only want identity/bootstrap users.

For post-bootstrap reruns with existing shared-dev credentials, use:

- `docs/P2_ONE_PAGE_DEV_GUIDE.md`
- `docs/P2_SHARED_DEV_OBSERVATION_HANDOFF.md`
- `docs/P2_SHARED_DEV_142_READONLY_REFREEZE_CANDIDATE_CHECKLIST.md`
- `docs/P2_SHARED_DEV_142_READONLY_REFREEZE_PROPOSAL_CHECKLIST.md`
- `docs/P2_SHARED_DEV_142_READONLY_REFREEZE_READINESS_CHECKLIST.md`
- `bash scripts/print_p2_shared_dev_observation_commands.sh`

For the current official readonly baseline on shared-dev host `142.171.239.56`, start with:

- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --help`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode print-daily-commands`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode print-readonly-commands`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode readonly-rerun`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode print-refreeze-candidate-commands`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode print-refreeze-proposal-commands`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode print-refreeze-readiness-commands`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode refreeze-candidate`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode refreeze-proposal`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode refreeze-readiness`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode drift-audit`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode drift-investigation`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode workflow-probe`
- `bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode workflow-readonly-check`

## CAD ML Docker helpers

Use the one-click scripts to start/stop cad-ml-platform (default ports 18000/19090/16379):

```bash
CAD_ML_API_PORT=18000 CAD_ML_API_METRICS_PORT=19090 CAD_ML_REDIS_PORT=16379 \
  scripts/run_cad_ml_docker.sh

scripts/check_cad_ml_docker.sh

scripts/stop_cad_ml_docker.sh
```

## Auth (dev)

Default is `YUANTUS_AUTH_MODE=required`: every route except `GET /api/v1/health`, `POST /api/v1/auth/login`, and the docs requires a JWT (enforced globally by `AuthEnforcementMiddleware`).

Seed identity (tenant/org/user) and login:
```bash
yuantus seed-identity --tenant tenant-1 --org org-1 --username admin --password admin --user-id 1 --roles admin
curl -s -X POST http://127.0.0.1:7910/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"tenant-1","username":"admin","password":"admin","org_id":"org-1"}'
```

Multi-org flow (login without org → list orgs → switch org token):
```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:7910/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"tenant-1","username":"admin","password":"admin"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s http://127.0.0.1:7910/api/v1/auth/orgs -H "Authorization: Bearer $TOKEN"
ORG_TOKEN=$(curl -s -X POST http://127.0.0.1:7910/api/v1/auth/switch-org \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"org_id":"org-1"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
```

For casual local development you can relax enforcement so unauthenticated
requests pass through (a bearer token is still honored when present):
```bash
export YUANTUS_AUTH_MODE=optional   # or: disabled
```

## Verification

See `docs/VERIFICATION.md`.

Quick CAD-ML regression:
```bash
RUN_CAD_ML_DOCKER=1 RUN_CAD_ML_METRICS=1 \
  scripts/verify_cad_ml_quick.sh http://127.0.0.1:7910 tenant-1 org-1
```

CAD Material Sync AutoCAD client:
```bash
python3 scripts/verify_cad_material_delivery_package.py
```

AutoCAD client source and Windows 2018 validation guide:
`clients/autocad-material-sync/`

Native PLM workspace browser regressions:
```bash
npm run playwright:test:plm-workspace
npm run playwright:test:plm-workspace:eco-actions
bash scripts/verify_playwright_plm_workspace_all.sh http://127.0.0.1:7910
```

Workspace-specific coverage and operator wrappers:
`playwright/tests/README_plm_workspace.md`

Claude Code sidecar/worktree templates (read-only by default):
`bash scripts/print_claude_code_parallel_commands.sh`

Read-only Claude reviewer sidecar (`claude -p --no-session-persistence --tools ""`):
`bash scripts/run_claude_code_parallel_reviewer.sh`

Claude Code output is advisory. Write-mode worktree usage requires explicit
user authorization and must not commit `.claude/` or `local-dev-env/`.

## Runbooks

- `docs/ERROR_CODES_JOBS.md` (Jobs error codes)
- `docs/OPS_RUNBOOK_MT.md` (Multi-tenancy ops)
- `docs/P2_OBSERVATION_REGRESSION_ONE_COMMAND.md` (P2 observation one-command rerun)
- `docs/P2_OBSERVATION_REGRESSION_WORKFLOW_DISPATCH.md` (P2 observation workflow dispatch)
- `docs/P2_ONE_PAGE_DEV_GUIDE.md` (P2 observation single-page execution guide)
- `docs/P2_REMOTE_OBSERVATION_REGRESSION_RUNBOOK.md` (P2 remote/shared-dev observation rerun)
- `docs/P2_SHARED_DEV_142_DAILY_OPS_CHECKLIST.md` (142 shared-dev 维护态日常操作清单)
- `docs/P2_SHARED_DEV_142_DRIFT_AUDIT_CHECKLIST.md` (142 shared-dev drift audit checklist)
- `docs/P2_SHARED_DEV_142_DRIFT_INVESTIGATION_CHECKLIST.md` (142 shared-dev drift investigation evidence pack checklist)
- `docs/P2_SHARED_DEV_142_READONLY_REFREEZE_CANDIDATE_CHECKLIST.md` (142 shared-dev stable readonly candidate preview checklist)
- `docs/P2_SHARED_DEV_142_READONLY_REFREEZE_PROPOSAL_CHECKLIST.md` (142 shared-dev formal readonly refreeze proposal checklist)
- `docs/P2_SHARED_DEV_142_RERUN_CHECKLIST.md` (142 shared-dev 常规 rerun checklist)
- `docs/P2_SHARED_DEV_FIRST_RUN_CHECKLIST.md` (P2 shared-dev first-run checklist)
- `docs/P2_SHARED_DEV_OBSERVATION_HANDOFF.md` (P2 shared-dev observation handoff)
- `docs/RUNBOOK_BACKUP_RESTORE.md` (Backup/restore)
- `docs/RUNBOOK_CAD_LEGACY_CONVERSION_QUEUE_AUDIT.md` (Legacy CAD conversion queue audit)
- `docs/RUNBOOK_CI_CHANGE_SCOPE.md` (CI/regression skip rules + force full runs)
- `docs/RUNBOOK_CLAUDE_CODE_PARALLEL_WORKTREE.md` (Claude Code sidecar/worktree usage)
- `docs/RUNBOOK_JOBS_DIAG.md` (Jobs/CAD failure diagnostics)
- `docs/RUNBOOK_MAINLINE_BASELINE_SWITCH_20260414.md` (Mainline baseline switch)
- `docs/RUNBOOK_P1_CAD_COMMIT_SEQUENCE_20260414.md` (P1 CAD commit sequence)
- `docs/RUNBOOK_PARALLEL_BRANCH_OBSERVABILITY_20260228.md` (Parallel branch observability and incident handling)
- `docs/RUNBOOK_PERF_GATE_CONFIG.md` (Perf CI gate config)
- `docs/RUNBOOK_RELATIONSHIP_ITEM_MIGRATION.md` (Relationship item migration)
- `docs/RUNBOOK_RELEASE_ORCHESTRATION.md` (Release orchestration)
- `docs/RUNBOOK_RUNTIME.md` (Runtime operations)
- `docs/RUNBOOK_SCHEDULED_BACKUP.md` (Scheduled backups)
- `docs/RUNBOOK_STRICT_GATE.md` (Strict gate evidence run)
- `docs/RUNBOOK_TENANT_MIGRATIONS_20260427.md` (Tenant schema provisioning and migration wiring)

## Development design

See `docs/DEVELOPMENT_DESIGN.md`.

## Development plan

See `docs/DEVELOPMENT_PLAN.md`.

## Private delivery acceptance

See `docs/PRIVATE_DELIVERY_ACCEPTANCE.md`.

## Reuse guide

See `docs/REUSE.md` (how to reuse `/Users/huazhou/Downloads/Github/PLM/src`).

## Contributing

See `CONTRIBUTING.md`.

## Security

See `SECURITY.md`.

## Conventions

- **Python package**: `yuantus` (`src/yuantus/`)
- **CLI**: `yuantus ...` (legacy alias: `plm ...`)
- **Tenant header**: `x-tenant-id`
- **Organization header**: `x-org-id`

## Runtime

- **Python**: 3.10+ (recommended 3.11)
