from __future__ import annotations

from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(12):
        if (cur / "pyproject.toml").is_file() and (cur / "docs").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise AssertionError("Could not locate repo root (expected pyproject.toml + docs/)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_p2_observation_docs_are_discoverable_from_readme_runbooks() -> None:
    repo_root = _find_repo_root(Path(__file__))
    readme = repo_root / "README.md"
    assert readme.is_file(), f"Missing {readme}"

    text = _read(readme)
    for token in (
        "docs/P2_OBSERVATION_REGRESSION_ONE_COMMAND.md",
        "docs/P2_OBSERVATION_REGRESSION_WORKFLOW_DISPATCH.md",
        "docs/P2_ONE_PAGE_DEV_GUIDE.md",
        "docs/P2_REMOTE_OBSERVATION_REGRESSION_RUNBOOK.md",
        "docs/P2_SHARED_DEV_142_DAILY_OPS_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_DRIFT_AUDIT_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_DRIFT_INVESTIGATION_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_READONLY_REFREEZE_CANDIDATE_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_READONLY_REFREEZE_PROPOSAL_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_READONLY_REFREEZE_READINESS_CHECKLIST.md",
        "docs/P2_SHARED_DEV_FIRST_RUN_CHECKLIST.md",
        "docs/P2_SHARED_DEV_OBSERVATION_HANDOFF.md",
        "docs/P2_SHARED_DEV_142_RERUN_CHECKLIST.md",
    ):
        assert token in text, f"README.md missing P2 observation runbook token: {token}"


def test_p2_shared_dev_142_entrypoint_is_discoverable_from_readme_top_level_shared_dev_section() -> None:
    repo_root = _find_repo_root(Path(__file__))
    readme = repo_root / "README.md"
    assert readme.is_file(), f"Missing {readme}"

    text = _read(readme)
    for token in (
        "docs/P2_ONE_PAGE_DEV_GUIDE.md",
        "docs/P2_SHARED_DEV_OBSERVATION_HANDOFF.md",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --help",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode print-daily-commands",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode print-readonly-commands",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode print-refreeze-candidate-commands",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode print-refreeze-proposal-commands",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode print-refreeze-readiness-commands",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode readonly-rerun",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode refreeze-candidate",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode refreeze-proposal",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode refreeze-readiness",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode drift-audit",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode drift-investigation",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode workflow-probe",
        "bash scripts/run_p2_shared_dev_142_entrypoint.sh --mode workflow-readonly-check",
    ):
        assert token in text, f"README.md missing shared-dev 142 selector token: {token}"


def test_p2_observation_scripts_are_discoverable_from_delivery_scripts_index() -> None:
    repo_root = _find_repo_root(Path(__file__))
    index_path = repo_root / "docs" / "DELIVERY_SCRIPTS_INDEX_20260202.md"
    assert index_path.is_file(), f"Missing {index_path}"

    text = _read(index_path)
    for token in (
        "print_p2_shared_dev_142_daily_ops_commands.sh",
        "print_p2_shared_dev_142_drift_audit_commands.sh",
        "print_p2_shared_dev_142_drift_investigation_commands.sh",
        "print_p2_shared_dev_142_refreeze_candidate_commands.sh",
        "print_p2_shared_dev_142_refreeze_proposal_commands.sh",
        "print_p2_shared_dev_142_refreeze_readiness_commands.sh",
        "print_p2_shared_dev_bootstrap_commands.sh",
        "print_p2_shared_dev_142_readonly_rerun_commands.sh",
        "print_p2_shared_dev_142_rerun_commands.sh",
        "print_p2_shared_dev_first_run_commands.sh",
        "print_p2_shared_dev_mode_selection.sh",
        "run_p2_shared_dev_142_entrypoint.sh",
        "run_p2_shared_dev_142_drift_audit.sh",
        "run_p2_shared_dev_142_drift_investigation.sh",
        "run_p2_shared_dev_142_refreeze_candidate.sh",
        "run_p2_shared_dev_142_refreeze_proposal.sh",
        "run_p2_shared_dev_142_refreeze_readiness.sh",
        "run_p2_shared_dev_142_readonly_rerun.sh",
        "run_p2_shared_dev_142_workflow_probe.sh",
        "run_p2_shared_dev_142_workflow_readonly_check.sh",
        "precheck_p2_observation_regression.sh",
        "verify_p2_dev_observation_startup.sh",
        "run_p2_observation_regression.sh",
        "run_p2_observation_regression_workflow.sh",
        "render_p2_observation_result.py",
        "render_p2_shared_dev_142_refreeze_candidate.py",
        "render_p2_shared_dev_142_refreeze_proposal.py",
        "compare_p2_observation_results.py",
        "evaluate_p2_observation_results.py",
        "`print_p2_shared_dev_142_daily_ops_commands.sh` prints the minimal maintenance-state command sequence for the official shared-dev 142 readonly baseline: readonly-rerun first, then drift-audit, then drift-investigation only if needed.",
        "`print_p2_shared_dev_142_drift_audit_commands.sh` prints the fixed drift-audit command sequence for investigating shared-dev 142 readonly baseline deltas before any refreeze.",
        "`print_p2_shared_dev_142_drift_investigation_commands.sh` prints the fixed drift-investigation command sequence for turning a shared-dev 142 drift-audit result into an evidence pack before any refreeze decision.",
        "`print_p2_shared_dev_142_refreeze_candidate_commands.sh` prints the fixed stable-candidate preview commands for reviewing an overdue-only shared-dev 142 baseline candidate before any tracked refreeze.",
        "`print_p2_shared_dev_142_refreeze_proposal_commands.sh` prints the fixed formal refreeze-proposal commands for turning an accepted stable candidate into a tracked baseline switch proposal pack.",
        "`print_p2_shared_dev_142_refreeze_readiness_commands.sh` prints the fixed readonly refreeze-readiness command sequence for checking whether shared-dev 142 can safely promote a new frozen baseline.",
        "`print_p2_shared_dev_142_rerun_commands.sh` prints the fixed rerun checklist for the already-initialized `142` shared-dev environment.",
        "`print_p2_shared_dev_bootstrap_commands.sh` prints the server-side shared-dev bootstrap and post-bootstrap observation handoff commands.",
        "`print_p2_shared_dev_142_readonly_rerun_commands.sh` prints the fixed readonly rerun commands for the current official shared-dev 142 baseline, including the canonical `BASELINE_DIR`.",
        "`print_p2_shared_dev_first_run_commands.sh` prints the fixed first-run checklist for fresh or explicitly resettable shared-dev environments.",
        "`print_p2_shared_dev_mode_selection.sh` prints the decision gate between existing shared-dev rerun and first-run bootstrap, defaulting unknown environments to rerun.",
        "`run_p2_shared_dev_142_entrypoint.sh` is the single mode selector for shared-dev host `142.171.239.56`, routing to print-daily-commands, readonly-rerun, refreeze-readiness, refreeze-candidate, refreeze-proposal, drift-audit, drift-investigation, workflow-probe, workflow-readonly-check, and the expanded readonly/refreeze/drift/investigation command printouts.",
        "`run_p2_shared_dev_142_drift_audit.sh` runs the fixed readonly rerun into a dedicated current result dir and renders a top-level `DRIFT_AUDIT.md` plus `drift_audit.json`.",
        "`run_p2_shared_dev_142_drift_investigation.sh` runs the fixed drift-audit flow into a nested result dir and renders a top-level `DRIFT_INVESTIGATION.md` plus `drift_investigation.json`.",
        "`run_p2_shared_dev_142_refreeze_candidate.sh` runs the fixed readonly rerun into a nested current result dir, renders a stable candidate preview, and writes a top-level `STABLE_READONLY_CANDIDATE.md` plus `stable_readonly_candidate.json`.",
        "`run_p2_shared_dev_142_refreeze_proposal.sh` runs the fixed stable candidate preview into a nested result dir, renders a formal proposal pack, and writes a top-level `REFREEZE_PROPOSAL.md` plus `refreeze_proposal.json`.",
        "`run_p2_shared_dev_142_refreeze_readiness.sh` runs the fixed readonly rerun into a nested current result dir and renders a top-level `REFREEZE_READINESS.md` plus `refreeze_readiness.json`.",
        "`run_p2_shared_dev_142_readonly_rerun.sh` runs the current official shared-dev 142 readonly rerun end-to-end with fixed baseline defaults, optional baseline restore, precheck, and readonly evaluation.",
        "`run_p2_shared_dev_142_workflow_probe.sh` runs the fixed GitHub workflow-dispatch current-only probe for shared-dev host `142.171.239.56` and downloads the resulting artifact locally.",
        "`run_p2_shared_dev_142_workflow_readonly_check.sh` runs the fixed shared-dev 142 workflow probe, then locally compares the downloaded artifact against the official frozen readonly baseline and writes readonly diff/eval outputs.",
        "`render_p2_shared_dev_142_refreeze_candidate.py` renders an overdue-only stable candidate pack from a current observation result dir, excluding future-deadline pending approvals and producing a candidate artifact bundle for review.",
        "`render_p2_shared_dev_142_refreeze_proposal.py` renders a formal tracked-baseline switch proposal from a green stable candidate preview, materializing a proposed tracked artifact dir without mutating the official baseline.",
        "`precheck_p2_observation_regression.sh` is the cheap local shared-dev readiness probe",
        "`run_p2_observation_regression.sh` is the canonical local/shared-dev wrapper",
        "`run_p2_observation_regression_workflow.sh` is the canonical local wrapper",
        "`print_p2_shared_dev_observation_commands.sh` prints the canonical P2 shared-dev shell and workflow entry commands.",
    ):
        assert token in text, f"DELIVERY_SCRIPTS_INDEX missing P2 observation token: {token}"


def test_p2_observation_handoff_and_runbooks_are_indexed_in_delivery_doc_index() -> None:
    repo_root = _find_repo_root(Path(__file__))
    index_path = repo_root / "docs" / "DELIVERY_DOC_INDEX.md"
    assert index_path.is_file(), f"Missing {index_path}"

    text = _read(index_path)
    for token in (
        "docs/P2_OBSERVATION_REGRESSION_ONE_COMMAND.md",
        "docs/P2_OBSERVATION_REGRESSION_WORKFLOW_DISPATCH.md",
        "docs/P2_ONE_PAGE_DEV_GUIDE.md",
        "docs/P2_REMOTE_OBSERVATION_REGRESSION_RUNBOOK.md",
        "docs/P2_SHARED_DEV_142_DAILY_OPS_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_DRIFT_AUDIT_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_DRIFT_INVESTIGATION_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_READONLY_REFREEZE_CANDIDATE_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_READONLY_REFREEZE_PROPOSAL_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_READONLY_REFREEZE_READINESS_CHECKLIST.md",
        "docs/P2_SHARED_DEV_FIRST_RUN_CHECKLIST.md",
        "docs/P2_SHARED_DEV_142_RERUN_CHECKLIST.md",
        "docs/P2_SHARED_DEV_OBSERVATION_HANDOFF.md",
        "docs/DEV_AND_VERIFICATION_P2_OBSERVATION_WORKFLOW_WRAPPER_20260418.md",
    ):
        assert token in text, f"DELIVERY_DOC_INDEX missing P2 observation doc token: {token}"


def test_dev_and_verification_p2_readme_runbook_alignment_doc_tracks_promoted_p2_operator_docs() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "docs" / "DEV_AND_VERIFICATION_P2_README_RUNBOOK_DISCOVERABILITY_ALIGNMENT_20260420.md"
    assert path.is_file(), f"Missing P2 README runbook alignment dev doc: {path}"

    text = _read(path)
    for token in (
        "README.md",
        "docs/P2_OBSERVATION_REGRESSION_ONE_COMMAND.md",
        "docs/P2_OBSERVATION_REGRESSION_WORKFLOW_DISPATCH.md",
        "docs/P2_ONE_PAGE_DEV_GUIDE.md",
        "docs/P2_REMOTE_OBSERVATION_REGRESSION_RUNBOOK.md",
        "docs/P2_SHARED_DEV_OBSERVATION_HANDOFF.md",
    ):
        assert token in text, f"P2 README runbook alignment dev doc missing token: {token}"


def test_dev_and_verification_p2_readme_first_run_checklist_doc_tracks_first_run_entrypoint() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "docs" / "DEV_AND_VERIFICATION_P2_README_FIRST_RUN_CHECKLIST_DISCOVERABILITY_20260420.md"
    assert path.is_file(), f"Missing P2 first-run discoverability dev doc: {path}"

    text = _read(path)
    for token in (
        "README.md",
        "docs/P2_SHARED_DEV_FIRST_RUN_CHECKLIST.md",
        "docs/P2_OBSERVATION_REGRESSION_ONE_COMMAND.md",
        "docs/P2_SHARED_DEV_OBSERVATION_HANDOFF.md",
    ):
        assert token in text, f"P2 first-run discoverability dev doc missing token: {token}"


def test_p2_remote_observation_runbook_stays_on_wrapper_path_for_baseline_and_rerun() -> None:
    repo_root = _find_repo_root(Path(__file__))
    runbook_path = repo_root / "docs" / "P2_REMOTE_OBSERVATION_REGRESSION_RUNBOOK.md"
    assert runbook_path.is_file(), f"Missing {runbook_path}"

    text = _read(runbook_path)
    for token in (
        "scripts/precheck_p2_observation_regression.sh",
        "bash scripts/run_p2_observation_regression.sh",
        "scripts/run_p2_shared_dev_142_entrypoint.sh",
        "print-readonly-commands",
        "readonly-rerun",
        "workflow-probe",
        "workflow-readonly-check",
        'TOKEN="$ADMIN_TOKEN"',
        'ADMIN_TOKEN=$(',
        'Authorization: Bearer $ADMIN_TOKEN',
    ):
        assert token in text, f"P2 remote observation runbook missing token: {token}"


def _extract_env_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if "<<'ENVEOF'" in line or '<<"ENVEOF"' in line or "<<ENVEOF" in line:
            current = []
            continue
        if current is not None and line.strip() == "ENVEOF":
            blocks.append("\n".join(current))
            current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def test_p2_shared_dev_env_file_examples_stay_repo_safe_and_precheck_compatible() -> None:
    repo_root = _find_repo_root(Path(__file__))
    targets = (
        repo_root / "docs" / "P2_SHARED_DEV_OBSERVATION_HANDOFF.md",
        repo_root / "docs" / "P2_SHARED_DEV_142_RERUN_CHECKLIST.md",
        repo_root / "docs" / "P2_ONE_PAGE_DEV_GUIDE.md",
        repo_root / "docs" / "P2_OBSERVATION_REGRESSION_ONE_COMMAND.md",
        repo_root / "docs" / "DEV_AND_VERIFICATION_P2_OBSERVATION_SHARED_DEV_EXECUTION_GATE_20260419.md",
        repo_root / "scripts" / "print_p2_shared_dev_142_rerun_commands.sh",
        repo_root / "scripts" / "print_p2_shared_dev_observation_commands.sh",
    )

    for path in targets:
        text = _read(path)
        assert "$HOME/.config/yuantus/" in text, f"{path} should keep shared-dev env files outside the repo"
        assert "./p2-shared-dev.env" not in text, f"{path} should not point shared-dev credentials at repo root"
        assert "./p2-observation.env" not in text, f"{path} should not point observation credentials at repo root"
        assert "ARCHIVE_RESULT=1" in text, f"{path} should still document archive enablement"
        for block in _extract_env_blocks(text):
            assert "ARCHIVE_RESULT=1" not in block, f"{path} env-file example must stay precheck-compatible"


def test_p2_shared_dev_mode_selection_script_is_present() -> None:
    repo_root = _find_repo_root(Path(__file__))
    for path in (
        repo_root / "scripts" / "print_p2_shared_dev_bootstrap_commands.sh",
        repo_root / "scripts" / "print_p2_shared_dev_142_drift_audit_commands.sh",
        repo_root / "scripts" / "print_p2_shared_dev_142_drift_investigation_commands.sh",
        repo_root / "scripts" / "print_p2_shared_dev_142_refreeze_candidate_commands.sh",
        repo_root / "scripts" / "print_p2_shared_dev_142_refreeze_proposal_commands.sh",
        repo_root / "scripts" / "print_p2_shared_dev_142_readonly_rerun_commands.sh",
        repo_root / "scripts" / "print_p2_shared_dev_142_rerun_commands.sh",
        repo_root / "scripts" / "print_p2_shared_dev_first_run_commands.sh",
        repo_root / "scripts" / "print_p2_shared_dev_mode_selection.sh",
        repo_root / "scripts" / "print_p2_shared_dev_observation_commands.sh",
        repo_root / "scripts" / "run_p2_shared_dev_142_entrypoint.sh",
        repo_root / "scripts" / "run_p2_shared_dev_142_drift_audit.sh",
        repo_root / "scripts" / "run_p2_shared_dev_142_drift_investigation.sh",
        repo_root / "scripts" / "run_p2_shared_dev_142_refreeze_candidate.sh",
        repo_root / "scripts" / "run_p2_shared_dev_142_refreeze_proposal.sh",
        repo_root / "scripts" / "run_p2_shared_dev_142_readonly_rerun.sh",
        repo_root / "scripts" / "run_p2_shared_dev_142_workflow_probe.sh",
        repo_root / "scripts" / "run_p2_shared_dev_142_workflow_readonly_check.sh",
    ):
        assert path.is_file(), f"Missing shared-dev observation script: {path}"


def test_p2_shared_dev_142_readonly_helper_tracks_current_official_baseline() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "scripts" / "print_p2_shared_dev_142_readonly_rerun_commands.sh"
    assert path.is_file(), f"Missing shared-dev 142 readonly helper: {path}"

    text = _read(path)
    for token in (
        "./tmp/p2-shared-dev-observation-20260421-stable",
        "./tmp/p2-shared-dev-observation-20260421-stable.tar.gz",
        'BASELINE_LABEL="shared-dev-142-readonly-20260421"',
        'EVAL_MODE="readonly"',
        "`142.171.239.56`",
    ):
        assert token in text, f"shared-dev 142 readonly helper missing token: {token}"


def test_p2_shared_dev_142_readonly_runner_tracks_current_official_baseline() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "scripts" / "run_p2_shared_dev_142_readonly_rerun.sh"
    assert path.is_file(), f"Missing shared-dev 142 readonly runner: {path}"

    text = _read(path)
    for token in (
        "$HOME/.config/yuantus/p2-shared-dev.env",
        "./tmp/p2-shared-dev-observation-20260421-stable",
        "./tmp/p2-shared-dev-observation-20260421-stable.tar.gz",
        "shared-dev-142-readonly-20260421",
        "overdue-only-stable",
        "raw-current",
        "stable_current_transform.json",
        "shared-dev-142-readonly-precheck",
        "shared-dev-142-readonly",
        "scripts/validate_p2_shared_dev_env.sh",
        "scripts/precheck_p2_observation_regression.sh",
        "scripts/run_p2_observation_regression.sh",
        "scripts/render_p2_shared_dev_142_stable_current.py",
    ):
        assert token in text, f"shared-dev 142 readonly runner missing token: {token}"


def test_p2_shared_dev_142_entrypoint_wrapper_exposes_all_modes() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "scripts" / "run_p2_shared_dev_142_entrypoint.sh"
    assert path.is_file(), f"Missing shared-dev 142 entrypoint wrapper: {path}"

    text = _read(path)
    for token in (
        "readonly-rerun",
        "refreeze-candidate",
        "refreeze-proposal",
        "drift-audit",
        "drift-investigation",
        "workflow-probe",
        "workflow-readonly-check",
        "print-readonly-commands",
        "print-refreeze-candidate-commands",
        "print-refreeze-proposal-commands",
        "print-drift-commands",
        "print-investigation-commands",
        "--dry-run",
        "scripts/run_p2_shared_dev_142_readonly_rerun.sh",
        "scripts/run_p2_shared_dev_142_refreeze_candidate.sh",
        "scripts/run_p2_shared_dev_142_refreeze_proposal.sh",
        "scripts/run_p2_shared_dev_142_drift_audit.sh",
        "scripts/run_p2_shared_dev_142_drift_investigation.sh",
        "scripts/run_p2_shared_dev_142_workflow_probe.sh",
        "scripts/run_p2_shared_dev_142_workflow_readonly_check.sh",
        "scripts/print_p2_shared_dev_142_readonly_rerun_commands.sh",
        "scripts/print_p2_shared_dev_142_refreeze_candidate_commands.sh",
        "scripts/print_p2_shared_dev_142_refreeze_proposal_commands.sh",
        "scripts/print_p2_shared_dev_142_drift_audit_commands.sh",
        "scripts/print_p2_shared_dev_142_drift_investigation_commands.sh",
    ):
        assert token in text, f"shared-dev 142 entrypoint wrapper missing token: {token}"


def test_dev_and_verification_shared_dev_142_entrypoint_selector_doc_keeps_all_modes_visible() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "docs" / "DEV_AND_VERIFICATION_SHARED_DEV_142_ENTRYPOINT_SELECTOR_20260420.md"
    assert path.is_file(), f"Missing shared-dev 142 selector dev doc: {path}"

    text = _read(path)
    for token in (
        "scripts/run_p2_shared_dev_142_entrypoint.sh",
        "readonly-rerun",
        "drift-audit",
        "drift-investigation",
        "workflow-probe",
        "workflow-readonly-check",
        "print-readonly-commands",
        "print-drift-commands",
        "print-investigation-commands",
        "--dry-run",
    ):
        assert token in text, f"shared-dev 142 selector dev doc missing token: {token}"


def test_p2_shared_dev_142_drift_audit_runner_tracks_current_official_baseline() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "scripts" / "run_p2_shared_dev_142_drift_audit.sh"
    assert path.is_file(), f"Missing shared-dev 142 drift audit runner: {path}"

    text = _read(path)
    for token in (
        "$HOME/.config/yuantus/p2-shared-dev.env",
        "./tmp/p2-shared-dev-observation-20260421-stable",
        "./tmp/p2-shared-dev-observation-20260421-stable.tar.gz",
        "shared-dev-142-readonly-20260421",
        "current-drift-audit",
        "DRIFT_AUDIT.md",
        "drift_audit.json",
        "scripts/run_p2_shared_dev_142_readonly_rerun.sh",
        "scripts/render_p2_shared_dev_142_drift_audit.py",
    ):
        assert token in text, f"shared-dev 142 drift audit runner missing token: {token}"


def test_p2_shared_dev_142_drift_audit_checklist_hands_off_to_investigation() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "docs" / "P2_SHARED_DEV_142_DRIFT_AUDIT_CHECKLIST.md"
    assert path.is_file(), f"Missing shared-dev 142 drift audit checklist: {path}"

    text = _read(path)
    for token in (
        "docs/P2_SHARED_DEV_142_DRIFT_INVESTIGATION_CHECKLIST.md",
        "drift-investigation",
        "print-investigation-commands",
        "run_p2_shared_dev_142_drift_investigation.sh",
        "DRIFT_INVESTIGATION.md",
        "drift_investigation.json",
    ):
        assert token in text, f"shared-dev 142 drift audit checklist missing token: {token}"


def test_p2_shared_dev_142_drift_investigation_runner_tracks_current_official_baseline() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "scripts" / "run_p2_shared_dev_142_drift_investigation.sh"
    assert path.is_file(), f"Missing shared-dev 142 drift investigation runner: {path}"

    text = _read(path)
    for token in (
        "$HOME/.config/yuantus/p2-shared-dev.env",
        "./tmp/p2-shared-dev-observation-20260421-stable",
        "./tmp/p2-shared-dev-observation-20260421-stable.tar.gz",
        "shared-dev-142-readonly-20260421",
        "current-drift-audit",
        "DRIFT_INVESTIGATION.md",
        "drift_investigation.json",
        "scripts/run_p2_shared_dev_142_drift_audit.sh",
        "scripts/render_p2_shared_dev_142_drift_investigation.py",
    ):
        assert token in text, f"shared-dev 142 drift investigation runner missing token: {token}"


def test_p2_shared_dev_142_workflow_probe_tracks_fixed_host_defaults() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "scripts" / "run_p2_shared_dev_142_workflow_probe.sh"
    assert path.is_file(), f"Missing shared-dev 142 workflow probe wrapper: {path}"

    text = _read(path)
    for token in (
        "http://142.171.239.56:7910",
        "tenant-1",
        "org-1",
        "shared-dev-142-workflow-probe",
        "current-only workflow probe",
        "scripts/run_p2_observation_regression_workflow.sh",
    ):
        assert token in text, f"shared-dev 142 workflow probe wrapper missing token: {token}"


def test_p2_shared_dev_142_workflow_readonly_check_wrapper_tracks_fixed_baseline() -> None:
    repo_root = _find_repo_root(Path(__file__))
    path = repo_root / "scripts" / "run_p2_shared_dev_142_workflow_readonly_check.sh"
    assert path.is_file(), f"Missing shared-dev 142 workflow readonly check wrapper: {path}"

    text = _read(path)
    for token in (
        "./tmp/p2-shared-dev-observation-20260421-stable",
        "./tmp/p2-shared-dev-observation-20260421-stable.tar.gz",
        "shared-dev-142-readonly-20260421",
        "workflow-probe-current",
        "workflow_stable_current_transform.json",
        "http://142.171.239.56:7910",
        "public observation target unavailable",
        "scripts/render_p2_shared_dev_142_stable_current.py",
        "WORKFLOW_READONLY_DIFF.md",
        "WORKFLOW_READONLY_EVAL.md",
        "WORKFLOW_READONLY_CHECK.md",
        "scripts/run_p2_shared_dev_142_workflow_probe.sh",
        "scripts/compare_p2_observation_results.py",
        "scripts/evaluate_p2_observation_results.py",
    ):
        assert token in text, f"shared-dev 142 workflow readonly check wrapper missing token: {token}"
