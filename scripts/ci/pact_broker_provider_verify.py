"""Advisory pact-broker provider verification (Phase A of the auto-gate).

Spins up the SAME seeded Yuantus provider used by the committed-pact verifier
(``test_pact_provider_yuantus_plm.py`` — reusing ``_isolated_test_database`` +
``_running_provider`` + the ``/_pact/provider_states`` setup endpoint) and runs the
``pact-provider-verifier`` CLI against the **broker-sourced** consumer pact, publishing
verification results back to the broker (provider version = commit SHA, branch = git ref).

Design notes (see docs/development/plm-collab-pact-broker-autogate-design-20260621.md):
- **Guarded:** no-ops (exit 0) unless ``PACT_BROKER_BASE_URL`` is set, so it stays inert until
  ops provisions the PactFlow account + the two GitHub secrets.
- **Advisory:** invoked from a ``continue-on-error`` CI step; the committed-pact verifier remains
  the live gate. This is strictly additive.
- **CLI, not pact-python's broker API:** the verifier CLI sources pacts from the broker +
  publishes results via documented flags, avoiding pact-python version-specific broker calls.

UNVERIFIED until a live broker exists: the exact ``pact-provider-verifier`` flags + auth must be
confirmed against a real PactFlow instance at activation — see the dev & verification MD runbook.
"""
from __future__ import annotations

import os
import subprocess

PROVIDER = "YuantusPLM"


def main() -> int:
    broker_url = os.environ.get("PACT_BROKER_BASE_URL", "").strip()
    if not broker_url:
        print("[pact-broker] PACT_BROKER_BASE_URL not set — skipping advisory broker verification.")
        return 0

    token = os.environ.get("PACT_BROKER_TOKEN", "").strip()
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
            cmd = [
                "pact-provider-verifier",
                "--provider", PROVIDER,
                "--provider-base-url", base_url,
                "--provider-states-setup-url", f"{base_url}/_pact/provider_states",
                "--pact-broker-base-url", broker_url,
                # Verify the consumer's main-branch pact; broaden selectors at the Phase B flip.
                "--consumer-version-selector", '{"mainBranch": true}',
                "--publish-verification-results",
                "--provider-app-version", version,
                "--provider-version-branch", branch,
            ]
            if token:
                cmd += ["--broker-token", token]
            redacted = " ".join("***" if (token and part == token) else part for part in cmd)
            print(f"[pact-broker] $ {redacted}")
            proc = subprocess.run(cmd, check=False)
            print(f"[pact-broker] pact-provider-verifier exit={proc.returncode} (advisory)")
            return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
