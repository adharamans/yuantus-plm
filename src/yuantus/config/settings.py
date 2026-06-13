from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YUANTUS_",
        env_file=".env",
        extra="ignore",
    )

    # Runtime
    ENVIRONMENT: str = Field(default="dev", description="Environment name")
    HOST: str = Field(default="0.0.0.0", description="Bind host")
    PORT: int = Field(default=7910, description="Bind port")

    # Tenancy
    # single: one database for all tenants (default)
    # db-per-tenant: one database/schema per tenant (dev-friendly for SaaS)
    # db-per-tenant-org: one database per (tenant, org) for strong isolation
    TENANCY_MODE: str = Field(
        default="single",
        description="single|db-per-tenant|db-per-tenant-org|schema-per-tenant (Postgres only, default off)",
    )
    DATABASE_URL_TEMPLATE: str = Field(
        default="",
        description="Optional template for db-per-tenant/org, e.g. sqlite:///yuantus_dev__{tenant_id}__{org_id}.db",
    )

    # Multi-tenant / multi-org headers
    TENANT_HEADER: str = Field(default="x-tenant-id", description="Tenant header name")
    ORG_HEADER: str = Field(default="x-org-id", description="Organization header name")
    PLATFORM_ADMIN_ENABLED: bool = Field(
        default=False,
        description="Enable platform admin capabilities for cross-tenant provisioning",
    )
    PLATFORM_TENANT_ID: str = Field(
        default="platform",
        description="Tenant id used for platform admin operations",
    )
    RELATIONSHIP_SIMULATE_ENABLED: bool = Field(
        default=False,
        description="Allow debug endpoint to simulate deprecated relationship writes",
    )
    RELATIONSHIP_TYPE_LEGACY_SEED_ENABLED: bool = Field(
        default=False,
        description="Seed legacy meta_relationship_types rows for compatibility",
    )
    TEST_FAILPOINTS_ENABLED: bool = Field(
        default=False,
        description="Enable test-only failpoints (used by Playwright/E2E to inject controlled failures)",
    )
    BREAKAGE_DESIGN_LOOPBACK_EVENTS_ENABLED: bool = Field(
        default=False,
        description=(
            "Tier-B #3 §3.6: emit a breakage.design_loopback_eco domain "
            "event when a design-loopback ECO result converges "
            "(CAS-winner create or durable-dedupe reuse). Default OFF — "
            "byte-identical pre-§3.6 behavior; flipping is a separate opt-in."
        ),
    )
    QUOTA_MODE: str = Field(
        default="disabled",
        description="disabled|soft|enforce quota checks for tenant limits",
    )

    # External services
    ATHENA_BASE_URL: str = Field(
        default="http://localhost:7700/api/v1", description="Athena ECM base URL"
    )
    ATHENA_SERVICE_TOKEN: str = Field(
        default="",
        description="Optional service token (JWT) for Athena ECM integrations",
    )
    ATHENA_TOKEN_URL: str = Field(
        default="",
        description="Optional Athena OAuth token URL for client credentials",
    )
    ATHENA_CLIENT_ID: str = Field(
        default="",
        description="Optional Athena OAuth client id for service account",
    )
    ATHENA_CLIENT_SECRET: str = Field(
        default="",
        description="Optional Athena OAuth client secret for service account",
    )
    ATHENA_CLIENT_SECRET_FILE: str = Field(
        default="",
        description="Optional file path for Athena client secret (preferred)",
    )
    ATHENA_CLIENT_SCOPE: str = Field(
        default="",
        description="Optional OAuth scope for Athena client credentials",
    )
    CAD_ML_BASE_URL: str = Field(
        default="http://localhost:8001", description="CAD ML Platform base URL"
    )
    DEDUP_VISION_BASE_URL: str = Field(
        default="http://localhost:8100", description="DedupCAD Vision base URL"
    )
    DEDUP_VISION_SERVICE_TOKEN: str = Field(
        default="",
        description="Optional service token (JWT) for DedupCAD Vision integrations",
    )
    # Phase 6 P6.1 — DedupCAD Vision circuit breaker (default off, status quo).
    # When enabled, consecutive request failures within the rolling window cause
    # the breaker to open and short-circuit subsequent calls until recovery_seconds
    # elapses, then a half-open trial decides closed/open.
    CIRCUIT_BREAKER_DEDUP_VISION_ENABLED: bool = Field(
        default=False,
        description="Enable circuit breaker for DedupCAD Vision client (default off)",
    )
    CIRCUIT_BREAKER_DEDUP_VISION_FAILURE_THRESHOLD: int = Field(
        default=5,
        description="DedupCAD Vision: failures within window to open circuit",
    )
    CIRCUIT_BREAKER_DEDUP_VISION_WINDOW_SECONDS: int = Field(
        default=60,
        description="DedupCAD Vision: rolling failure window in seconds",
    )
    CIRCUIT_BREAKER_DEDUP_VISION_RECOVERY_SECONDS: int = Field(
        default=30,
        description="DedupCAD Vision: base seconds before open->half-open trial",
    )
    CIRCUIT_BREAKER_DEDUP_VISION_HALF_OPEN_MAX_CALLS: int = Field(
        default=1,
        description="DedupCAD Vision: max trial calls allowed in half-open state",
    )
    CIRCUIT_BREAKER_DEDUP_VISION_BACKOFF_MAX_SECONDS: int = Field(
        default=600,
        description="DedupCAD Vision: maximum exponential backoff cap in seconds",
    )
    # Phase 6 P6.2 — CAD ML Platform circuit breaker (default off, status quo).
    # Mirrors P6.1 thresholds; toggled via YUANTUS_CIRCUIT_BREAKER_CAD_ML_*.
    CIRCUIT_BREAKER_CAD_ML_ENABLED: bool = Field(
        default=False,
        description="Enable circuit breaker for CAD ML Platform client (default off)",
    )
    CIRCUIT_BREAKER_CAD_ML_FAILURE_THRESHOLD: int = Field(
        default=5,
        description="CAD ML: failures within window to open circuit",
    )
    CIRCUIT_BREAKER_CAD_ML_WINDOW_SECONDS: int = Field(
        default=60,
        description="CAD ML: rolling failure window in seconds",
    )
    CIRCUIT_BREAKER_CAD_ML_RECOVERY_SECONDS: int = Field(
        default=30,
        description="CAD ML: base seconds before open->half-open trial",
    )
    CIRCUIT_BREAKER_CAD_ML_HALF_OPEN_MAX_CALLS: int = Field(
        default=1,
        description="CAD ML: max trial calls allowed in half-open state",
    )
    CIRCUIT_BREAKER_CAD_ML_BACKOFF_MAX_SECONDS: int = Field(
        default=600,
        description="CAD ML: maximum exponential backoff cap in seconds",
    )
    # Phase 6 P6.3 — Athena ECM circuit breaker (default off, status quo).
    # Mirrors P6.1/P6.2 thresholds; toggled via YUANTUS_CIRCUIT_BREAKER_ATHENA_*.
    CIRCUIT_BREAKER_ATHENA_ENABLED: bool = Field(
        default=False,
        description="Enable circuit breaker for Athena ECM client (default off)",
    )
    CIRCUIT_BREAKER_ATHENA_FAILURE_THRESHOLD: int = Field(
        default=5,
        description="Athena: failures within window to open circuit",
    )
    CIRCUIT_BREAKER_ATHENA_WINDOW_SECONDS: int = Field(
        default=60,
        description="Athena: rolling failure window in seconds",
    )
    CIRCUIT_BREAKER_ATHENA_RECOVERY_SECONDS: int = Field(
        default=30,
        description="Athena: base seconds before open->half-open trial",
    )
    CIRCUIT_BREAKER_ATHENA_HALF_OPEN_MAX_CALLS: int = Field(
        default=1,
        description="Athena: max trial calls allowed in half-open state",
    )
    CIRCUIT_BREAKER_ATHENA_BACKOFF_MAX_SECONDS: int = Field(
        default=600,
        description="Athena: maximum exponential backoff cap in seconds",
    )
    CAD_ML_SERVICE_TOKEN: str = Field(
        default="",
        description="Optional service token (JWT) for CAD ML Platform integrations",
    )
    # VemCAD render service (high-fidelity DXF → PNG/SVG). Empty = disabled;
    # cad_preview then keeps its existing CAD-ML / connector path.
    RENDER_SERVICE_BASE_URL: str = Field(
        default="",
        description="VemCAD render service base URL (e.g. http://render:8077); empty disables it",
    )
    RENDER_SERVICE_SERVICE_TOKEN: str = Field(
        default="",
        description="Optional Bearer token for the render service (Phase 1 service is internal/no-auth)",
    )
    RENDER_SERVICE_TIMEOUT_SECONDS: int = Field(
        default=30, description="Render service request timeout (seconds)"
    )
    # Circuit breaker (default off, status quo) — mirrors the CAD-ML P6 policy.
    CIRCUIT_BREAKER_RENDER_SERVICE_ENABLED: bool = Field(
        default=False,
        description="Enable circuit breaker for the render service client (default off)",
    )
    CIRCUIT_BREAKER_RENDER_SERVICE_FAILURE_THRESHOLD: int = Field(default=5)
    CIRCUIT_BREAKER_RENDER_SERVICE_WINDOW_SECONDS: int = Field(default=60)
    CIRCUIT_BREAKER_RENDER_SERVICE_RECOVERY_SECONDS: int = Field(default=30)
    CIRCUIT_BREAKER_RENDER_SERVICE_HALF_OPEN_MAX_CALLS: int = Field(default=1)
    CIRCUIT_BREAKER_RENDER_SERVICE_BACKOFF_MAX_SECONDS: int = Field(default=600)
    CAD_EXTRACTOR_BASE_URL: str = Field(
        default="",
        description="Optional CAD extractor service base URL",
    )
    CAD_EXTRACTOR_SERVICE_TOKEN: str = Field(
        default="",
        description="Optional service token (JWT) for CAD extractor service",
    )
    CAD_EXTRACTOR_TIMEOUT_SECONDS: int = Field(
        default=30,
        description="CAD extractor service timeout in seconds",
    )
    CAD_EXTRACTOR_MODE: str = Field(
        default="optional",
        description="optional|required (fail cad_extract when external service fails)",
    )
    CAD_CONNECTOR_BASE_URL: str = Field(
        default="",
        description="Optional CAD connector service base URL",
    )
    CAD_CONNECTOR_SERVICE_TOKEN: str = Field(
        default="",
        description="Optional service token (JWT) for CAD connector service",
    )
    CAD_CONNECTOR_TIMEOUT_SECONDS: int = Field(
        default=60,
        description="CAD connector service timeout in seconds",
    )
    CAD_CONNECTOR_MODE: str = Field(
        default="optional",
        description="disabled|optional|required (fail when CAD connector fails)",
    )
    CAD_CONVERSION_BACKEND_PROFILE: str = Field(
        default="auto",
        description=(
            "auto|local-baseline|hybrid-auto|external-enterprise; "
            "auto preserves legacy CAD_CONNECTOR_MODE behavior"
        ),
    )
    CAD_STEP_IGES_BACKEND: str = Field(
        default="auto",
        description=(
            "auto|local|connector for STEP/IGES preview/geometry under hybrid-auto; "
            "local-baseline and external-enterprise profiles keep their strict defaults"
        ),
    )
    CAD_CONNECTORS_CONFIG_PATH: str = Field(
        default="",
        description="Optional JSON config path for custom CAD connectors",
    )
    CAD_CONNECTORS_ALLOW_PATH_OVERRIDE: bool = Field(
        default=False,
        description="Allow reload endpoint to accept config_path override (admin only)",
    )
    CADGF_ROOT: str = Field(
        default="",
        description="Path to CADGameFusion repo root (for 2D CAD conversion)",
    )
    CADGF_CONVERT_SCRIPT: str = Field(
        default="",
        description="Path to CADGameFusion tools/plm_convert.py",
    )
    CADGF_CONVERT_CLI: str = Field(
        default="",
        description="Path to CADGameFusion convert_cli binary",
    )
    CADGF_DXF_PLUGIN_PATH: str = Field(
        default="",
        description="Path to CADGameFusion DXF importer plugin",
    )
    CADGF_PYTHON_BIN: str = Field(
        default="",
        description="Override python executable for CADGF conversion",
    )
    CADGF_ROUTER_BASE_URL: str = Field(
        default="http://127.0.0.1:9000",
        description="CADGameFusion router service base URL",
    )
    CADGF_ROUTER_PUBLIC_BASE_URL: str = Field(
        default="",
        description="Public CADGameFusion router base URL for viewer links",
    )
    CADGF_ROUTER_AUTH_TOKEN: str = Field(
        default="",
        description="Bearer token for CADGameFusion router service",
    )
    CADGF_DEFAULT_EMIT: str = Field(
        default="json,gltf,meta",
        description="Default emit mode for CAD preview bridge",
    )
    CADGF_ROUTER_TIMEOUT_SECONDS: int = Field(
        default=60,
        description="Timeout for CADGameFusion router requests",
    )
    DWG_CONVERTER_BIN: str = Field(
        default="",
        description="Optional DWG->DXF converter binary (e.g. ODAFileConverter).",
    )
    CAD_PREVIEW_PUBLIC: bool = Field(
        default=False,
        description="Allow unauthenticated access to CAD preview assets",
    )
    CAD_PREVIEW_CORS_ORIGINS: str = Field(
        default="",
        description="Comma-separated CORS origins for CAD preview assets",
    )

    # Database
    DATABASE_URL: str = Field(default="sqlite:///yuantus_dev.db")
    TEST_DATABASE_URL: str = Field(default="sqlite:///:memory:")
    IDENTITY_DATABASE_URL: str = Field(
        default="",
        description="Optional separate identity DB URL; default uses DATABASE_URL",
    )
    ALEMBIC_TARGET_SCHEMA: str = Field(
        default="",
        description="Optional tenant schema target for the tenant Alembic env; empty disables tenant env execution",
    )
    ALEMBIC_CREATE_SCHEMA: bool = Field(
        default=False,
        description="Allow the tenant Alembic env/provisioning helper to CREATE SCHEMA IF NOT EXISTS",
    )
    SCHEMA_MODE: str = Field(
        default="create_all",
        description="create_all: auto-create tables (dev), migrations: use Alembic only (prod)",
    )

    # Auth (built-in / dev-first)
    AUTH_MODE: str = Field(
        default="required", description="disabled|optional|required"
    )
    JWT_SECRET_KEY: str = Field(
        default="yuantus-dev-secret-change-me",
        description="HS256 secret for dev; override in production",
    )
    ESIGN_SECRET_KEY: str = Field(
        default="",
        description="Optional HMAC secret for electronic signatures (defaults to JWT_SECRET_KEY)",
    )
    ESIGN_VERIFY_SECRET_KEYS: str = Field(
        default="",
        description="Optional comma-separated HMAC secrets accepted for verifying existing e-signatures (key rotation).",
    )
    JWT_ACCESS_TOKEN_TTL_SECONDS: int = Field(
        default=3600, description="Access token TTL seconds"
    )
    AUTH_LEEWAY_SECONDS: int = Field(
        default=0, description="JWT exp leeway seconds"
    )

    # PLM-COLLAB-P3-D1: embed-token minting (Ed25519, asymmetric). The signing PRIVATE key is
    # Yuantus-only and NEVER committed; a consumer verifies offline with the matching PUBLIC
    # key. Empty signing key = minting disabled (fail-closed).
    EMBED_TOKEN_SIGNING_KEY: str = Field(
        default="",
        description="base64 raw Ed25519 PRIVATE key (32-byte seed) for signing embed tokens; empty = minting disabled (fail-closed). NEVER commit.",
    )
    EMBED_TOKEN_KEY_ID: str = Field(
        default="embed-1",
        description="kid for the embed-token signing key (rotation / consumer-side key lookup).",
    )
    EMBED_TOKEN_AUDIENCE: str = Field(
        default="metasheet2.embed",
        description="JWT `aud` (the intended recipient SERVICE) so a consumer can do standard "
        "RFC-7519 audience validation; the iframe origin is carried separately as `embed_origin`.",
    )
    EMBED_TOKEN_TTL_SECONDS: int = Field(
        default=120,
        description="Embed token TTL seconds (short-lived; the service caps it at 600).",
    )
    EMBED_ALLOWED_ORIGINS: str = Field(
        default="",
        description="Comma-separated allowlist of embed origins (matched against the token's embed_origin claim; the JWT aud is the service audience, see EMBED_TOKEN_AUDIENCE). Empty = none allowed (fail-closed). Production must NOT use '*'.",
    )

    LOG_FORMAT: str = Field(
        default="text",
        description="Request log format: 'text' (legacy) or 'json' (structured per-request log line)",
    )
    REQUEST_ID_HEADER: str = Field(
        default="x-request-id",
        description="Header that supplies an upstream request id; absent → middleware generates uuid4",
    )

    # PLM Collaboration & Automation Edition (#691 canonical / #693 Phase 0).
    # Master kill switch for the MetaSheet collaboration bridge seam
    # (PLM-COLLAB-P0-A, scope taskbook D0-2). Replaces the compose-only
    # YUANTUS_ENABLE_COLLAB, which Settings (extra="ignore") silently drops.
    ENABLE_METASHEET: bool = Field(
        default=False,
        description=(
            "MetaSheet collaboration bridge kill switch. False (default) = base "
            "PLM only: no bridge route, no bridge state, no event subscription. "
            "True = the inert bridge seam may mount; per-tenant entitlement still "
            "gates actual use (Phase 1+). env: YUANTUS_ENABLE_METASHEET."
        ),
    )

    # PLM-COLLAB-P1-C: offline-license verification public keys, kid -> base64 raw
    # Ed25519 public key. Verifies vendor-signed license files; the signing PRIVATE
    # key never lives in this repo. env: YUANTUS_LICENSE_PUBLIC_KEYS as a JSON object.
    LICENSE_PUBLIC_KEYS: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Offline-license public keys (kid -> base64 raw Ed25519 public key) for "
            "verifying vendor-signed license files. env: YUANTUS_LICENSE_PUBLIC_KEYS "
            "(JSON object). The signing private key is never stored in this repo."
        ),
    )

    # PLM-COLLAB-P1-D: enable the DEFAULT-OFF, superuser-only MOCK feature
    # activation route (demo/test of the upgrade affordance). Production stays off;
    # real authorization always goes via the P1-C signed license import.
    FEATURE_MOCK_ACTIVATION_ENABLED: bool = Field(
        default=False,
        description=(
            "Enable the superuser-only POST /features/{key}/mock-activate route "
            "(P1-D demo/test only; NEVER a production authorization path). Default "
            "off. env: YUANTUS_FEATURE_MOCK_ACTIVATION_ENABLED."
        ),
    )

    AUDIT_ENABLED: bool = Field(default=False, description="Audit log middleware")
    AUDIT_RETENTION_DAYS: int = Field(
        default=0, description="Prune audit logs older than N days (0=disabled)"
    )
    AUDIT_RETENTION_MAX_ROWS: int = Field(
        default=0, description="Keep at most N audit rows (0=disabled)"
    )
    AUDIT_RETENTION_PRUNE_INTERVAL_SECONDS: int = Field(
        default=600, description="Min seconds between audit prune runs"
    )
    HEALTHCHECK_EXTERNAL: bool = Field(
        default=False, description="Enable external dependency checks in /health/deps"
    )
    HEALTHCHECK_EXTERNAL_TIMEOUT_SECONDS: int = Field(
        default=2, description="Timeout for external dependency checks"
    )

    # Release validation (strategy-based prechecks)
    RELEASE_VALIDATION_RULESETS_JSON: str = Field(
        default="",
        description="Optional JSON mapping release validation rulesets (routing_release/mbom_release).",
    )
    LATEST_RELEASED_GUARD_DISABLED: bool = Field(
        default=False,
        description="Disable latest-released write guard globally; this is a hard disable.",
    )
    SUSPENDED_GUARD_DISABLED: bool = Field(
        default=False,
        description="Disable suspended-state write guard globally; this is a hard disable.",
    )

    # Search engine (optional)
    SEARCH_ENGINE_INDEX_PREFIX: str = Field(default="yuantus")
    SEARCH_ENGINE_URL: str = Field(default="")
    SEARCH_ENGINE_USERNAME: str = Field(default="")
    SEARCH_ENGINE_PASSWORD: str = Field(default="")

    # File storage (Meta Engine)
    STORAGE_TYPE: str = Field(default="local", description="local|s3")
    LOCAL_STORAGE_PATH: str = Field(default="./data/storage")
    LOCAL_STORAGE_PUBLIC_URL_PREFIX: str = Field(default="")
    FILE_UPLOAD_MAX_BYTES: int = Field(
        default=0,
        description="Max upload size in bytes (0 disables limit)",
    )
    FILE_ALLOWED_EXTENSIONS: str = Field(
        default="",
        description="Comma-separated allowed file extensions (no dot). Empty allows all.",
    )

    # Plugins
    PLUGIN_DIRS: str = Field(
        default="./plugins", description="Comma-separated plugin directories"
    )
    PLUGINS_AUTOLOAD: bool = Field(
        default=False,
        description="Auto-discover/load plugins on startup; keep false unless explicit allowlist or controlled startup",
    )
    PLUGINS_ENABLED: str = Field(
        default="",
        description="Comma-separated plugin ids to load; empty keeps plugins disabled unless autoload is explicitly enabled",
    )

    S3_BUCKET_NAME: str = Field(default="yuantus")
    S3_ENDPOINT_URL: str = Field(default="http://localhost:9000")
    S3_PUBLIC_ENDPOINT_URL: str = Field(
        default="",
        description="Public S3 endpoint used in presigned URLs; defaults to S3_ENDPOINT_URL",
    )
    S3_ACCESS_KEY_ID: str = Field(default="minioadmin")
    S3_SECRET_ACCESS_KEY: str = Field(default="minioadmin")
    S3_REGION_NAME: str = Field(default="us-east-1")

    # Jobs / Async
    JOB_MAX_ATTEMPTS_DEFAULT: int = Field(
        default=3, description="Default max attempts for async jobs"
    )
    JOB_RETRY_BACKOFF_SECONDS: int = Field(
        default=5, description="Retry backoff seconds"
    )
    JOB_STALE_TIMEOUT_SECONDS: int = Field(
        default=900, description="Requeue processing jobs after this timeout (seconds)"
    )
    # PLM->ERP publication outbox worker (G2 R2 worker daemon)
    PUBLICATION_OUTBOX_POLL_INTERVAL_SECONDS: int = Field(
        default=10, description="PLM->ERP publication worker poll interval (seconds)"
    )
    PUBLICATION_OUTBOX_BATCH_SIZE: int = Field(
        default=20, description="Max publication-outbox rows claimed per worker tick"
    )
    PUBLICATION_OUTBOX_RETRY_BACKOFF_SECONDS: int = Field(
        default=30,
        description="PLM->ERP publication retry backoff seconds (linear * attempt_count)",
    )
    PUBLICATION_OUTBOX_STALE_TIMEOUT_SECONDS: int = Field(
        default=900,
        description="Reclaim publication-outbox rows claimed but unprocessed beyond this (seconds)",
    )
    # PLM->ERP publication connector (G2 R3, generic outbound HTTP)
    PUBLICATION_ERP_TARGET_SYSTEM: str = Field(
        default="",
        description="target_system routed to the HTTP connector (empty = Null adapter only)",
    )
    PUBLICATION_ERP_BASE_URL: str = Field(
        default="",
        description="PLM->ERP publication HTTP endpoint base URL (empty = Null adapter only)",
    )
    PUBLICATION_ERP_PATH: str = Field(
        default="/publications", description="PLM->ERP publication POST path"
    )
    PUBLICATION_ERP_SERVICE_TOKEN: str = Field(
        default="",
        description="Bearer token for the publication HTTP endpoint (never logged)",
    )
    PUBLICATION_ERP_TIMEOUT_SECONDS: float = Field(
        default=30.0, description="PLM->ERP publication HTTP timeout (seconds)"
    )
    METRICS_ENABLED: bool = Field(
        default=True,
        description="Serve `/api/v1/metrics` (Prometheus text format). When False the endpoint returns 404; instrumentation always records in-memory.",
    )
    METRICS_BACKEND: str = Field(
        default="prometheus",
        description="Metric exposition backend. Currently only 'prometheus' is supported (in-process registry, scraped via /metrics).",
    )
    SCHEDULER_ENABLED: bool = Field(
        default=False,
        description="Enable the lightweight application scheduler loop",
    )
    SCHEDULER_POLL_INTERVAL_SECONDS: int = Field(
        default=60,
        description="Scheduler loop poll interval seconds",
    )
    SCHEDULER_SYSTEM_USER_ID: int = Field(
        default=1,
        description="User id used for scheduler-created jobs",
    )
    SCHEDULER_ECO_ESCALATION_ENABLED: bool = Field(
        default=True,
        description="Enable periodic ECO overdue approval escalation job enqueue",
    )
    SCHEDULER_ECO_ESCALATION_INTERVAL_SECONDS: int = Field(
        default=300,
        description="Minimum seconds between ECO overdue escalation scheduler jobs",
    )
    SCHEDULER_ECO_ESCALATION_PRIORITY: int = Field(
        default=80,
        description="Queue priority for ECO overdue escalation scheduler jobs",
    )
    SCHEDULER_ECO_ESCALATION_MAX_ATTEMPTS: int = Field(
        default=1,
        description="Max attempts for ECO overdue escalation scheduler jobs",
    )
    SCHEDULER_AUDIT_RETENTION_ENABLED: bool = Field(
        default=True,
        description="Enable periodic audit retention prune job enqueue",
    )
    SCHEDULER_AUDIT_RETENTION_INTERVAL_SECONDS: int = Field(
        default=3600,
        description="Minimum seconds between audit retention scheduler jobs",
    )
    SCHEDULER_AUDIT_RETENTION_PRIORITY: int = Field(
        default=95,
        description="Queue priority for audit retention scheduler jobs",
    )
    SCHEDULER_AUDIT_RETENTION_MAX_ATTEMPTS: int = Field(
        default=1,
        description="Max attempts for audit retention scheduler jobs",
    )
    SCHEDULER_BOM_TO_MBOM_ENABLED: bool = Field(
        default=False,
        description="Enable periodic BOM to MBOM sync job enqueue",
    )
    SCHEDULER_BOM_TO_MBOM_INTERVAL_SECONDS: int = Field(
        default=3600,
        description="Minimum seconds between BOM to MBOM scheduler jobs",
    )
    SCHEDULER_BOM_TO_MBOM_PRIORITY: int = Field(
        default=85,
        description="Queue priority for BOM to MBOM scheduler jobs",
    )
    SCHEDULER_BOM_TO_MBOM_MAX_ATTEMPTS: int = Field(
        default=1,
        description="Max attempts for BOM to MBOM scheduler jobs",
    )
    SCHEDULER_BOM_TO_MBOM_SOURCE_ITEM_IDS: str = Field(
        default="",
        description="Comma-separated source Part item ids eligible for scheduled MBOM sync",
    )
    SCHEDULER_BOM_TO_MBOM_PLANT_CODE: str = Field(
        default="",
        description="Optional plant code stamped on scheduled MBOMs",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Backward-compatible module-level settings instance.
settings = get_settings()
