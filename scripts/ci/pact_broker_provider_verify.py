"""Blocking pact-broker provider verification (Phase B of the auto-gate).

Spins up the SAME seeded Yuantus provider used by the committed-pact verifier
(``test_pact_provider_yuantus_plm.py`` — reusing ``_isolated_test_database`` +
``_running_provider`` + the ``/_pact/provider_states`` setup endpoint) and runs the
``pact_verifier_cli`` against the **broker-sourced** consumer pact, publishing
verification results back to the broker (provider version = commit SHA, branch = git ref).

Design notes (see docs/development/plm-collab-pact-broker-autogate-design-20260621.md):
- **Guarded:** no-ops (exit 0) unless ``PACT_BROKER_BASE_URL`` is set, so it stays inert until
  ops provisions the PactFlow account + the two GitHub secrets.
- **Blocking (Phase B):** invoked from a BLOCKING CI step (the Phase B flip removed
  ``continue-on-error``) — a non-zero exit now fails the build. The committed-pact verifier
  remains a redundant local gate; the guard above still skips unconfigured / fork CI.
- **CLI, not pact-python's broker API:** the verifier CLI sources pacts from the broker +
  publishes results via documented flags, avoiding pact-python version-specific broker calls.

Verified against live PactFlow at activation (#861 ``a352baa9``): provider-verify rc=0, the
deliberate drift-catch rc=1, can-i-deploy rc=0. The flags + auth below are that confirmed set.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

PROVIDER = "YuantusPLM"
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def main() -> int:
    broker_url = os.environ.get("PACT_BROKER_BASE_URL", "").strip()
    if not broker_url:
        print("[pact-broker] PACT_BROKER_BASE_URL not set — skipping broker verification (unconfigured / fork CI).")
        return 0

    token = os.environ.get("PACT_BROKER_TOKEN", "").strip()
    if not token:
        print(
            "::error title=Pact broker token missing::PACT_BROKER_BASE_URL is set but "
            "PACT_BROKER_TOKEN is empty; broker verification is configured but invalid."
        )
        return 1

    version = os.environ.get("GITHUB_SHA", "").strip() or "dev"
    branch = os.environ.get("GITHUB_REF_NAME", "").strip() or "main"

    # Reuse the committed-pact verifier's harness: isolated DB + seeded, running provider that
    # exposes /_pact/provider_states. Imported lazily so a missing pact runtime can't break import.
    from yuantus.api.tests.test_pact_provider_yuantus_plm import (  # noqa: E501
        _isolated_test_database,
        _running_provider,
    )

    with _isolated_test_database():
        with _running_provider() as base_url:
            parsed_base_url = urlparse(base_url)
            cmd = [
                "pact_verifier_cli",
                "--provider-name", PROVIDER,
                "--hostname", parsed_base_url.hostname or "localhost",
                "--port", str(parsed_base_url.port or 80),
                "--transport", parsed_base_url.scheme or "http",
                "--state-change-url", f"{base_url}/_pact/provider_states",
                "--broker-url", broker_url,
                # Verify the consumer's main-branch pact; broaden selectors at the Phase B flip.
                "--consumer-version-selectors", '{"mainBranch": true}',
                "--publish",
                "--provider-version", version,
                "--provider-branch", branch,
            ]
            if token:
                cmd += ["--token", token]
            redacted = " ".join("***" if (token and part == token) else part for part in cmd)
            print(f"[pact-broker] $ {redacted}")
            env = os.environ.copy()
            env.setdefault("PACT_BROKER_ERROR_ON_UNKNOWN_OPTION", "true")
            proc = subprocess.run(cmd, check=False, env=env)
            print(f"[pact-broker] pact_verifier_cli exit={proc.returncode}")
            return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
