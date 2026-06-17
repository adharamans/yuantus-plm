from __future__ import annotations

import os
from pathlib import Path

import pytest


_ALLOWLIST_NO_DB = {
    "src/yuantus/meta_engine/tests/test_a3_workstation_checkout_context.py",
    "src/yuantus/meta_engine/tests/test_config_variants.py",
    "src/yuantus/meta_engine/tests/test_cad_preview_min_size.py",
    "src/yuantus/meta_engine/tests/test_cad_render_diff_service.py",
    "src/yuantus/meta_engine/tests/test_cad_2d3d_staleness.py",
    "src/yuantus/meta_engine/tests/test_cad_stale_drawings.py",
    "src/yuantus/meta_engine/tests/test_manufacturing_mbom_routing.py",
    "src/yuantus/meta_engine/tests/test_ir_rule_adapter.py",
    "src/yuantus/meta_engine/tests/test_assembly_promotion_service.py",
    "src/yuantus/meta_engine/tests/test_baseline_release_diagnostics.py",
    "src/yuantus/meta_engine/tests/test_item_release_gate.py",
    "src/yuantus/meta_engine/tests/test_item_number_immutable.py",
    "src/yuantus/meta_engine/tests/test_latest_released_guard.py",
    "src/yuantus/meta_engine/tests/test_latest_released_guard_router.py",
    "src/yuantus/meta_engine/tests/test_latest_released_write_paths.py",
    "src/yuantus/meta_engine/tests/test_version_supersede_b1.py",
    "src/yuantus/meta_engine/tests/test_version_supersede_read_surface.py",
    "src/yuantus/meta_engine/tests/test_release_hook_point_hardening.py",
    "src/yuantus/meta_engine/tests/test_release_validation_directory.py",
    "src/yuantus/meta_engine/tests/test_eco_apply_diagnostics.py",
    "src/yuantus/meta_engine/tests/test_release_readiness_router.py",
    "src/yuantus/meta_engine/tests/test_release_readiness_item_release.py",
    "src/yuantus/meta_engine/tests/test_impact_export_bundles.py",
    "src/yuantus/meta_engine/tests/test_release_readiness_export_bundles.py",
    "src/yuantus/meta_engine/tests/test_item_cockpit_router.py",
    "src/yuantus/meta_engine/tests/test_product_detail_cockpit_extensions.py",
    "src/yuantus/meta_engine/tests/test_release_orchestration_router.py",
    "src/yuantus/meta_engine/tests/test_plm_erp_publication_router.py",
    "src/yuantus/meta_engine/tests/test_erp_publication_outbox_service.py",
    "src/yuantus/meta_engine/tests/test_erp_http_adapter.py",
    "src/yuantus/meta_engine/tests/test_plm_erp_publication_outbox_router.py",
    "src/yuantus/meta_engine/tests/test_plm_erp_publication_outbox_worker.py",
    "src/yuantus/meta_engine/tests/test_ecm_publication_enqueue.py",
    "src/yuantus/meta_engine/tests/test_ecm_publication_adapter.py",
    "src/yuantus/meta_engine/tests/test_ecm_publication_worker.py",
    "src/yuantus/meta_engine/tests/test_ecm_publication_worker_cli.py",
    "src/yuantus/meta_engine/tests/test_ecm_cmis_adapter.py",
    "src/yuantus/meta_engine/tests/test_ecm_transfer_receiver_adapter.py",
    "src/yuantus/meta_engine/tests/test_ecm_transfer_receiver_phase0_smoke.py",
    "src/yuantus/meta_engine/tests/test_ecm_worker_e2e_smoke.py",
    "src/yuantus/meta_engine/tests/test_plm_ecm_publication_outbox_router.py",
    "src/yuantus/meta_engine/tests/test_consumption_mes_ingestion_contract.py",
    "src/yuantus/meta_engine/tests/test_consumption_mes_ingestion_runtime.py",
    "src/yuantus/meta_engine/tests/test_parallel_tasks_services.py",
    "src/yuantus/meta_engine/tests/test_pdm_relationship_traversal.py",
    "src/yuantus/meta_engine/tests/test_pdm_relationship_types.py",
    "src/yuantus/meta_engine/tests/test_bom_delta_preview.py",
    "src/yuantus/meta_engine/tests/test_eco_parallel_flow_hooks.py",
    "src/yuantus/meta_engine/tests/test_parallel_tasks_router.py",
    "src/yuantus/meta_engine/tests/test_spare_router.py",
    "src/yuantus/meta_engine/tests/test_spare_router_contracts.py",
    "src/yuantus/meta_engine/tests/test_search_released_only_filter.py",
}

_DB_DEPENDENT_PATHS = (
    "src/yuantus/meta_engine/operations/tests",
    "src/yuantus/meta_engine/workflow",
    "src/yuantus/meta_engine/web/test_client.py",
)


def _db_enabled() -> bool:
    flag = os.getenv("YUANTUS_PYTEST_DB") or os.getenv("YUANTUS_TEST_DB") or os.getenv("PYTEST_DB")
    if flag:
        return flag.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _is_allowlisted(path_str: str) -> bool:
    return any(path_str.endswith(allowed) for allowed in _ALLOWLIST_NO_DB)


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool:  # type: ignore[override]
    if _db_enabled():
        return False

    path_str = collection_path.as_posix()
    if _is_allowlisted(path_str):
        return False

    # Allow traversal into the tests directory so allowlisted files can be collected.
    if path_str.endswith("src/yuantus/meta_engine/tests"):
        return False

    if "src/yuantus/meta_engine/tests" in path_str and path_str.endswith(".py"):
        return True

    return any(marker in path_str for marker in _DB_DEPENDENT_PATHS)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_db: marks tests that need a configured database (enable with YUANTUS_PYTEST_DB=1)",
    )
