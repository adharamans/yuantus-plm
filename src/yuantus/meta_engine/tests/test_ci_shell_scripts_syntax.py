from __future__ import annotations

import subprocess
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(12):
        if (cur / "pyproject.toml").is_file() and (cur / "scripts").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise AssertionError("Could not locate repo root (expected pyproject.toml + scripts/)")


def _bash_n(script: Path) -> None:
    cp = subprocess.run(  # noqa: S603,S607
        ["bash", "-n", str(script)],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr


def test_ci_and_ops_shell_scripts_are_syntax_valid() -> None:
    repo_root = _find_repo_root(Path(__file__))
    scripts_dir = repo_root / "scripts"

    # Keep this list small and focused on scripts used by CI workflows or ops runbooks.
    paths = [
        scripts_dir / "ci_change_scope_debug.sh",
        scripts_dir / "strict_gate.sh",
        scripts_dir / "strict_gate_report.sh",
        scripts_dir / "run_playwright_strict_gate.sh",
        scripts_dir / "strict_gate_perf_download_and_trend.sh",
        scripts_dir / "strict_gate_recent_perf_audit_regression.sh",
        scripts_dir / "demo_plm_closed_loop.sh",
        scripts_dir / "release_orchestration.sh",
        scripts_dir / "mt_pg_bootstrap.sh",
        scripts_dir / "verify_all.sh",
        scripts_dir / "verify_compose_sku_profiles.sh",
        scripts_dir / "verify_compose_sku_profiles_smoke.sh",
        scripts_dir / "sync_metasheet2_pact.sh",
        scripts_dir / "verify_release_orchestration.sh",
        scripts_dir / "verify_release_orchestration_perf_smoke.sh",
        scripts_dir / "verify_esign_api.sh",
        scripts_dir / "verify_esign_perf_smoke.sh",
        scripts_dir / "verify_reports_perf_smoke.sh",
        scripts_dir / "verify_identity_only_migrations.sh",
        scripts_dir / "verify_dedup_management.sh",
        scripts_dir / "verify_quota_enforcement.sh",
        scripts_dir / "verify_platform_tenant_provisioning.sh",
        scripts_dir / "verify_odoo18_plm_stack.sh",
        scripts_dir / "verify_item_equivalents.sh",
        scripts_dir / "verify_version_file_binding.sh",
        scripts_dir / "verify_versions_e2e.sh",
        scripts_dir / "verify_eco_advanced_e2e.sh",
        scripts_dir / "verify_where_used_e2e.sh",
        scripts_dir / "verify_effectivity_extended_e2e.sh",
        scripts_dir / "verify_bom_obsolete_e2e.sh",
        scripts_dir / "verify_bom_weight_rollup_e2e.sh",
        scripts_dir / "verify_bom_compare_e2e.sh",
        scripts_dir / "verify_bom_effectivity_e2e.sh",
        scripts_dir / "verify_bom_tree_e2e.sh",
        scripts_dir / "verify_bom_substitutes_e2e.sh",
        scripts_dir / "verify_mbom_convert_e2e.sh",
        scripts_dir / "verify_mbom_routing_e2e.sh",
        scripts_dir / "verify_routing_primary_release_e2e.sh",
        scripts_dir / "verify_routing_operations_e2e.sh",
        scripts_dir / "verify_routing_copy_e2e.sh",
        scripts_dir / "verify_baseline_e2e.sh",
        scripts_dir / "verify_baseline_filters_e2e.sh",
        scripts_dir / "verify_workcenter_e2e.sh",
        scripts_dir / "verify_cad_backend_profile_scope.sh",
        scripts_dir / "verify_cad_dedup_vision_s3.sh",
        scripts_dir / "verify_cad_dedup_relationship_s3.sh",
        scripts_dir / "verify_cad_ml_quick.sh",
        scripts_dir / "verify_cad_preview_online.sh",
        scripts_dir / "generate_tenant_import_rehearsal_env_template.sh",
        scripts_dir / "verify_playwright_product_ui_summaries.sh",
        scripts_dir / "list_native_workspace_bundle.sh",
        scripts_dir / "verify_playwright_plm_workspace_all.sh",
        scripts_dir / "verify_playwright_plm_workspace_documents_ui.sh",
        scripts_dir / "verify_playwright_plm_workspace_demo_resume.sh",
        scripts_dir / "verify_playwright_plm_workspace_document_handoff.sh",
        scripts_dir / "verify_playwright_plm_workspace_eco_actions.sh",
        scripts_dir / "print_claude_code_parallel_commands.sh",
        scripts_dir / "print_current_worktree_closeout_commands.sh",
        scripts_dir / "print_cross_domain_services_split_helper.sh",
        scripts_dir / "print_delivery_pack_split_helper.sh",
        scripts_dir / "print_dirty_tree_domain_commands.sh",
        scripts_dir / "print_dirty_tree_domain_coverage.sh",
        scripts_dir / "print_dirty_tree_split_matrix.sh",
        scripts_dir / "print_docs_parallel_split_helper.sh",
        scripts_dir / "print_mainline_baseline_switch_commands.sh",
        scripts_dir / "print_p2_shared_dev_142_daily_ops_commands.sh",
        scripts_dir / "print_p2_shared_dev_142_drift_audit_commands.sh",
        scripts_dir / "print_p2_shared_dev_142_drift_investigation_commands.sh",
        scripts_dir / "print_p2_shared_dev_142_refreeze_candidate_commands.sh",
        scripts_dir / "print_p2_shared_dev_142_refreeze_proposal_commands.sh",
        scripts_dir / "print_p2_shared_dev_142_refreeze_readiness_commands.sh",
        scripts_dir / "print_p2_shared_dev_142_rerun_commands.sh",
        scripts_dir / "print_p2_shared_dev_bootstrap_commands.sh",
        scripts_dir / "print_p2_shared_dev_142_readonly_rerun_commands.sh",
        scripts_dir / "print_p2_shared_dev_first_run_commands.sh",
        scripts_dir / "print_p2_shared_dev_mode_selection.sh",
        scripts_dir / "precheck_tenant_import_rehearsal_env_file.sh",
        scripts_dir / "precheck_tenant_import_rehearsal_evidence.sh",
        scripts_dir / "precheck_tenant_import_rehearsal_operator.sh",
        scripts_dir / "prepare_tenant_import_rehearsal_operator_commands.sh",
        scripts_dir / "precheck_p2_observation_regression.sh",
        scripts_dir / "print_strict_gate_split_helper.sh",
        scripts_dir / "print_tenant_import_rehearsal_commands.sh",
        scripts_dir / "print_subcontracting_first_cut_anchors.sh",
        scripts_dir / "run_claude_code_parallel_reviewer.sh",
        scripts_dir / "run_p2_shared_dev_142_entrypoint.sh",
        scripts_dir / "run_p2_shared_dev_142_drift_audit.sh",
        scripts_dir / "run_p2_shared_dev_142_drift_investigation.sh",
        scripts_dir / "run_p2_shared_dev_142_refreeze_candidate.sh",
        scripts_dir / "run_p2_shared_dev_142_refreeze_proposal.sh",
        scripts_dir / "run_p2_shared_dev_142_refreeze_readiness.sh",
        scripts_dir / "run_p2_shared_dev_142_readonly_rerun.sh",
        scripts_dir / "run_p2_shared_dev_142_workflow_probe.sh",
        scripts_dir / "run_p2_shared_dev_142_workflow_readonly_check.sh",
        scripts_dir / "run_p2_observation_regression.sh",
        scripts_dir / "run_p2_observation_regression_workflow.sh",
        scripts_dir / "run_scheduler_audit_retention_activation_smoke.sh",
        scripts_dir / "run_scheduler_bom_to_mbom_activation_smoke.sh",
        scripts_dir / "run_scheduler_dry_run_preflight.sh",
        scripts_dir / "run_scheduler_eco_escalation_activation_smoke.sh",
        scripts_dir / "run_scheduler_jobs_api_readback_smoke.sh",
        scripts_dir / "run_scheduler_local_activation_suite.sh",
        scripts_dir / "run_tenant_import_evidence_closeout.sh",
        scripts_dir / "run_tenant_import_operator_launchpack.sh",
        scripts_dir / "run_tenant_import_rehearsal_full_closeout.sh",
        scripts_dir / "run_tenant_import_rehearsal_operator_sequence.sh",
        scripts_dir / "validate_tenant_import_rehearsal_operator_commands.sh",
        scripts_dir / "verify_run_h_e2e.sh",
        scripts_dir / "verify_run_h.sh",
    ]

    for p in paths:
        assert p.is_file(), f"Missing script: {p}"
        _bash_n(p)


def test_release_orchestration_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "release_orchestration.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    assert "Usage:" in (cp.stdout or "")
    assert "release_orchestration.sh plan" in (cp.stdout or "")
    assert "release_orchestration.sh execute" in (cp.stdout or "")


def test_p2_observation_scripts_help_uses_repo_safe_env_file_examples() -> None:
    repo_root = _find_repo_root(Path(__file__))
    for name in (
        "precheck_p2_observation_regression.sh",
        "run_p2_shared_dev_142_readonly_rerun.sh",
        "run_p2_observation_regression.sh",
    ):
        script = repo_root / "scripts" / name
        assert script.is_file(), f"Missing script: {script}"

        cp = subprocess.run(  # noqa: S603,S607
            ["bash", str(script), "--help"],
            text=True,
            capture_output=True,
        )
        assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
        out = cp.stdout or ""
        assert "$HOME/.config/yuantus/" in out, f"{name} help should keep env-file examples outside repo root"
        assert "./p2-shared-dev.env" not in out, f"{name} help should not point shared-dev credentials at repo root"


def test_p2_shared_dev_142_workflow_probe_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "run_p2_shared_dev_142_workflow_probe.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    for token in (
        "Usage:",
        "http://142.171.239.56:7910",
        "shared-dev-142-workflow-probe",
        "current-only workflow probe",
        "run_p2_shared_dev_142_workflow_readonly_check.sh",
        "run_p2_shared_dev_142_readonly_rerun.sh",
    ):
        assert token in out, f"run_p2_shared_dev_142_workflow_probe.sh help missing token: {token}"


def test_p2_shared_dev_142_entrypoint_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "run_p2_shared_dev_142_entrypoint.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    for token in (
        "Usage:",
        "--mode <mode>",
        "print-daily-commands",
        "readonly-rerun",
        "refreeze-readiness",
        "refreeze-candidate",
        "refreeze-proposal",
        "drift-audit",
        "drift-investigation",
        "workflow-probe",
        "workflow-readonly-check",
        "print-readonly-commands",
        "print-refreeze-readiness-commands",
        "print-refreeze-candidate-commands",
        "print-refreeze-proposal-commands",
        "print-drift-commands",
        "print-investigation-commands",
        "--dry-run",
        "`142.171.239.56`",
    ):
        assert token in out, f"run_p2_shared_dev_142_entrypoint.sh help missing token: {token}"


def test_p2_shared_dev_142_drift_audit_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "run_p2_shared_dev_142_drift_audit.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    for token in (
        "Usage:",
        "./tmp/p2-shared-dev-observation-20260421-stable",
        "shared-dev-142-readonly-20260421",
        "current-drift-audit",
        "DRIFT_AUDIT.md",
        "drift_audit.json",
        "run_p2_shared_dev_142_readonly_rerun.sh",
    ):
        assert token in out, f"run_p2_shared_dev_142_drift_audit.sh help missing token: {token}"


def test_p2_shared_dev_142_drift_investigation_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "run_p2_shared_dev_142_drift_investigation.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    for token in (
        "Usage:",
        "./tmp/p2-shared-dev-observation-20260421-stable",
        "shared-dev-142-readonly-20260421",
        "current-drift-audit",
        "DRIFT_INVESTIGATION.md",
        "drift_investigation.json",
        "run_p2_shared_dev_142_drift_audit.sh",
    ):
        assert token in out, f"run_p2_shared_dev_142_drift_investigation.sh help missing token: {token}"


def test_p2_shared_dev_142_refreeze_readiness_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "run_p2_shared_dev_142_refreeze_readiness.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    for token in (
        "Usage:",
        "REFREEZE_READINESS.md",
        "refreeze_readiness.json",
        "future-deadline pending approvals",
        "run_p2_shared_dev_142_readonly_rerun.sh",
    ):
        assert token in out, f"run_p2_shared_dev_142_refreeze_readiness.sh help missing token: {token}"


def test_p2_shared_dev_142_refreeze_candidate_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "run_p2_shared_dev_142_refreeze_candidate.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    for token in (
        "Usage:",
        "STABLE_READONLY_CANDIDATE.md",
        "stable_readonly_candidate.json",
        "excluding future-deadline pending approvals",
        "run_p2_shared_dev_142_readonly_rerun.sh",
    ):
        assert token in out, f"run_p2_shared_dev_142_refreeze_candidate.sh help missing token: {token}"


def test_p2_shared_dev_142_refreeze_proposal_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "run_p2_shared_dev_142_refreeze_proposal.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    for token in (
        "Usage:",
        "REFREEZE_PROPOSAL.md",
        "refreeze_proposal.json",
        "shared-dev-142-readonly-",
        "run_p2_shared_dev_142_refreeze_candidate.sh",
    ):
        assert token in out, f"run_p2_shared_dev_142_refreeze_proposal.sh help missing token: {token}"


def test_p2_shared_dev_142_workflow_readonly_check_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "run_p2_shared_dev_142_workflow_readonly_check.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    for token in (
        "Usage:",
        "./tmp/p2-shared-dev-observation-20260421-stable",
        "shared-dev-142-readonly-20260421",
        "workflow-probe-current",
        "--base-url",
        "http://142.171.239.56:7910",
        "WORKFLOW_READONLY_DIFF.md",
        "WORKFLOW_READONLY_EVAL.md",
        "run_p2_shared_dev_142_readonly_rerun.sh",
    ):
        assert token in out, f"run_p2_shared_dev_142_workflow_readonly_check.sh help missing token: {token}"


def test_strict_gate_report_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "strict_gate_report.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    assert "Usage:" in out
    assert "strict_gate_report.sh" in out
    assert "OUT_DIR" in out
    assert "REPORT_PATH" in out
    assert "Default: STRICT_GATE_<timestamp>_<pid>" in out
    assert "Default: docs/DAILY_REPORTS/<run_id>.md" in out
    assert "PLAYWRIGHT_KEEP_DB" in out
    assert "PLAYWRIGHT_RETRYABLE_PATTERN" in out


def test_strict_gate_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "strict_gate.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    assert "Usage:" in out
    assert "strict_gate.sh" in out
    assert "PLAYWRIGHT_RUNNER" in out
    assert "PLAYWRIGHT_MAX_ATTEMPTS" in out
    assert "PLAYWRIGHT_KEEP_DB" in out
    assert "PLAYWRIGHT_RETRYABLE_PATTERN" in out
    assert "PLAYWRIGHT_PORT_PICKER_CMD" in out


def test_run_playwright_strict_gate_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "run_playwright_strict_gate.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    assert "Usage:" in out
    assert "run_playwright_strict_gate.sh" in out
    assert "PLAYWRIGHT_PORT_PICKER_CMD" in out
    assert "PLAYWRIGHT_MAX_ATTEMPTS" in out
    assert "PLAYWRIGHT_RETRYABLE_PATTERN" in out


def test_strict_gate_perf_download_and_trend_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "strict_gate_perf_download_and_trend.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    assert "Usage:" in out
    assert "strict_gate_perf_download_and_trend.sh" in out
    assert "--limit" in out
    assert "--run-id" in out
    assert "--conclusion" in out
    assert "--max-run-age-days" in out
    assert "--artifact-name" in out
    assert "--download-retries" in out
    assert "--download-retry-delay-sec" in out
    assert "--clean-download-dir" in out
    assert "--fail-if-no-runs" in out
    assert "--fail-if-no-metrics" in out
    assert "--fail-if-skipped" in out
    assert "--fail-if-none-downloaded" in out
    assert "--json-out" in out
    assert "--trend-out" in out


def test_strict_gate_recent_perf_audit_regression_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "strict_gate_recent_perf_audit_regression.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    assert "Usage:" in out
    assert "strict_gate_recent_perf_audit_regression.sh" in out
    assert "--workflow" in out
    assert "--ref" in out
    assert "--repo" in out
    assert "--poll-interval-sec" in out
    assert "--max-wait-sec" in out
    assert "--success-limit" in out
    assert "--success-max-run-age-days" in out
    assert "--success-conclusion" in out
    assert "--summary-json" in out
    assert "--out-dir" in out


def test_p2_observation_regression_workflow_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "run_p2_observation_regression_workflow.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    assert "Usage:" in out
    assert "run_p2_observation_regression_workflow.sh" in out
    assert "--base-url" in out
    assert "--workflow" in out
    assert "--artifact-name" in out
    assert "--poll-interval-sec" in out
    assert "--max-discovery-sec" in out
    assert "WORKFLOW_DISPATCH_RESULT.md" in out
    assert "workflow_dispatch.json" in out


def test_p2_observation_precheck_script_has_help() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "precheck_p2_observation_regression.sh"
    assert script.is_file(), f"Missing script: {script}"

    cp = subprocess.run(  # noqa: S603,S607
        ["bash", str(script), "--help"],
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stdout + "\n" + cp.stderr
    out = cp.stdout or ""
    assert "Usage:" in out
    assert "precheck_p2_observation_regression.sh" in out
    assert "--env-file" in out
    assert "OBSERVATION_PRECHECK.md" in out
    assert "observation_precheck.json" in out
