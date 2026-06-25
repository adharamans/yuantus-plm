from __future__ import annotations

from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(12):
        if (cur / "pyproject.toml").is_file() and (cur / ".github").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise AssertionError("Could not locate repo root (expected pyproject.toml + .github/)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_p2_observation_regression_workflow_contracts() -> None:
    repo_root = _find_repo_root(Path(__file__))
    wf = repo_root / ".github" / "workflows" / "p2-observation-regression.yml"
    assert wf.is_file(), f"Missing workflow: {wf}"
    wf_text = _read(wf)

    for token in (
        "name: p2-observation-regression",
        "workflow_dispatch:",
        "permissions:",
        "contents: read",
        "actions: read",
        "concurrency:",
        "cancel-in-progress: true",
        "github.ref",
        "timeout-minutes: 30",
    ):
        assert token in wf_text, f"workflow missing token: {token}"

    for token in (
        "base_url:",
        "tenant_id:",
        "org_id:",
        "username:",
        "environment:",
        "company_id:",
        "eco_type:",
        "eco_state:",
        "deadline_from:",
        "deadline_to:",
        'default: "tenant-1"',
        'default: "org-1"',
        'default: "admin"',
        'default: "workflow-dispatch"',
    ):
        assert token in wf_text, f"workflow missing dispatch input token: {token}"

    for token in (
        "Validate auth configuration",
        "id: validate_auth_config",
        "secrets.P2_OBSERVATION_TOKEN",
        "secrets.P2_OBSERVATION_PASSWORD",
        "WORKFLOW_PRECHECK.md",
        "workflow_precheck.json",
        '"reason": "missing authentication secret"',
        "echo \"auth_ready=false\" >> \"$GITHUB_OUTPUT\"",
        "echo \"auth_ready=true\" >> \"$GITHUB_OUTPUT\"",
        "Run P2 observation regression",
        "id: run_p2_observation_regression",
        "if: steps.validate_auth_config.outputs.auth_ready == 'true'",
        "bash scripts/run_p2_observation_regression.sh",
        "EVAL_MODE: current-only",
        "OUTPUT_DIR: tmp/p2-observation-workflow/${{ github.run_id }}",
        "Write observation summary to job summary",
        "steps.validate_auth_config.outputs.auth_ready",
        "steps.run_p2_observation_regression.outcome",
        'if [[ -f "${OUTPUT_DIR}/WORKFLOW_PRECHECK.md" ]]',
        'cat "${OUTPUT_DIR}/WORKFLOW_PRECHECK.md" >> "$GITHUB_STEP_SUMMARY"',
        'echo "## P2 Observation Regression" >> "$GITHUB_STEP_SUMMARY"',
        'cat "${OUTPUT_DIR}/OBSERVATION_RESULT.md" >> "$GITHUB_STEP_SUMMARY"',
        'cat "${OUTPUT_DIR}/OBSERVATION_EVAL.md" >> "$GITHUB_STEP_SUMMARY"',
        "Write workflow failure diagnostics",
        "WORKFLOW_FAILURE.md",
        "workflow_failure.json",
        "Upload P2 observation regression evidence",
        "if: always()",
        "name: p2-observation-regression",
        "path: tmp/p2-observation-workflow/${{ github.run_id }}",
        "if-no-files-found: error",
        "retention-days: 14",
        "Fail workflow when precheck or observation execution failed",
        "ERROR: authentication precheck failed",
        "ERROR: observation regression execution failed",
    ):
        assert token in wf_text, f"workflow missing execution/artifact token: {token}"

    doc = repo_root / "docs" / "P2_OBSERVATION_REGRESSION_WORKFLOW_DISPATCH.md"
    assert doc.is_file(), f"Missing doc: {doc}"
    doc_text = _read(doc)
    for token in (
        "gh workflow run p2-observation-regression",
        "scripts/run_p2_observation_regression_workflow.sh",
        "scripts/run_p2_shared_dev_142_entrypoint.sh",
        "scripts/run_p2_shared_dev_142_workflow_probe.sh",
        "scripts/run_p2_shared_dev_142_workflow_readonly_check.sh",
        "docs/P2_SHARED_DEV_142_DRIFT_INVESTIGATION_CHECKLIST.md",
        "workflow-probe",
        "workflow-readonly-check",
        "drift-investigation",
        "print-investigation-commands",
        "P2_OBSERVATION_TOKEN",
        "P2_OBSERVATION_PASSWORD",
        "WORKFLOW_PRECHECK.md",
        "workflow_precheck.json",
        "current-only",
        "p2-observation-regression",
    ):
        assert token in doc_text, f"workflow dispatch doc missing token: {token}"
