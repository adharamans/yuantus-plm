from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import typer
import uvicorn

from yuantus import __version__
from yuantus.config import get_settings
from yuantus.context import org_id_var, tenant_id_var

app = typer.Typer(add_completion=False, help="YuantusPLM CLI")
search_app = typer.Typer(help="Search index maintenance")
app.add_typer(search_app, name="search")
license_app = typer.Typer(help="Offline license import/verify (PLM-COLLAB-P1-C)")
app.add_typer(license_app, name="license")


@license_app.command("import")
def license_import(
    path: str = typer.Argument(..., help="Path to a signed license JSON file"),
) -> None:
    """Verify a vendor-signed license file and activate it (offline, no network)."""
    import json

    from yuantus.config import get_settings
    from yuantus.database import get_db_session
    from yuantus.meta_engine.app_framework.license_import_service import (
        LicenseImportService,
    )
    from yuantus.meta_engine.app_framework.license_verification import (
        LicenseVerificationError,
    )

    with open(path, "r", encoding="utf-8") as fh:
        license_obj = json.load(fh)
    public_keys = get_settings().LICENSE_PUBLIC_KEYS
    try:
        with get_db_session() as session:
            activated = LicenseImportService(session).import_license(
                license_obj, public_keys
            )
            session.commit()
            for lic in activated:
                typer.echo(
                    f"activated tenant={lic.tenant_id} app={lic.app_name} "
                    f"expires={lic.expires_at}"
                )
    except (LicenseVerificationError, ValueError) as exc:
        typer.echo(f"license import failed: {exc}", err=True)
        raise typer.Exit(code=1)

    # The license is committed (the commercial source of truth). Best-effort: project any
    # paid seat cap (payload["seats"]) onto the identity-side TenantQuota.max_users -- the
    # cap the QuotaService provisioning gate enforces (QUOTA_MODE-gated, default-off).
    # A failure here never un-activates the license; re-running the import re-projects.
    try:
        from yuantus.security.auth.database import get_identity_db_session
        from yuantus.security.auth.seat_projection import project_license_seats

        # license_obj["payload"] is the SAME object import_license() just verified
        # (verify_license checks the Ed25519 signature over it), so this raw read is the
        # signature-authenticated value -- not untrusted input -- even post-commit.
        with get_identity_db_session() as identity_session:
            projected = project_license_seats(
                identity_session, license_obj.get("payload") or {}
            )
        if projected is not None:
            typer.echo(f"seat cap projected: TenantQuota.max_users={projected}")
    except Exception as exc:  # noqa: BLE001 -- best-effort; never fail an activated license
        typer.echo(
            f"warning: seat-cap projection failed ({exc}); license is active, seat cap "
            f"not applied (TenantQuota.max_users left unchanged). Re-run `yuantus license import` to retry.",
            err=True,
        )


@license_app.command("status")
def license_status(
    tenant_id: str = typer.Option(
        ..., "--tenant-id", help="tenant to report entitlement/license status for"
    ),
) -> None:
    """Print a tenant's collaboration entitlement + offline-license status (read-only).

    Safe for an operator support bundle: no private keys, no raw license_data.
    """
    from yuantus.database import get_db_session
    from yuantus.meta_engine.app_framework.license_status import collect_license_status

    token = tenant_id_var.set(str(tenant_id or "").strip())
    try:
        with get_db_session() as session:
            status = collect_license_status(session, tenant_id)
    finally:
        tenant_id_var.reset(token)

    typer.echo(f"license status for tenant: {status.tenant_id}")
    typer.echo("features (sellable SKUs):")
    for key, entitled in status.features.items():
        typer.echo(f"  {key:<24} {'ENTITLED' if entitled else 'not entitled'}")
    typer.echo(f"licenses ({len(status.licenses)}):")
    if not status.licenses:
        typer.echo("  (none for this tenant)")
    for row in status.licenses:
        expires = row.expires_at or "perpetual"
        typer.echo(
            f"  app={row.app_name:<24} status={row.status:<8} "
            f"expires={expires:<26} plan={row.plan_type or '-'}  key={row.license_key}"
        )


@app.callback()
def _root() -> None:
    invoked_as = Path(sys.argv[0]).name
    if invoked_as == "plm":
        typer.echo("Deprecated: use `yuantus` instead of `plm`.", err=True)


@app.command()
def start(
    host: Optional[str] = typer.Option(None, help="Bind host"),
    port: Optional[int] = typer.Option(None, help="Bind port"),
    reload: bool = typer.Option(False, help="Auto-reload on changes (dev)"),
) -> None:
    settings = get_settings()
    uvicorn.run(
        "yuantus.api.app:app",
        host=host or settings.HOST,
        port=port or settings.PORT,
        reload=reload,
    )


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command()
def worker(
    worker_id: Optional[str] = typer.Option(None, help="Worker id"),
    poll_interval: int = typer.Option(5, help="Poll interval seconds"),
    once: bool = typer.Option(False, help="Process one job then exit"),
    tenant: Optional[str] = typer.Option(
        None, "--tenant", help="Tenant id (for db-per-tenant/org)"
    ),
    org: Optional[str] = typer.Option(
        None, "--org", help="Org id (for db-per-tenant-org)"
    ),
) -> None:
    """
    Run a background job worker.

    This is a dev-friendly worker that polls the database for pending jobs.
    """
    if tenant is not None:
        tenant_id_var.set(tenant)
    if org is not None:
        org_id_var.set(org)

    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.meta_engine.services.job_worker import JobWorker
    from yuantus.meta_engine.tasks.cad_conversion_tasks import perform_cad_conversion
    from yuantus.meta_engine.tasks.cad_pipeline_tasks import (
        cad_dedup_vision,
        cad_geometry_with_binding,
        cad_extract,
        cad_ml_vision,
        cad_preview_with_binding,
        cad_bom,
    )
    from yuantus.meta_engine.tasks.system_tasks import quota_test
    from yuantus.meta_engine.tasks.scheduler_tasks import (
        audit_retention_prune,
        bom_to_mbom_sync,
        eco_approval_escalation,
    )
    from yuantus.meta_engine.tasks.breakage_tasks import (
        breakage_helpdesk_sync_stub,
        breakage_incidents_export,
        breakage_incidents_export_cleanup,
        parallel_ops_breakage_helpdesk_failures_export,
    )

    import_all_models()

    w = JobWorker(worker_id or "worker-1", poll_interval=poll_interval)
    w.register_handler("cad_conversion", perform_cad_conversion)
    w.register_handler("cad_preview", cad_preview_with_binding)
    w.register_handler("cad_geometry", cad_geometry_with_binding)
    w.register_handler("cad_extract", cad_extract)
    w.register_handler("cad_bom", cad_bom)
    w.register_handler("cad_dedup_vision", cad_dedup_vision)
    w.register_handler("cad_ml_vision", cad_ml_vision)
    w.register_handler("quota_test", quota_test)
    w.register_handler("eco_approval_escalation", eco_approval_escalation)
    w.register_handler("audit_retention_prune", audit_retention_prune)
    w.register_handler("bom_to_mbom_sync", bom_to_mbom_sync)
    w.register_handler("breakage_helpdesk_sync_stub", breakage_helpdesk_sync_stub)
    w.register_handler("breakage_incidents_export", breakage_incidents_export)
    w.register_handler(
        "breakage_incidents_export_cleanup",
        breakage_incidents_export_cleanup,
    )
    w.register_handler("parallel_ops_breakage_helpdesk_failures_export", parallel_ops_breakage_helpdesk_failures_export)
    try:
        from yuantus.plugin_manager.worker import register_plugin_job_handlers

        register_plugin_job_handlers(w)
    except Exception as exc:
        typer.echo(f"Warning: plugin job handlers not loaded: {exc}", err=True)

    if once:
        processed = w.run_once()
        if processed:
            typer.echo("Processed one job.")
        else:
            typer.echo("No pending jobs.")
        return

    w.start()
    typer.echo(f"Worker '{w.worker_id}' started. Press Ctrl+C to stop.", err=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        w.stop()
        typer.echo(f"Worker '{w.worker_id}' stopped.", err=True)


@app.command(name="publication-worker")
def publication_worker(
    worker_id: Optional[str] = typer.Option(None, help="Worker id"),
    poll_interval: Optional[int] = typer.Option(
        None, help="Poll interval seconds (default from settings)"
    ),
    once: bool = typer.Option(False, help="Drain one batch then exit"),
    tenant: Optional[str] = typer.Option(None, "--tenant", help="Tenant id"),
    org: Optional[str] = typer.Option(None, "--org", help="Org id"),
) -> None:
    """Run the PLM->ERP publication outbox worker (auto-drains due pending rows).

    R2: the only adapter is the no-I/O Null adapter, so a `send` reaches `sent`
    via Null (no real ERP). The real connector is a later slice.
    """
    if tenant is not None:
        tenant_id_var.set(tenant)
    if org is not None:
        org_id_var.set(org)

    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.meta_engine.erp_publication.worker import PublicationOutboxWorker

    import_all_models()

    w = PublicationOutboxWorker(
        worker_id or "publication-worker-1", poll_interval_seconds=poll_interval
    )
    if once:
        processed = w.run_once()
        typer.echo(f"Processed {processed} publication outbox row(s).")
        return

    typer.echo(
        f"Publication worker '{w.worker_id}' started. Press Ctrl+C to stop.", err=True
    )
    try:
        w.run_forever()
    except KeyboardInterrupt:
        w.stop()
        typer.echo(f"Publication worker '{w.worker_id}' stopped.", err=True)


@app.command(name="ecm-publication-worker")
def ecm_publication_worker(
    worker_id: Optional[str] = typer.Option(None, help="Worker id"),
    poll_interval: Optional[int] = typer.Option(
        None, help="Poll interval seconds (default from settings)"
    ),
    once: bool = typer.Option(False, help="Drain one batch then exit"),
    tenant: Optional[str] = typer.Option(None, "--tenant", help="Tenant id"),
    org: Optional[str] = typer.Option(None, "--org", help="Org id"),
) -> None:
    """Run the PLM->ECM (Athena) publication outbox worker (auto-drains due rows).

    ECM-P1C: the only adapter is the no-I/O Null adapter, so a `send` reaches
    `sent` via Null (nothing is written to a real Athena). The real CMIS connector
    is P1D (Phase-0-gated).

    Note: --tenant/--org only set the request context vars (parity with the erp
    worker); they do NOT scope which rows are drained -- the claim is tenant-agnostic
    and this worker drains ALL due rows.
    """
    if tenant is not None:
        tenant_id_var.set(tenant)
    if org is not None:
        org_id_var.set(org)

    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.meta_engine.ecm_publication.worker import EcmPublicationOutboxWorker

    import_all_models()

    w = EcmPublicationOutboxWorker(
        worker_id or "ecm-publication-worker-1", poll_interval_seconds=poll_interval
    )
    if once:
        processed = w.run_once()
        typer.echo(f"Processed {processed} ECM publication outbox row(s).")
        return

    typer.echo(
        f"ECM publication worker '{w.worker_id}' started. Press Ctrl+C to stop.",
        err=True,
    )
    try:
        w.run_forever()
    except KeyboardInterrupt:
        w.stop()
        typer.echo(f"ECM publication worker '{w.worker_id}' stopped.", err=True)


@app.command(name="date-obsolete-worker")
def date_obsolete_worker(
    worker_id: Optional[str] = typer.Option(None, help="Worker id"),
    poll_interval: Optional[int] = typer.Option(
        None, help="Poll interval seconds (default from settings)"
    ),
    once: bool = typer.Option(False, help="Run one sweep then exit"),
    system_user_id: Optional[int] = typer.Option(
        None,
        "--system-user-id",
        help=(
            "User id recorded for the auto-obsolete lifecycle promote (default from "
            "settings). Must be a real (service) user or the promote records "
            "child_obsolete_failed and only the parent flag is written."
        ),
    ),
    tenant: Optional[str] = typer.Option(None, "--tenant", help="Tenant id"),
    org: Optional[str] = typer.Option(None, "--org", help="Org id"),
) -> None:
    """Run the CAD-PDM C3 date-BOM auto-obsolete worker (default-OFF, double-gated).

    Each sweep scans EXPIRED date effectivities and, for every fully-expired Item
    (no remaining effective version), promotes it to lifecycle Obsolete and flags
    its depth-1 where-used parents; the sweep is idempotent, so re-running is safe.
    Two gates, both required: the global kill-switch DATE_EFFECTIVITY_OBSOLETE_ENABLED
    (restart-only) and the per-tenant `cadpdm_date_obsolete` entitlement.

    Tenant scope: unlike the publication workers, this sweep is PER-TENANT -- it runs
    under the context tenant's schema and checks that tenant's entitlement. In a
    multi-tenant deployment run one invocation per tenant (pass --tenant); in
    single-tenancy --tenant is not required.
    """
    settings = get_settings()
    mode = getattr(settings, "TENANCY_MODE", "single")

    # Operability guard: a multi-tenant deployment REQUIRES tenant (and, in
    # db-per-tenant-org, org) context -- get_db_session() raises a bare RuntimeError
    # otherwise, which on the --once path leaks as a raw traceback (run_forever logs and
    # retries). Fail fast with a clear message + exit 2 instead.
    if mode != "single" and tenant is None:
        typer.echo(
            f"--tenant is required in TENANCY_MODE={mode!r}: the date-obsolete sweep "
            "runs per-tenant. Run one invocation per tenant.",
            err=True,
        )
        raise typer.Exit(code=2)
    if mode == "db-per-tenant-org" and org is None:
        typer.echo(
            f"--org is required in TENANCY_MODE={mode!r}: this mode scopes the session by "
            "tenant AND org. Pass --org alongside --tenant.",
            err=True,
        )
        raise typer.Exit(code=2)

    if tenant is not None:
        tenant_id_var.set(tenant)
    if org is not None:
        org_id_var.set(org)

    # Operability hints (no behaviour change -- the worker is a no-op when gated off).
    if not getattr(settings, "DATE_EFFECTIVITY_OBSOLETE_ENABLED", False):
        typer.echo(
            "Note: DATE_EFFECTIVITY_OBSOLETE_ENABLED is off -- the worker is a no-op "
            "until you enable it and restart (the setting is read once at startup).",
            err=True,
        )
    effective_system_user = (
        system_user_id
        if system_user_id is not None
        else getattr(settings, "DATE_EFFECTIVITY_OBSOLETE_SYSTEM_USER_ID", 0)
    )
    if not effective_system_user:
        typer.echo(
            "Note: system user id is 0 -- the lifecycle promote will record "
            "child_obsolete_failed and only parent flags are written. Set "
            "--system-user-id or DATE_EFFECTIVITY_OBSOLETE_SYSTEM_USER_ID to a real "
            "service user to actually obsolete Items.",
            err=True,
        )

    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.meta_engine.services.date_obsolete_worker import DateObsoleteWorker

    import_all_models()

    w = DateObsoleteWorker(
        worker_id=worker_id or "cadpdm-date-obsolete-worker-1",
        poll_interval_seconds=poll_interval,
        system_user_id=system_user_id,
    )
    if once:
        processed = w.run_once()
        typer.echo(f"Processed {processed} expired date-effectivity row(s).")
        return

    typer.echo(
        f"Date-obsolete worker '{w.worker_id}' started. Press Ctrl+C to stop.",
        err=True,
    )
    try:
        w.run_forever()
    except KeyboardInterrupt:
        w.stop()
        typer.echo(f"Date-obsolete worker '{w.worker_id}' stopped.", err=True)


@app.command()
def scheduler(
    poll_interval: Optional[int] = typer.Option(
        None,
        help="Scheduler poll interval seconds",
    ),
    once: bool = typer.Option(False, help="Run one scheduler tick then exit"),
    force: bool = typer.Option(
        False,
        help="Run even when SCHEDULER_ENABLED=false",
    ),
    dry_run: bool = typer.Option(
        False,
        help="Evaluate due scheduler jobs without enqueueing them",
    ),
    tenant: Optional[str] = typer.Option(
        None,
        "--tenant",
        help="Tenant id for scheduled job payload/context",
    ),
    org: Optional[str] = typer.Option(
        None,
        "--org",
        help="Org id for scheduled job payload/context",
    ),
) -> None:
    """Run the lightweight scheduler that enqueues due periodic jobs."""
    if tenant is not None:
        tenant_id_var.set(tenant)
    if org is not None:
        org_id_var.set(org)

    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.database import get_db_session
    from yuantus.meta_engine.services.scheduler_service import SchedulerService

    import_all_models()
    settings = get_settings()
    interval = (
        int(poll_interval)
        if poll_interval is not None
        else int(settings.SCHEDULER_POLL_INTERVAL_SECONDS or 60)
    )

    def _tick() -> int:
        with get_db_session() as session:
            result = SchedulerService(session).run_once(force=force, dry_run=dry_run)
        typer.echo(
            json.dumps(
                {
                    "enqueued": [d.__dict__ for d in result.enqueued],
                    "would_enqueue": [d.__dict__ for d in result.would_enqueue],
                    "skipped": [d.__dict__ for d in result.skipped],
                    "disabled": [d.__dict__ for d in result.disabled],
                },
                sort_keys=True,
            )
        )
        return result.enqueued_count

    if once:
        _tick()
        return

    if not force and not settings.SCHEDULER_ENABLED:
        typer.echo("Scheduler disabled (set YUANTUS_SCHEDULER_ENABLED=true).", err=True)
        return

    typer.echo(
        "Scheduler dry-run started. Press Ctrl+C to stop."
        if dry_run
        else "Scheduler started. Press Ctrl+C to stop.",
        err=True,
    )
    try:
        while True:
            _tick()
            time.sleep(max(interval, 1))
    except KeyboardInterrupt:
        typer.echo("Scheduler stopped.", err=True)


@search_app.command("reindex")
def search_reindex(
    item_type: Optional[str] = typer.Option(None, help="ItemType id to reindex"),
    reset: bool = typer.Option(False, help="Delete index before reindex"),
    limit: Optional[int] = typer.Option(None, help="Limit items to reindex"),
    batch_size: int = typer.Option(200, help="Batch size for reindex"),
    tenant: Optional[str] = typer.Option(
        None, "--tenant", help="Tenant id (for db-per-tenant/org)"
    ),
    org: Optional[str] = typer.Option(
        None, "--org", help="Org id (for db-per-tenant-org)"
    ),
) -> None:
    if tenant is not None:
        tenant_id_var.set(tenant)
    if org is not None:
        org_id_var.set(org)

    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.database import get_db_session
    from yuantus.meta_engine.services.search_service import SearchService

    import_all_models()

    with get_db_session() as session:
        service = SearchService(session)
        result = service.reindex_items(
            item_type_id=item_type,
            reset=reset,
            limit=limit,
            batch_size=batch_size,
        )

    typer.echo(json.dumps(result, indent=2, default=str))


@search_app.command("reindex-ecos")
def search_reindex_ecos(
    state: Optional[str] = typer.Option(None, help="Filter ECO state"),
    reset: bool = typer.Option(False, help="Delete index before reindex"),
    limit: Optional[int] = typer.Option(None, help="Limit ECOs to reindex"),
    batch_size: int = typer.Option(200, help="Batch size for reindex"),
    tenant: Optional[str] = typer.Option(
        None, "--tenant", help="Tenant id (for db-per-tenant/org)"
    ),
    org: Optional[str] = typer.Option(
        None, "--org", help="Org id (for db-per-tenant-org)"
    ),
) -> None:
    if tenant is not None:
        tenant_id_var.set(tenant)
    if org is not None:
        org_id_var.set(org)

    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.database import get_db_session
    from yuantus.meta_engine.services.search_service import SearchService

    import_all_models()

    with get_db_session() as session:
        service = SearchService(session)
        result = service.reindex_ecos(
            state=state,
            reset=reset,
            limit=limit,
            batch_size=batch_size,
        )

    typer.echo(json.dumps(result, indent=2, default=str))


@app.command("seed-identity")
def seed_identity(
    tenant: str = typer.Option("tenant-1", help="Tenant id"),
    org: str = typer.Option("org-1", help="Org id"),
    username: str = typer.Option("admin", help="Username"),
    password: str = typer.Option("admin", help="Password (dev only)"),
    email: str = typer.Option("admin@example.com", help="Email"),
    user_id: int = typer.Option(1, help="Identity user id (int)"),
    roles: str = typer.Option("admin", help="Comma-separated role names"),
    superuser: bool = typer.Option(True, help="Grant superuser"),
) -> None:
    """
    Seed identity DB for development: tenant/org/user/membership.
    """
    from yuantus.security.auth.database import get_identity_db_session, init_identity_db
    from yuantus.security.auth.models import AuthUser
    from yuantus.security.auth.service import AuthService

    init_identity_db(create_tables=True)
    role_list = [r.strip() for r in (roles or "").split(",") if r.strip()]

    with get_identity_db_session() as session:
        svc = AuthService(session)
        svc.ensure_tenant(tenant, name=tenant)
        svc.ensure_org(tenant, org, name=org)

        existing = (
            session.query(AuthUser)
            .filter(AuthUser.tenant_id == tenant, AuthUser.username == username)
            .first()
        )
        if not existing:
            user = svc.create_user(
                tenant_id=tenant,
                username=username,
                password=password,
                email=email,
                is_superuser=superuser,
                user_id=user_id,
            )
        else:
            existing.is_active = True
            existing.is_superuser = bool(superuser)
            svc.set_password(tenant_id=tenant, username=username, password=password)
            user = existing

        svc.add_membership(
            tenant_id=tenant, org_id=org, user_id=user.id, roles=role_list
        )

    typer.echo(f"Seeded identity: tenant={tenant}, org={org}, user={username} ({user_id})")


@app.command("seed-meta")
def seed_meta(
    tenant: Optional[str] = typer.Option(
        None, "--tenant", help="Tenant id (used when TENANCY_MODE=db-per-tenant)"
    ),
    org: Optional[str] = typer.Option(
        None, "--org", help="Org id (used when TENANCY_MODE=db-per-tenant-org)"
    )
) -> None:
    """
    Seed minimal Meta Engine schema (ItemType/Property) for local development.
    """
    from yuantus.config import get_settings
    from yuantus.database import (
        SessionLocal as GlobalSessionLocal,
        engine as GlobalEngine,
        get_engine_for_scope,
        get_engine_for_tenant,
        get_sessionmaker_for_scope,
        get_sessionmaker_for_tenant,
        init_db,
    )
    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.meta_engine.lifecycle.models import (
        LifecycleMap,
        LifecycleState,
        LifecycleTransition,
    )
    from yuantus.meta_engine.models.meta_schema import ItemType, Property
    from yuantus.meta_engine.permission.models import Access, Permission
    from yuantus.models.base import Base
    from yuantus.security.rbac.models import RBACRole, RBACUser

    settings = get_settings()
    if settings.TENANCY_MODE == "db-per-tenant-org":
        engine = get_engine_for_scope(tenant, org)
        SessionLocal = get_sessionmaker_for_scope(tenant, org)
    elif settings.TENANCY_MODE == "db-per-tenant":
        engine = get_engine_for_tenant(tenant)
        SessionLocal = get_sessionmaker_for_tenant(tenant)
    else:
        engine = GlobalEngine
        SessionLocal = GlobalSessionLocal

    import_all_models()
    init_db(create_tables=True, bind_engine=engine)

    session = SessionLocal()
    try:
        # Dev identity baseline (needed by VersionHistory FK constraints)
        admin_user = session.get(RBACUser, 1)
        if not admin_user:
            admin_user = RBACUser(
                id=1,
                user_id=1,
                username="admin",
                email="admin@example.com",
                is_active=True,
                is_superuser=True,
            )
            session.add(admin_user)

        admin_role = session.query(RBACRole).filter_by(name="admin").first()
        if not admin_role:
            admin_role = RBACRole(
                name="admin",
                display_name="Admin",
                description="Built-in admin role (dev)",
                is_system=True,
                is_active=True,
                priority=1000,
            )
            session.add(admin_role)
            session.flush()

        if admin_role not in (admin_user.roles or []):
            admin_user.roles.append(admin_role)

        default_perm = session.get(Permission, "Default")
        if not default_perm:
            default_perm = Permission(id="Default", name="Default (dev)")
            session.add(default_perm)
            session.add(
                Access(
                    id="Default:world",
                    permission=default_perm,
                    identity_id="world",
                    can_create=True,
                    can_get=True,
                    can_update=True,
                    can_delete=True,
                    can_discover=True,
                )
            )

        part = session.get(ItemType, "Part")
        if not part:
            part = ItemType(id="Part", label="Part", is_versionable=True)
            session.add(part)
        if not part.permission_id:
            part.permission_id = default_perm.id

        props = [
            Property(
                name="item_number",
                label="Part Number",
                is_required=True,
                length=32,
                data_type="string",
            ),
            Property(name="name", label="Name", length=128, data_type="string"),
            Property(
                name="description",
                label="Description",
                length=256,
                data_type="string",
            ),
            Property(
                name="revision",
                label="Revision",
                length=32,
                data_type="string",
            ),
            Property(
                name="state",
                label="State",
                default_value="Draft",
                data_type="string",
            ),
            Property(name="cost", label="Cost", data_type="float"),
            Property(name="weight", label="Weight", data_type="float"),
            Property(name="weight_rollup", label="Weight Rollup", data_type="float"),
        ]
        existing_prop_names = {p.name for p in (part.properties or [])}
        for prop in props:
            if prop.name in existing_prop_names:
                continue
            prop.item_type = part
            session.add(prop)

        part_bom = session.get(ItemType, "Part BOM")
        if not part_bom:
            part_bom = ItemType(id="Part BOM", label="Part BOM", is_relationship=True)
            part_bom.source_item_type_id = "Part"
            part_bom.related_item_type_id = "Part"
            session.add(part_bom)
        if not part_bom.permission_id:
            part_bom.permission_id = default_perm.id

        if not any(p.name == "quantity" for p in (part_bom.properties or [])):
            session.add(
                Property(
                    item_type=part_bom,
                    name="quantity",
                    label="Quantity",
                    data_type="float",
                    default_value="1.0",
                )
            )

        def ensure_lifecycle_map(name: str, description: str) -> LifecycleMap:
            lifecycle = session.query(LifecycleMap).filter_by(name=name).first()
            if not lifecycle:
                lifecycle = LifecycleMap(
                    id=str(uuid.uuid4()),
                    name=name,
                    description=description,
                )
                session.add(lifecycle)
                session.flush()
            return lifecycle

        def ensure_state(
            lifecycle: LifecycleMap,
            name: str,
            sequence: int,
            *,
            is_start: bool = False,
            is_end: bool = False,
            is_released: bool = False,
            is_suspended: bool = False,
            version_lock: bool = False,
        ) -> LifecycleState:
            if is_start:
                session.query(LifecycleState).filter_by(
                    lifecycle_map_id=lifecycle.id, is_start_state=True
                ).update({"is_start_state": False})
            state = (
                session.query(LifecycleState)
                .filter_by(lifecycle_map_id=lifecycle.id, name=name)
                .first()
            )
            if not state:
                state = LifecycleState(
                    id=str(uuid.uuid4()),
                    lifecycle_map_id=lifecycle.id,
                    name=name,
                    label=name,
                    sequence=sequence,
                )
                session.add(state)
            state.sequence = sequence
            state.is_start_state = is_start
            state.is_end_state = is_end
            state.is_released = is_released
            state.is_suspended = is_suspended
            state.version_lock = version_lock
            session.flush()
            return state

        def ensure_transition(
            lifecycle: LifecycleMap,
            from_state: LifecycleState,
            to_state: LifecycleState,
            action_name: str,
            role_id: Optional[int],
        ) -> None:
            transition = (
                session.query(LifecycleTransition)
                .filter_by(
                    lifecycle_map_id=lifecycle.id,
                    from_state_id=from_state.id,
                    to_state_id=to_state.id,
                )
                .first()
            )
            if not transition:
                transition = LifecycleTransition(
                    id=str(uuid.uuid4()),
                    lifecycle_map_id=lifecycle.id,
                    from_state_id=from_state.id,
                    to_state_id=to_state.id,
                )
                session.add(transition)
            transition.action_name = action_name
            transition.role_allowed_id = role_id

        document = session.get(ItemType, "Document")
        if not document:
            document = ItemType(id="Document", label="Document", is_versionable=True)
            session.add(document)
        if not document.permission_id:
            document.permission_id = default_perm.id

        doc_props = [
            Property(
                name="doc_number",
                label="Document Number",
                is_required=True,
                length=64,
                data_type="string",
            ),
            Property(name="name", label="Title", length=256, data_type="string"),
            Property(
                name="description",
                label="Description",
                length=512,
                data_type="string",
            ),
            Property(
                name="state",
                label="State",
                default_value="Draft",
                data_type="string",
            ),
        ]
        existing_doc_props = {p.name for p in (document.properties or [])}
        for prop in doc_props:
            if prop.name in existing_doc_props:
                continue
            prop.item_type = document
            session.add(prop)

        admin_role_id = admin_role.id if admin_role else None
        document_lifecycle = ensure_lifecycle_map(
            "Document Lifecycle", "Controlled release for documents"
        )
        doc_draft_state = ensure_state(document_lifecycle, "Draft", 10, is_start=True)
        doc_review_state = ensure_state(document_lifecycle, "Review", 20)
        doc_released_state = ensure_state(
            document_lifecycle,
            "Released",
            30,
            is_released=True,
            version_lock=True,
        )
        doc_suspended_state = ensure_state(
            document_lifecycle,
            "Suspended",
            35,
            version_lock=True,
        )
        doc_obsolete_state = ensure_state(
            document_lifecycle,
            "Obsolete",
            40,
            is_end=True,
            version_lock=True,
        )
        ensure_transition(
            document_lifecycle,
            doc_draft_state,
            doc_review_state,
            "submit",
            admin_role_id,
        )
        ensure_transition(
            document_lifecycle,
            doc_review_state,
            doc_draft_state,
            "reject",
            admin_role_id,
        )
        ensure_transition(
            document_lifecycle,
            doc_review_state,
            doc_released_state,
            "release",
            admin_role_id,
        )
        ensure_transition(
            document_lifecycle,
            doc_released_state,
            doc_obsolete_state,
            "obsolete",
            admin_role_id,
        )
        ensure_transition(
            document_lifecycle,
            doc_released_state,
            doc_suspended_state,
            "suspend",
            admin_role_id,
        )
        ensure_transition(
            document_lifecycle,
            doc_suspended_state,
            doc_released_state,
            "resume",
            admin_role_id,
        )
        ensure_transition(
            document_lifecycle,
            doc_suspended_state,
            doc_obsolete_state,
            "obsolete",
            admin_role_id,
        )

        if not document.lifecycle_map_id:
            document.lifecycle_map_id = document_lifecycle.id

        part_lifecycle = ensure_lifecycle_map(
            "Part Lifecycle", "Controlled release for parts"
        )
        part_draft_state = ensure_state(part_lifecycle, "Draft", 10, is_start=True)
        part_review_state = ensure_state(part_lifecycle, "Review", 20)
        part_released_state = ensure_state(
            part_lifecycle,
            "Released",
            30,
            is_released=True,
            version_lock=True,
        )
        part_suspended_state = ensure_state(
            part_lifecycle,
            "Suspended",
            35,
            is_suspended=True,
            version_lock=True,
        )
        part_obsolete_state = ensure_state(
            part_lifecycle,
            "Obsolete",
            40,
            is_end=True,
            version_lock=True,
        )
        ensure_transition(
            part_lifecycle,
            part_draft_state,
            part_review_state,
            "submit",
            admin_role_id,
        )
        ensure_transition(
            part_lifecycle,
            part_review_state,
            part_draft_state,
            "reject",
            admin_role_id,
        )
        ensure_transition(
            part_lifecycle,
            part_review_state,
            part_released_state,
            "release",
            admin_role_id,
        )
        ensure_transition(
            part_lifecycle,
            part_released_state,
            part_obsolete_state,
            "obsolete",
            admin_role_id,
        )
        ensure_transition(
            part_lifecycle,
            part_released_state,
            part_suspended_state,
            "suspend",
            admin_role_id,
        )
        ensure_transition(
            part_lifecycle,
            part_suspended_state,
            part_released_state,
            "resume",
            admin_role_id,
        )
        ensure_transition(
            part_lifecycle,
            part_suspended_state,
            part_obsolete_state,
            "obsolete",
            admin_role_id,
        )

        if not part.lifecycle_map_id:
            part.lifecycle_map_id = part_lifecycle.id

        session.commit()
        typer.echo("Seeded meta schema: Part, Part BOM, Document")
    finally:
        session.close()


@app.command("seed-data")
def seed_data(
    part_count: int = typer.Option(100, help="Number of parts to generate"),
    doc_count: int = typer.Option(50, help="Number of documents to generate"),
    bom_roots: int = typer.Option(10, help="Number of root assemblies (finished goods)"),
    bom_depth: int = typer.Option(3, help="Depth of BOM structure"),
    tenant: Optional[str] = typer.Option(
        None, "--tenant", help="Tenant id (used when TENANCY_MODE=db-per-tenant)"
    ),
    org: Optional[str] = typer.Option(
        None, "--org", help="Org id (used when TENANCY_MODE=db-per-tenant-org)"
    )
) -> None:
    """
    Generate mock data (Parts, Documents, BOMs) for development.
    Requires 'seed-meta' to be run first.
    """
    from yuantus.config import get_settings
    from yuantus.database import (
        SessionLocal as GlobalSessionLocal,
        engine as GlobalEngine,
        get_engine_for_scope,
        get_engine_for_tenant,
        get_sessionmaker_for_scope,
        get_sessionmaker_for_tenant,
    )
    from yuantus.scripts.mock_data import run_seed

    settings = get_settings()
    if settings.TENANCY_MODE == "db-per-tenant-org":
        SessionLocal = get_sessionmaker_for_scope(tenant, org)
    elif settings.TENANCY_MODE == "db-per-tenant":
        SessionLocal = get_sessionmaker_for_tenant(tenant)
    else:
        SessionLocal = GlobalSessionLocal

    session = SessionLocal()
    try:
        run_seed(
            session,
            part_count=part_count,
            doc_count=doc_count,
            bom_roots=bom_roots,
            bom_depth=bom_depth
        )
    except Exception as e:
        typer.echo(f"Error seeding data: {e}", err=True)
        # Import might fail if ItemType not found
        typer.echo("Hint: Did you run 'yuantus seed-meta' first?", err=True)
        raise typer.Exit(1)
    finally:
        session.close()


@app.command("db")
def db_command(
    action: str = typer.Argument(
        ..., help="upgrade|downgrade|revision|current|history|stamp"
    ),
    message: Optional[str] = typer.Option(
        None, "--message", "-m", help="Migration message (for revision)"
    ),
    autogenerate: bool = typer.Option(
        True, "--autogenerate/--no-autogenerate", help="Autogenerate migration"
    ),
    revision: Optional[str] = typer.Option(
        None, "--revision", "-r", help="Target revision (for upgrade/downgrade)"
    ),
    db_url: Optional[str] = typer.Option(
        None,
        "--db-url",
        help="Override database URL for migrations (e.g. identity DB).",
    ),
    identity: bool = typer.Option(
        False,
        "--identity/--no-identity",
        help="Use IDENTITY_DATABASE_URL for migrations.",
    ),
    identity_only: bool = typer.Option(
        False,
        "--identity-only/--no-identity-only",
        help="Use IDENTITY_DATABASE_URL with identity-only migrations (auth + audit only).",
    ),
) -> None:
    """
    Database migrations via Alembic.

    Actions:
      upgrade   - Apply migrations (default: head)
      downgrade - Revert migrations
      revision  - Create new migration
      current   - Show current revision
      history   - Show migration history
      stamp     - Set revision without running migrations
    """
    import os
    import subprocess
    import sys

    if identity_only and (identity or db_url):
        typer.echo(
            "Error: --identity-only cannot be combined with --identity or --db-url",
            err=True,
        )
        raise typer.Exit(1)

    if db_url and identity:
        typer.echo("Error: --db-url and --identity are mutually exclusive", err=True)
        raise typer.Exit(1)

    config_basename = "alembic.identity.ini" if identity_only else "alembic.ini"

    # Find alembic config
    alembic_ini = os.path.join(os.getcwd(), config_basename)
    if not os.path.exists(alembic_ini):
        # Try package directory
        pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        alembic_ini = os.path.join(pkg_dir, config_basename)
        if not os.path.exists(alembic_ini):
            typer.echo(f"Error: {config_basename} not found", err=True)
            raise typer.Exit(1)

    # Build alembic command
    cmd = [sys.executable, "-m", "alembic", "-c", alembic_ini]

    if action == "upgrade":
        target = revision or "head"
        cmd.extend(["upgrade", target])
    elif action == "downgrade":
        target = revision or "-1"
        cmd.extend(["downgrade", target])
    elif action == "revision":
        cmd.append("revision")
        if autogenerate:
            cmd.append("--autogenerate")
        if message:
            cmd.extend(["-m", message])
        else:
            typer.echo("Warning: No message provided, using default", err=True)
            cmd.extend(["-m", "auto migration"])
    elif action == "current":
        cmd.append("current")
    elif action == "history":
        cmd.append("history")
    elif action == "stamp":
        target = revision or "head"
        cmd.extend(["stamp", target])
    else:
        typer.echo(f"Unknown action: {action}", err=True)
        raise typer.Exit(1)

    env = os.environ.copy()
    if identity or identity_only:
        from yuantus.config import get_settings

        settings = get_settings()
        resolved_url = settings.IDENTITY_DATABASE_URL or settings.DATABASE_URL
        if not resolved_url:
            typer.echo(
                "Error: IDENTITY_DATABASE_URL is not set and DATABASE_URL is empty",
                err=True,
            )
            raise typer.Exit(1)
        env["YUANTUS_DATABASE_URL"] = resolved_url
    elif db_url:
        env["YUANTUS_DATABASE_URL"] = db_url

    typer.echo(f"Running: {' '.join(cmd)}", err=True)
    result = subprocess.run(cmd, cwd=os.getcwd(), env=env)
    raise typer.Exit(result.returncode)


@app.command("init-storage")
def init_storage(
    bucket: Optional[str] = typer.Option(None, help="Bucket name (default: from settings)"),
) -> None:
    """
    Initialize S3/MinIO storage bucket.

    Creates the bucket if it doesn't exist.
    Only needed for STORAGE_TYPE=s3.
    """
    from yuantus.config import get_settings

    settings = get_settings()
    if settings.STORAGE_TYPE != "s3":
        typer.echo(f"Storage type is '{settings.STORAGE_TYPE}', not 's3'. Nothing to initialize.")
        return

    bucket_name = bucket or settings.S3_BUCKET_NAME

    try:
        import boto3
        from botocore.exceptions import ClientError

        s3_client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name=settings.S3_REGION_NAME,
        )

        # Check if bucket exists
        try:
            s3_client.head_bucket(Bucket=bucket_name)
            typer.echo(f"Bucket '{bucket_name}' already exists.")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchBucket"):
                # Create bucket
                try:
                    if settings.S3_REGION_NAME == "us-east-1":
                        s3_client.create_bucket(Bucket=bucket_name)
                    else:
                        s3_client.create_bucket(
                            Bucket=bucket_name,
                            CreateBucketConfiguration={
                                "LocationConstraint": settings.S3_REGION_NAME
                            },
                        )
                    typer.echo(f"Created bucket '{bucket_name}'.")
                except ClientError as create_error:
                    typer.echo(f"Error creating bucket: {create_error}", err=True)
                    raise typer.Exit(1)
            else:
                typer.echo(f"Error checking bucket: {e}", err=True)
                raise typer.Exit(1)

    except ImportError:
        typer.echo("Error: boto3 not installed. Run: pip install boto3", err=True)
        raise typer.Exit(1)


def main() -> None:
    app()
