from __future__ import annotations

import importlib.util
import sys
import types
from contextlib import contextmanager
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


def _load_broker_script():
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "ci" / "pact_broker_provider_verify.py"
    spec = importlib.util.spec_from_file_location("pact_broker_provider_verify_under_test", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ci_contracts_job_wires_pact_provider_verifier() -> None:
    repo_root = _find_repo_root(Path(__file__))
    ci_yml = repo_root / ".github" / "workflows" / "ci.yml"
    assert ci_yml.is_file()

    text = _read(ci_yml)
    assert "workflow_dispatch: {}" in text
    assert "pact-python==3.2.1" in text
    assert "pip install -r requirements.lock pytest pact-python==3.2.1" in text
    assert "Pact provider verifier (Metasheet2 -> Yuantus)" in text
    assert "src/yuantus/api/tests/test_pact_provider_yuantus_plm.py" in text
    assert "scripts/ci/pact_broker_provider_verify.py" in text
    assert "Pact broker token missing" in text
    assert 'PACT_BROKER_ERROR_ON_UNKNOWN_OPTION: "true"' in text
    assert 'PACT_CLI_VERSION: "v2.5.12"' in text
    assert "https://raw.githubusercontent.com/pact-foundation/pact-ruby-standalone/master/install.sh" in text
    assert 'export PATH="$PWD/pact/bin:$PATH"' in text
    assert "pact-broker can-i-deploy --pacticipant YuantusPLM" in text


def test_pact_broker_step_is_blocking_phase_b() -> None:
    """Phase B: the broker verify/can-i-deploy step must be BLOCKING.

    Scoped to the broker step block only — other CI steps may use continue-on-error
    legitimately, so a global ``"continue-on-error" not in text`` would be wrong.
    """
    repo_root = _find_repo_root(Path(__file__))
    ci_yml = repo_root / ".github" / "workflows" / "ci.yml"
    text = _read(ci_yml)

    # Positive Phase-B markers: the step name and the blocking log line.
    assert "Pact broker verify + publish + can-i-deploy (blocking, Phase B)" in text
    assert "(Phase B: BLOCKING)" in text

    # Isolate the broker step block (from its `- name:` to the next step's `- name:`).
    marker = "- name: Pact broker verify + publish + can-i-deploy (blocking, Phase B)"
    start = text.index(marker)
    tail = text[start + len(marker):]
    end_rel = tail.find("\n      - name:")
    broker_block = tail if end_rel == -1 else tail[:end_rel]

    # The gate must not be defanged by a `continue-on-error:` step key (scoped to this step).
    # Match the actual YAML directive, not the word inside the revert-note comment.
    directive_lines = [
        ln for ln in broker_block.splitlines()
        if ln.strip().startswith("continue-on-error:")
    ]
    assert not directive_lines, (
        f"Phase B: broker step must be blocking (no continue-on-error directive); found {directive_lines}"
    )
    # The verify + can-i-deploy verdict is the step's exit code.
    assert "[ $rc1 -eq 0 ] && [ $rc2 -eq 0 ]" in broker_block
    # The pact CLI install is a curl|bash pipeline; pipefail keeps curl failures retryable/visible.
    assert "set -o pipefail" in broker_block
    # The secret-guard skip (legit resilience for unconfigured/fork CI) stays.
    assert "PACT_BROKER_BASE_URL not set" in broker_block


def test_ci_change_scope_covers_pact_provider_and_cad_diff_surface() -> None:
    repo_root = _find_repo_root(Path(__file__))
    ci_yml = repo_root / ".github" / "workflows" / "ci.yml"
    assert ci_yml.is_file()

    text = _read(ci_yml)
    for token in (
        "contracts/pacts/*.json",
        "scripts/ci/pact_broker_provider_verify.py",
        "src/yuantus/api/tests/test_pact_provider_yuantus_plm.py",
        "src/yuantus/meta_engine/tests/test_ci_contracts_pact_provider_gate.py",
        "src/yuantus/meta_engine/web/cad_backend_profile_router.py",
        "src/yuantus/meta_engine/web/cad_checkin_router.py",
        "src/yuantus/meta_engine/web/cad_connectors_router.py",
        "src/yuantus/meta_engine/web/cad_diff_router.py",
        "src/yuantus/meta_engine/web/cad_file_data_router.py",
        "src/yuantus/meta_engine/web/cad_history_router.py",
        "src/yuantus/meta_engine/web/cad_import_router.py",
        "src/yuantus/meta_engine/web/cad_mesh_stats_router.py",
        "src/yuantus/meta_engine/web/cad_properties_router.py",
        "src/yuantus/meta_engine/web/cad_review_router.py",
        "src/yuantus/meta_engine/web/cad_router.py",
        "src/yuantus/meta_engine/web/cad_sync_template_router.py",
        "src/yuantus/meta_engine/web/cad_view_state_router.py",
        "src/yuantus/web/cad_review.html",
    ):
        assert token in text, f"Expected detect_changes contract trigger token: {token}"


def test_pact_broker_provider_name_matches_committed_pact() -> None:
    repo_root = _find_repo_root(Path(__file__))
    script = repo_root / "scripts" / "ci" / "pact_broker_provider_verify.py"
    pact = repo_root / "contracts" / "pacts" / "metasheet2-yuantus-plm.json"

    assert script.is_file()
    assert pact.is_file()

    script_text = _read(script)
    pact_text = _read(pact)
    assert 'PROVIDER = "YuantusPLM"' in script_text
    assert 'SRC_ROOT = REPO_ROOT / "src"' in script_text
    assert "sys.path.insert(0, str(SRC_ROOT))" in script_text
    assert "PACT_BROKER_TOKEN is empty" in script_text
    assert '"provider":' in pact_text
    assert '"name": "YuantusPLM"' in pact_text


def test_pact_broker_provider_script_skips_without_broker_url(monkeypatch, capsys) -> None:
    script = _load_broker_script()
    monkeypatch.delenv("PACT_BROKER_BASE_URL", raising=False)
    monkeypatch.delenv("PACT_BROKER_TOKEN", raising=False)

    assert script.main() == 0

    captured = capsys.readouterr()
    assert "PACT_BROKER_BASE_URL not set" in captured.out


def test_pact_broker_provider_script_fails_when_url_set_without_token(monkeypatch, capsys) -> None:
    script = _load_broker_script()
    monkeypatch.setenv("PACT_BROKER_BASE_URL", "https://example.pactflow.io")
    monkeypatch.delenv("PACT_BROKER_TOKEN", raising=False)

    assert script.main() == 1

    captured = capsys.readouterr()
    assert "Pact broker token missing" in captured.out


def test_pact_broker_provider_script_redacts_token_and_uses_main_branch_selector(
    monkeypatch, capsys
) -> None:
    script = _load_broker_script()
    token = "secret-token-123"
    captured_cmd: list[str] = []

    @contextmanager
    def isolated_database():
        yield

    @contextmanager
    def running_provider():
        yield "http://127.0.0.1:43210"

    fake_provider_module = types.ModuleType("yuantus.api.tests.test_pact_provider_yuantus_plm")
    fake_provider_module._isolated_test_database = isolated_database
    fake_provider_module._running_provider = running_provider
    monkeypatch.setitem(
        sys.modules,
        "yuantus.api.tests.test_pact_provider_yuantus_plm",
        fake_provider_module,
    )

    captured_env: dict[str, str] = {}

    def fake_run(cmd, check=False, env=None):
        assert check is False
        captured_cmd.extend(cmd)
        assert env is not None
        captured_env.update(env)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    monkeypatch.setenv("PACT_BROKER_BASE_URL", "https://example.pactflow.io")
    monkeypatch.setenv("PACT_BROKER_TOKEN", token)
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv("GITHUB_REF_NAME", "feature/pr-branch")

    assert script.main() == 0

    captured = capsys.readouterr()
    assert token not in captured.out
    assert "--token ***" in captured.out
    assert "pact_verifier_cli" in captured_cmd
    assert "--provider-name" in captured_cmd
    assert "YuantusPLM" in captured_cmd
    assert "--hostname" in captured_cmd
    assert "--port" in captured_cmd
    assert "--transport" in captured_cmd
    assert "--state-change-url" in captured_cmd
    assert "--broker-url" in captured_cmd
    assert "--consumer-version-selectors" in captured_cmd
    assert '{"mainBranch": true}' in captured_cmd
    assert "--publish" in captured_cmd
    assert "--provider-version" in captured_cmd
    assert "abc123" in captured_cmd
    assert "--provider-branch" in captured_cmd
    assert "feature/pr-branch" in captured_cmd
    assert captured_env["PACT_BROKER_ERROR_ON_UNKNOWN_OPTION"] == "true"
