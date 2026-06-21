# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

YuantusPLM (元图PLM) — the **core PLM service**: a Python/FastAPI **modular monolith** for mechanical-industry product lifecycle management (Parts/BOM/ECO/versions/baselines/approvals/e-sign/CAD pipeline). It exposes ~620 REST endpoints and integrates with external services via contracts: `Athena` (ECM/DMS), `cad-ml-platform` (CAD ML), `dedupcad-vision` (drawing dedup).

- Python package: `yuantus` (`src/yuantus/`). CLI: `yuantus …` (legacy alias `plm …`).
- Runtime: Python 3.10+ (3.11 recommended; 3.14 also works — see Environment notes).

## Common commands

Setup (editable install pulls all runtime deps; `pytest`/`pytest-asyncio` are dev-only and must be added separately):
```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .\.venv\Scripts\Activate.ps1
pip install -e .
pip install pytest pytest-asyncio                 # for the test suite
```

Run the API (uvicorn, binds `settings.HOST`/`PORT` = `0.0.0.0:7910`):
```bash
yuantus start --reload            # or: yuantus start --host 127.0.0.1 --port 7910
```
Health: `GET http://localhost:7910/api/v1/health`. Interactive docs: `/docs`.

Tests — **two-tier** via `conftest.py`:
```bash
pytest                            # default: ~1.4k tests; DB-dependent tests are SKIPPED at collection
YUANTUS_PYTEST_DB=1 pytest        # full suite (~4.8k); uses TEST_DATABASE_URL=sqlite:///:memory:
pytest path/to/test_x.py::test_y  # single test (add YUANTUS_PYTEST_DB=1 if it needs a DB)
```
`pytest_ignore_collect` in `conftest.py` hides DB-dependent tests unless `YUANTUS_PYTEST_DB` is truthy; a hardcoded allowlist of no-DB test files is collected regardless. Many tests shell out to `bash` to lint `scripts/*.sh` / CI workflows — these fail anywhere `bash` isn't on PATH (e.g. Windows) and are not product failures. There is **no configured linter/formatter** (no ruff/black/flake8/mypy config); inline `# noqa` hints suggest flake8/ruff conventions only.

Seed dev data (order matters — `seed-meta` registers ItemTypes that `seed-data` needs):
```bash
yuantus seed-identity --tenant tenant-1 --org org-1 --username admin --password admin --user-id 1 --roles admin
yuantus seed-meta                 # creates ItemTypes: Part, Part BOM, Document
yuantus seed-data --part-count 30 --doc-count 10 --bom-roots 3 --bom-depth 2
```

Migrations / storage / workers:
```bash
yuantus db upgrade                # Alembic; required only when SCHEMA_MODE=migrations
yuantus init-storage
yuantus worker                    # background job worker
```
Docker (full stack incl. Postgres+MinIO): `docker compose up --build`. Compose default exposes Postgres at `localhost:55432`, MinIO at `localhost:59000/59001`.

## Architecture (big picture)

**App factory + middleware stack.** `yuantus.api.app:create_app()` builds the FastAPI app and `app` is the module-level instance uvicorn serves. On startup (lifespan → `_run_startup`) it calls `init_db(create_tables=True)` and `init_identity_db(...)`. Middleware order (added in `create_app`): CORS → Audit → **TenantOrgContext** → **AuthEnforcement** → RequestLogging. The ~620 endpoints come from **~142 explicit `include_router(...)` calls** in `app.py` (no auto-discovery) — routers live in `src/yuantus/meta_engine/web/*_router.py` (141 files) and `src/yuantus/api/routers/`. Adding a router means wiring it in `app.py`.

**Meta Engine is the core domain model** (`src/yuantus/meta_engine/`). It's a generic, metadata-driven item system rather than one table per business object:
- `meta_item_types` define types (Part, Document, Part BOM, …); `meta_items` are instances; properties are JSON on the item.
- **Relationships (incl. BOM edges) are themselves items** — e.g. a "Part BOM" item with `source_id`/`related_id`. `meta_relationships` is largely unused; do not expect BOM links there.
- Business domains are subpackages: `manufacturing`, `approvals`, `esign`, `version`, `lifecycle`, `reports`, `quality`, `subcontracting`, `box`, `cutted_parts`, `document_sync`, `ecm_publication`/`erp_publication`, `dedup`, etc. Each typically has models + services + `web/*_router.py`.
- `meta_engine/bootstrap.py:import_all_models()` imports **all** ORM models so SQLAlchemy can resolve FKs/mappers. Any standalone process (CLI command, script) that touches the DB without booting the app must call this first, or FK targets like `meta_item_types` won't be registered.

**Persistence & multitenancy** (`src/yuantus/database.py`, `config/settings.py`). SQLAlchemy 2.x. `DATABASE_URL` defaults to `sqlite:///yuantus_dev.db`; Postgres via `psycopg`. `TENANCY_MODE` ∈ `single` (default) | `db-per-tenant` | `db-per-tenant-org` | `schema-per-tenant` (**Postgres-only**). `SCHEMA_MODE` ∈ `create_all` (default; auto-creates tables on startup) | `migrations` (refuses auto-create, requires `yuantus db upgrade`). There are three Alembic trees: `migrations/` (default), `migrations_identity/`, `migrations_tenant/` with matching `alembic*.ini`. Tenant/org flow through `x-tenant-id` / `x-org-id` headers and `context.py` context vars.

**Auth.** JWT (HS256). `AuthEnforcementMiddleware` enforces globally when `AUTH_MODE=required` (**the default** — see settings.py); public paths are `GET /api/v1/health`, `POST /api/v1/auth/login`, and docs. `dependency_overrides` in tests do NOT bypass this middleware, so router tests that mock `get_current_user` need `YUANTUS_AUTH_MODE=optional` to reach the route. Multi-org: login → `/auth/orgs` → `/auth/switch-org` yields an org-scoped token.

**Storage.** `STORAGE_TYPE=local` (default, files under `./data/storage`) or `s3` (MinIO/S3). Providers in `meta_engine/storage/` (`local_storage.py`, `s3_storage.py`) behind `storage_interface.py`.

**Config.** `pydantic-settings` `Settings` (`config/settings.py`) with `env_prefix="YUANTUS_"` and `env_file=".env"`. So a setting `FOO` is overridden by env var `YUANTUS_FOO`. `get_settings()` reads the environment live.

## Conventions & gotchas

- **Settings env prefix is `YUANTUS_`** — every override is `YUANTUS_<FIELD>` (e.g. `YUANTUS_AUTH_MODE`, `YUANTUS_DATABASE_URL`).
- **Dependency pinning:** `requirements.lock` pins exact versions; `pyproject.toml` uses `>=`. Installing `-e .` resolves to the latest, which can drift from the lock (e.g. a newer Starlette where `app.routes` entries are `_IncludedRouter` without `.path`, breaking `test_*_routes_registered_in_create_app`). Prefer the lock for reproducibility when the interpreter has matching wheels.
- **PowerShell + FastAPI redirects:** calling an endpoint without its trailing slash triggers a 307; PowerShell's `Invoke-RestMethod`/`Invoke-WebRequest` drop the `Authorization` header on redirect → misleading `401 "Missing bearer token"`. Use exact paths (e.g. `GET /api/v1/search/`).
- Extensive operational docs live in `docs/` (runbooks, multi-tenancy ops, P2 observation regression, release orchestration). The shared-dev bootstrap and "P2 observation" fixtures are a recurring workflow there.

## Environment notes (this machine)

Windows + Python 3.14 only, no Docker. Docker-free dev works on the SQLite/local-storage defaults. Use `.\.venv\Scripts\python.exe` / `.\.venv\Scripts\yuantus.exe` directly (no Unix `activate`). All native deps (psycopg, cryptography, pydantic-core, Pillow) ship cp314 wheels, so `pip install -e .` succeeds; if a future dep lacks one, a Python 3.12 venv is the clean fallback rather than building from source.
