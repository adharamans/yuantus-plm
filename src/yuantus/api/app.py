from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from yuantus import __version__
from yuantus.api.middleware.auth_enforce import AuthEnforcementMiddleware
from yuantus.api.middleware.audit import AuditLogMiddleware
from yuantus.api.middleware.context import TenantOrgContextMiddleware
from yuantus.api.middleware.request_logging import RequestLoggingMiddleware
from yuantus.api.routers.admin import router as admin_router
from yuantus.api.routers.auth import router as auth_router
from yuantus.api.routers.cad_preview import router as cad_preview_router
from yuantus.api.routers.favicon import router as favicon_router
from yuantus.api.routers.health import router as health_router
from yuantus.api.routers.integrations import router as integrations_router
from yuantus.meta_engine.web.integration_capabilities_router import integration_capabilities_router
from yuantus.api.routers.jobs import router as jobs_router
from yuantus.api.routers.metrics import router as metrics_router
from yuantus.api.routers.plugins import router as plugins_router
from yuantus.api.routers.plm_workspace import router as plm_workspace_router
from yuantus.api.routers.workbench import router as workbench_router
from yuantus.config import get_settings
from yuantus.database import init_db
from yuantus.meta_engine.web.bom_compare_router import bom_compare_router
from yuantus.meta_engine.web.approval_automation_capabilities_router import approval_automation_capabilities_router
from yuantus.meta_engine.web.approval_automation_eco_router import approval_automation_eco_router
from yuantus.meta_engine.web.approval_automation_router import approval_automation_router
from yuantus.meta_engine.web.feature_router import feature_router
from yuantus.meta_engine.web.license_status_router import license_status_router
from yuantus.meta_engine.web.license_cap_change_history_router import license_cap_change_history_router
from yuantus.meta_engine.web.license_revoke_router import license_revoke_router
from yuantus.meta_engine.web.bom_multitable_router import bom_multitable_router
from yuantus.meta_engine.web.bom_tree_router import bom_tree_router
from yuantus.meta_engine.web.bom_children_router import bom_children_router
from yuantus.meta_engine.web.bom_obsolete_rollup_router import (
    bom_obsolete_rollup_router,
)
from yuantus.meta_engine.web.bom_where_used_router import bom_where_used_router
from yuantus.meta_engine.web.bom_substitutes_router import bom_substitutes_router
from yuantus.meta_engine.web.baseline_router import baseline_router
from yuantus.meta_engine.web.box_aging_router import box_aging_router
from yuantus.meta_engine.web.box_analytics_router import box_analytics_router
from yuantus.meta_engine.web.box_capacity_router import box_capacity_router
from yuantus.meta_engine.web.box_core_router import box_core_router
from yuantus.meta_engine.web.box_custody_router import box_custody_router
from yuantus.meta_engine.web.box_ops_router import box_ops_router
from yuantus.meta_engine.web.box_policy_router import box_policy_router
from yuantus.meta_engine.web.box_reconciliation_router import (
    box_reconciliation_router,
)
from yuantus.meta_engine.web.box_traceability_router import box_traceability_router
from yuantus.meta_engine.web.box_turnover_router import box_turnover_router
from yuantus.meta_engine.web.config_router import config_router
from yuantus.meta_engine.web.cutted_parts_alerts_router import cutted_parts_alerts_router
from yuantus.meta_engine.web.cutted_parts_analytics_router import (
    cutted_parts_analytics_router,
)
from yuantus.meta_engine.web.cutted_parts_benchmark_router import (
    cutted_parts_benchmark_router,
)
from yuantus.meta_engine.web.cutted_parts_bottlenecks_router import (
    cutted_parts_bottlenecks_router,
)
from yuantus.meta_engine.web.cutted_parts_core_router import cutted_parts_core_router
from yuantus.meta_engine.web.cutted_parts_scenarios_router import (
    cutted_parts_scenarios_router,
)
from yuantus.meta_engine.web.cutted_parts_thresholds_router import (
    cutted_parts_thresholds_router,
)
from yuantus.meta_engine.web.cutted_parts_throughput_router import (
    cutted_parts_throughput_router,
)
from yuantus.meta_engine.web.cutted_parts_utilization_router import (
    cutted_parts_utilization_router,
)
from yuantus.meta_engine.web.cutted_parts_variance_router import (
    cutted_parts_variance_router,
)
from yuantus.meta_engine.web.cad_backend_profile_router import cad_backend_profile_router
from yuantus.meta_engine.web.cad_checkin_router import cad_checkin_router
from yuantus.meta_engine.web.cad_connectors_router import cad_connectors_router
from yuantus.meta_engine.web.cad_consistency_router import cad_consistency_router
from yuantus.meta_engine.web.cad_diff_router import cad_diff_router
from yuantus.meta_engine.web.cad_file_data_router import cad_file_data_router
from yuantus.meta_engine.web.cad_history_router import cad_history_router
from yuantus.meta_engine.web.cad_import_router import cad_import_router
from yuantus.meta_engine.web.cad_mesh_stats_router import cad_mesh_stats_router
from yuantus.meta_engine.web.cad_properties_router import cad_properties_router
from yuantus.meta_engine.web.cad_review_router import cad_review_router
from yuantus.meta_engine.web.cad_sync_template_router import cad_sync_template_router
from yuantus.meta_engine.web.cad_view_state_router import cad_view_state_router
from yuantus.meta_engine.web.cad_router import router as cad_router
from yuantus.meta_engine.web.dedup_router import dedup_router
from yuantus.meta_engine.web.document_sync_analytics_router import (
    document_sync_analytics_router,
)
from yuantus.meta_engine.web.document_sync_core_router import document_sync_core_router
from yuantus.meta_engine.web.document_sync_drift_router import document_sync_drift_router
from yuantus.meta_engine.web.document_sync_freshness_router import (
    document_sync_freshness_router,
)
from yuantus.meta_engine.web.document_sync_lineage_router import (
    document_sync_lineage_router,
)
from yuantus.meta_engine.web.document_sync_reconciliation_router import (
    document_sync_reconciliation_router,
)
from yuantus.meta_engine.web.document_sync_replay_audit_router import (
    document_sync_replay_audit_router,
)
from yuantus.meta_engine.web.document_sync_retention_router import (
    document_sync_retention_router,
)
from yuantus.meta_engine.web.eco_approval_ops_router import eco_approval_ops_router
from yuantus.meta_engine.web.eco_approval_workflow_router import (
    eco_approval_workflow_router,
)
from yuantus.meta_engine.web.eco_change_analysis_router import (
    eco_change_analysis_router,
)
from yuantus.meta_engine.web.eco_impact_apply_router import eco_impact_apply_router
from yuantus.meta_engine.web.eco_lifecycle_router import eco_lifecycle_router
from yuantus.meta_engine.web.eco_core_router import eco_core_router
from yuantus.meta_engine.web.eco_stage_router import eco_stage_router
from yuantus.meta_engine.web.approval_category_router import (
    approval_category_router,
)
from yuantus.meta_engine.web.approval_request_router import (
    approval_request_router,
)
from yuantus.meta_engine.web.approval_ops_router import approval_ops_router
from yuantus.meta_engine.web.app_router import app_router
from yuantus.meta_engine.web.change_router import change_router
from yuantus.meta_engine.web.equivalent_router import equivalent_router
from yuantus.meta_engine.web.spare_router import spare_router
from yuantus.meta_engine.web.effectivity_router import effectivity_router
from yuantus.meta_engine.web.file_attachment_router import file_attachment_router
from yuantus.meta_engine.web.file_conversion_router import file_conversion_router
from yuantus.meta_engine.web.file_metadata_router import file_metadata_router
from yuantus.meta_engine.web.file_storage_router import file_storage_router
from yuantus.meta_engine.web.file_viewer_router import file_viewer_router
from yuantus.meta_engine.web.esign_router import esign_router
from yuantus.meta_engine.web.pdm_assembly_promotion_router import (
    pdm_assembly_promotion_router,
)
from yuantus.meta_engine.web.pdm_relationship_router import pdm_relationship_router
from yuantus.meta_engine.web.permission_router import permission_router
from yuantus.meta_engine.web.product_router import product_router
from yuantus.meta_engine.web.release_readiness_router import release_readiness_router
from yuantus.meta_engine.web.release_validation_router import release_validation_router
from yuantus.meta_engine.web.release_orchestration_router import (
    release_orchestration_router,
)
from yuantus.meta_engine.web.plm_erp_publication_router import (
    publication_readiness_router,
)
from yuantus.meta_engine.web.plm_erp_publication_outbox_router import (
    publication_outbox_router,
)
from yuantus.meta_engine.web.plm_ecm_publication_outbox_router import (
    ecm_publication_outbox_router,
)
from yuantus.meta_engine.web.report_saved_search_router import (
    report_saved_search_router,
)
from yuantus.meta_engine.web.report_summary_search_router import (
    report_summary_search_router,
)
from yuantus.meta_engine.web.report_definition_router import (
    report_definition_router,
)
from yuantus.meta_engine.web.report_dashboard_router import (
    report_dashboard_router,
)
from yuantus.meta_engine.web.query_router import query_router
from yuantus.meta_engine.web.rpc_router import rpc_router
from yuantus.meta_engine.web.router import meta_router
from yuantus.meta_engine.web.schema_router import schema_router
from yuantus.meta_engine.web.search_router import search_router
from yuantus.meta_engine.web.store_router import store_router
from yuantus.meta_engine.web.impact_router import impact_router
from yuantus.meta_engine.web.item_cockpit_router import item_cockpit_router
from yuantus.meta_engine.web.locale_router import locale_router
from yuantus.meta_engine.web.maintenance_category_router import (
    maintenance_category_router,
)
from yuantus.meta_engine.web.maintenance_equipment_router import (
    maintenance_equipment_router,
)
from yuantus.meta_engine.web.maintenance_request_router import (
    maintenance_request_router,
)
from yuantus.meta_engine.web.maintenance_schedule_router import (
    maintenance_schedule_router,
)
from yuantus.meta_engine.web.quality_alerts_router import quality_alerts_router
from yuantus.meta_engine.web.quality_checks_router import quality_checks_router
from yuantus.meta_engine.web.quality_points_router import quality_points_router
from yuantus.meta_engine.web.quality_analytics_router import quality_analytics_router
from yuantus.meta_engine.web.subcontracting_orders_router import (
    subcontracting_orders_router,
)
from yuantus.meta_engine.web.subcontracting_analytics_router import (
    subcontracting_analytics_router,
)
from yuantus.meta_engine.web.subcontracting_approval_mapping_router import (
    subcontracting_approval_mapping_router,
)
from yuantus.meta_engine.web.ui_router import ui_router
from yuantus.meta_engine.web.version_effectivity_router import version_effectivity_router
from yuantus.meta_engine.web.version_file_router import version_file_router
from yuantus.meta_engine.web.version_iteration_router import version_iteration_router
from yuantus.meta_engine.web.version_lifecycle_router import version_lifecycle_router
from yuantus.meta_engine.web.version_revision_router import version_revision_router
from yuantus.meta_engine.web.manufacturing_router import (
    mbom_router,
    routing_router,
    workcenter_router,
)
from yuantus.meta_engine.web.parallel_tasks_breakage_router import (
    parallel_tasks_breakage_router,
)
from yuantus.meta_engine.web.parallel_tasks_cad_3d_router import (
    parallel_tasks_cad_3d_router,
)
from yuantus.meta_engine.web.parallel_tasks_consumption_router import (
    parallel_tasks_consumption_router,
)
from yuantus.meta_engine.web.date_obsolete_ops_router import (
    date_obsolete_ops_router,
)
from yuantus.meta_engine.web.lifecycle_transition_history_router import (
    lifecycle_transition_history_router,
)
from yuantus.meta_engine.web.parallel_tasks_doc_sync_router import (
    parallel_tasks_doc_sync_router,
)
from yuantus.meta_engine.web.parallel_tasks_eco_activities_router import (
    parallel_tasks_eco_activities_router,
)
from yuantus.meta_engine.web.parallel_tasks_ops_router import parallel_tasks_ops_router
from yuantus.meta_engine.web.parallel_tasks_workflow_actions_router import (
    parallel_tasks_workflow_actions_router,
)
from yuantus.meta_engine.web.parallel_tasks_workorder_docs_router import (
    parallel_tasks_workorder_docs_router,
)
from yuantus.plugin_manager.runtime import load_plugins
from yuantus.security.auth.database import init_identity_db


def _run_startup(app: FastAPI) -> None:
    # Dev convenience: auto-create tables for the migrated Meta Engine kernel.
    # Production environments should use migrations instead.
    settings = get_settings()
    if settings.ENVIRONMENT == "dev":
        init_db(create_tables=True)
        init_identity_db(create_tables=True)
    from yuantus.meta_engine.services.search_indexer import (
        register_search_index_handlers,
    )

    register_search_index_handlers()
    load_plugins(app)


def _run_shutdown(app: FastAPI) -> None:
    manager = getattr(app.state, "plugin_manager", None)
    if manager and hasattr(manager, "shutdown"):
        manager.shutdown()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _run_startup(app)
    try:
        yield
    finally:
        _run_shutdown(app)


def create_app() -> FastAPI:
    app = FastAPI(title="YuantusPLM", version=__version__, lifespan=_lifespan)
    settings = get_settings()
    origins = [
        origin.strip()
        for origin in (settings.CAD_PREVIEW_CORS_ORIGINS or "").split(",")
        if origin.strip()
    ]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=True,
        )
    app.add_middleware(AuditLogMiddleware)
    app.add_middleware(TenantOrgContextMiddleware)
    app.add_middleware(AuthEnforcementMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(favicon_router)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(metrics_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(cad_preview_router, prefix="/api/v1")
    app.include_router(integrations_router, prefix="/api/v1")
    # PLM-COLLAB-P2.5 (Integration Handshake): advisory integration capability manifest.
    # Same /integrations namespace; separate router (no async-probe deps). Ungated.
    app.include_router(integration_capabilities_router, prefix="/api/v1")
    app.include_router(plugins_router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    app.include_router(plm_workspace_router, prefix="/api/v1")
    app.include_router(workbench_router, prefix="/api/v1")
    app.include_router(meta_router, prefix="/api/v1")
    app.include_router(query_router, prefix="/api/v1")
    app.include_router(rpc_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(app_router, prefix="/api/v1")
    app.include_router(store_router, prefix="/api/v1")
    app.include_router(bom_compare_router, prefix="/api/v1")
    app.include_router(bom_tree_router, prefix="/api/v1")
    # PLM-COLLAB-P3-A: BOM multi-table governed read-only projection (auth->entitled->part->perm).
    app.include_router(bom_multitable_router, prefix="/api/v1")
    app.include_router(bom_children_router, prefix="/api/v1")
    app.include_router(bom_obsolete_rollup_router, prefix="/api/v1")
    app.include_router(bom_where_used_router, prefix="/api/v1")
    app.include_router(bom_substitutes_router, prefix="/api/v1")
    app.include_router(box_core_router, prefix="/api/v1")
    app.include_router(box_analytics_router, prefix="/api/v1")
    app.include_router(box_ops_router, prefix="/api/v1")
    app.include_router(box_reconciliation_router, prefix="/api/v1")
    app.include_router(box_capacity_router, prefix="/api/v1")
    app.include_router(box_policy_router, prefix="/api/v1")
    app.include_router(box_traceability_router, prefix="/api/v1")
    app.include_router(box_custody_router, prefix="/api/v1")
    app.include_router(box_turnover_router, prefix="/api/v1")
    app.include_router(box_aging_router, prefix="/api/v1")
    app.include_router(approval_category_router, prefix="/api/v1")
    app.include_router(approval_request_router, prefix="/api/v1")
    app.include_router(approval_ops_router, prefix="/api/v1")
    app.include_router(equivalent_router, prefix="/api/v1")
    app.include_router(spare_router, prefix="/api/v1")
    app.include_router(effectivity_router, prefix="/api/v1")
    app.include_router(baseline_router, prefix="/api/v1")
    app.include_router(config_router, prefix="/api/v1")
    app.include_router(change_router, prefix="/api/v1")  # LEGACY compat shim — sunset 2026-07-01
    app.include_router(cutted_parts_analytics_router, prefix="/api/v1")
    app.include_router(cutted_parts_utilization_router, prefix="/api/v1")
    app.include_router(cutted_parts_scenarios_router, prefix="/api/v1")
    app.include_router(cutted_parts_benchmark_router, prefix="/api/v1")
    app.include_router(cutted_parts_variance_router, prefix="/api/v1")
    app.include_router(cutted_parts_thresholds_router, prefix="/api/v1")
    app.include_router(cutted_parts_alerts_router, prefix="/api/v1")
    app.include_router(cutted_parts_throughput_router, prefix="/api/v1")
    app.include_router(cutted_parts_bottlenecks_router, prefix="/api/v1")
    app.include_router(cutted_parts_core_router, prefix="/api/v1")
    app.include_router(release_validation_router, prefix="/api/v1")
    app.include_router(dedup_router, prefix="/api/v1")
    app.include_router(cad_backend_profile_router, prefix="/api/v1")
    app.include_router(cad_checkin_router, prefix="/api/v1")
    app.include_router(cad_connectors_router, prefix="/api/v1")
    app.include_router(cad_consistency_router, prefix="/api/v1")
    app.include_router(cad_diff_router, prefix="/api/v1")
    app.include_router(cad_file_data_router, prefix="/api/v1")
    app.include_router(cad_history_router, prefix="/api/v1")
    app.include_router(cad_import_router, prefix="/api/v1")
    app.include_router(cad_mesh_stats_router, prefix="/api/v1")
    app.include_router(cad_properties_router, prefix="/api/v1")
    app.include_router(cad_review_router, prefix="/api/v1")
    app.include_router(cad_sync_template_router, prefix="/api/v1")
    app.include_router(cad_view_state_router, prefix="/api/v1")
    app.include_router(cad_router, prefix="/api/v1")
    app.include_router(document_sync_analytics_router, prefix="/api/v1")
    app.include_router(document_sync_reconciliation_router, prefix="/api/v1")
    app.include_router(document_sync_replay_audit_router, prefix="/api/v1")
    app.include_router(document_sync_drift_router, prefix="/api/v1")
    app.include_router(document_sync_lineage_router, prefix="/api/v1")
    app.include_router(document_sync_retention_router, prefix="/api/v1")
    app.include_router(document_sync_freshness_router, prefix="/api/v1")
    app.include_router(document_sync_core_router, prefix="/api/v1")
    app.include_router(product_router, prefix="/api/v1")
    app.include_router(pdm_assembly_promotion_router, prefix="/api/v1")
    app.include_router(pdm_relationship_router, prefix="/api/v1")
    app.include_router(permission_router, prefix="/api/v1")
    app.include_router(schema_router, prefix="/api/v1")
    app.include_router(impact_router, prefix="/api/v1")
    app.include_router(item_cockpit_router, prefix="/api/v1")
    app.include_router(locale_router, prefix="/api/v1")
    app.include_router(release_readiness_router, prefix="/api/v1")
    app.include_router(release_orchestration_router, prefix="/api/v1")
    app.include_router(publication_readiness_router, prefix="/api/v1")
    app.include_router(publication_outbox_router, prefix="/api/v1")
    app.include_router(ecm_publication_outbox_router, prefix="/api/v1")
    app.include_router(ui_router, prefix="/api/v1")
    app.include_router(file_conversion_router, prefix="/api/v1")
    app.include_router(file_viewer_router, prefix="/api/v1")
    app.include_router(file_storage_router, prefix="/api/v1")
    app.include_router(file_attachment_router, prefix="/api/v1")
    app.include_router(file_metadata_router, prefix="/api/v1")
    app.include_router(esign_router, prefix="/api/v1")
    app.include_router(version_revision_router, prefix="/api/v1")
    app.include_router(version_iteration_router, prefix="/api/v1")
    app.include_router(version_file_router, prefix="/api/v1")
    app.include_router(version_lifecycle_router, prefix="/api/v1")
    app.include_router(version_effectivity_router, prefix="/api/v1")
    app.include_router(mbom_router, prefix="/api/v1")
    app.include_router(routing_router, prefix="/api/v1")
    app.include_router(workcenter_router, prefix="/api/v1")
    app.include_router(report_saved_search_router, prefix="/api/v1")
    app.include_router(report_summary_search_router, prefix="/api/v1")
    app.include_router(report_definition_router, prefix="/api/v1")
    app.include_router(report_dashboard_router, prefix="/api/v1")
    app.include_router(eco_approval_ops_router, prefix="/api/v1")
    app.include_router(eco_stage_router, prefix="/api/v1")
    app.include_router(eco_approval_workflow_router, prefix="/api/v1")
    app.include_router(eco_impact_apply_router, prefix="/api/v1")
    app.include_router(eco_change_analysis_router, prefix="/api/v1")
    app.include_router(eco_lifecycle_router, prefix="/api/v1")
    app.include_router(eco_core_router, prefix="/api/v1")
    app.include_router(parallel_tasks_breakage_router, prefix="/api/v1")
    app.include_router(parallel_tasks_cad_3d_router, prefix="/api/v1")
    app.include_router(parallel_tasks_consumption_router, prefix="/api/v1")
    app.include_router(date_obsolete_ops_router, prefix="/api/v1")
    app.include_router(lifecycle_transition_history_router, prefix="/api/v1")
    app.include_router(parallel_tasks_doc_sync_router, prefix="/api/v1")
    app.include_router(parallel_tasks_eco_activities_router, prefix="/api/v1")
    app.include_router(parallel_tasks_ops_router, prefix="/api/v1")
    app.include_router(parallel_tasks_workflow_actions_router, prefix="/api/v1")
    app.include_router(parallel_tasks_workorder_docs_router, prefix="/api/v1")
    app.include_router(maintenance_category_router, prefix="/api/v1")
    app.include_router(maintenance_equipment_router, prefix="/api/v1")
    app.include_router(maintenance_request_router, prefix="/api/v1")
    app.include_router(maintenance_schedule_router, prefix="/api/v1")
    app.include_router(quality_points_router, prefix="/api/v1")
    app.include_router(quality_checks_router, prefix="/api/v1")
    app.include_router(quality_alerts_router, prefix="/api/v1")
    app.include_router(quality_analytics_router, prefix="/api/v1")
    app.include_router(subcontracting_orders_router, prefix="/api/v1")
    app.include_router(subcontracting_analytics_router, prefix="/api/v1")
    app.include_router(subcontracting_approval_mapping_router, prefix="/api/v1")

    # PLM-COLLAB-P1-D: feature-entitlement affordance (status + mock-activate).
    # Unconditional -- base PLM also surfaces the upgrade affordance; entitlement
    # itself is decided only by EntitlementService.is_entitled.
    app.include_router(feature_router, prefix="/api/v1")
    app.include_router(license_status_router, prefix="/api/v1")
    app.include_router(license_cap_change_history_router, prefix="/api/v1")
    app.include_router(license_revoke_router, prefix="/api/v1")

    # PLM-COLLAB-P2-B: approval-automation product entry (template catalog + draft
    # provisioning). Unconditional -- the GET is the upgrade-affordance surface; the
    # POST is gated by EntitlementService.is_entitled("approval_automation").
    app.include_router(approval_automation_router, prefix="/api/v1")

    # PLM-COLLAB-P2-C: ECO approval-automation governed projection + notify action.
    # Read-only projection + stub notify; GET auth->entitled->ECO, POST admin->entitled.
    app.include_router(approval_automation_eco_router, prefix="/api/v1")

    # PLM-COLLAB-P2-D: ECO-scenario capability/upgrade entry (ungated affordance).
    # Consumes is_entitled only; no ECO lookup, no write.
    app.include_router(approval_automation_capabilities_router, prefix="/api/v1")

    # PLM-COLLAB-P0-A: MetaSheet collaboration bridge seam.
    # Absent by default so the base PLM SKU keeps its exact route surface
    # (the flag-OFF route-count pin stays at 691). When ENABLE_METASHEET is true
    # the seam mounts but stays inert until a later phase wires per-tenant
    # entitlement. The flag is read ONLY here, at the registration boundary
    # (canonical plan 铁律 4) — no MetaSheet concern leaks into PLM core logic.
    if settings.ENABLE_METASHEET:
        from yuantus.api.routers.metasheet_bridge import (
            router as metasheet_bridge_router,
        )

        app.include_router(metasheet_bridge_router, prefix="/api/v1")

    return app


app = create_app()
