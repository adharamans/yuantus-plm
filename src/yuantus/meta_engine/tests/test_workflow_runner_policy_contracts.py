from __future__ import annotations

from pathlib import Path

import yaml

_ALLOWED_RUNNERS = {"ubuntu-latest"}
_RUNNER_EXCEPTIONS = {
    (".github/workflows/cad-helper-shared-dotnet.yml", "dotnet"): {"windows-latest"},
}


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(12):
        if (cur / "pyproject.toml").is_file() and (cur / ".github").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise AssertionError("Could not locate repo root (expected pyproject.toml + .github/)")


def _load_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    assert isinstance(payload, dict), f"workflow did not parse as mapping: {path}"
    return payload


def _assert_runner_is_allowed(*, workflow_rel: str, job_name: str, runner: object) -> None:
    allowed = _ALLOWED_RUNNERS | _RUNNER_EXCEPTIONS.get((workflow_rel, job_name), set())
    if isinstance(runner, str):
        assert runner in allowed, (
            f"{workflow_rel} job '{job_name}' runs-on={runner!r} not in allowed set "
            f"{sorted(allowed)}"
        )
        return

    if isinstance(runner, list):
        assert runner, f"{workflow_rel} job '{job_name}' runs-on list must not be empty"
        for idx, item in enumerate(runner):
            assert isinstance(item, str), (
                f"{workflow_rel} job '{job_name}' runs-on[{idx}] must be string, "
                f"got {type(item).__name__}"
            )
            assert item in allowed, (
                f"{workflow_rel} job '{job_name}' runs-on[{idx}]={item!r} not in allowed set "
                f"{sorted(allowed)}"
            )
        return

    raise AssertionError(
        f"{workflow_rel} job '{job_name}' runs-on must be string/list, got {type(runner).__name__}"
    )


def test_workflow_jobs_use_allowed_runners() -> None:
    repo_root = _find_repo_root(Path(__file__))
    workflows = sorted((repo_root / ".github" / "workflows").glob("*.yml"))
    assert workflows, "No workflow files found under .github/workflows"

    for workflow in workflows:
        rel = workflow.relative_to(repo_root).as_posix()
        payload = _load_yaml(workflow)
        jobs = payload.get("jobs")
        assert isinstance(jobs, dict), f"{rel} must declare jobs mapping"
        assert jobs, f"{rel} must declare at least one job"

        for job_name, job_payload in jobs.items():
            assert isinstance(job_payload, dict), f"{rel} job '{job_name}' must be a mapping"
            assert "runs-on" in job_payload, f"{rel} job '{job_name}' must declare runs-on"
            _assert_runner_is_allowed(
                workflow_rel=rel,
                job_name=str(job_name),
                runner=job_payload["runs-on"],
            )
