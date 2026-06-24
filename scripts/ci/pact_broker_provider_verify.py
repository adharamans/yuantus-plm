"""Advisory pact-broker provider verification (Phase A of the auto-gate).

Spins up the SAME seeded Yuantus provider used by the committed-pact verifier
(``test_pact_provider_yuantus_plm.py`` — reusing ``_isolated_test_database`` +
``_running_provider`` + the ``/_pact/provider_states`` setup endpoint) and runs the
``pact_verifier_cli`` against the **broker-sourced** consumer pact, publishing
verification results back to the broker (provider version = commit SHA, branch = git ref).

Design notes (see docs/development/plm-collab-pact-broker-autogate-design-20260621.md):
- **Guarded:** no-ops (exit 0) unless ``PACT_BROKER_BASE_URL`` is set, so it stays inert until
  ops provisions the PactFlow account + the two GitHub secrets.
- **Advisory:** invoked from a ``continue-on-error`` CI step; the committed-pact verifier remains
  the live gate. This is strictly additive.
- **CLI, not pact-python's broker API:** the verifier CLI sources pacts from the broker +
  publishes results via documented flags, avoiding pact-python version-specific broker calls.

UNVERIFIED until a live broker exists: the exact ``pact_verifier_cli`` flags + auth must be
confirmed against a real PactFlow instance at activation — see the dev & verification MD runbook.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import requests

PROVIDER = "YuantusPLM"
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@contextlib.contextmanager
def _drift_breaking_proxy(upstream_base_url: str):
    """Temporary drift-catch proxy: flip one pact-pinned capability field."""

    parsed_upstream = urlparse(upstream_base_url)
    upstream_origin = f"{parsed_upstream.scheme}://{parsed_upstream.netloc}"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
            self._forward()

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
            self._forward()

        def do_PATCH(self) -> None:  # noqa: N802 - stdlib hook name
            self._forward()

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib hook name
            self._forward()

        def log_message(self, _format: str, *args: object) -> None:
            return None

        def _forward(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in {"host", "content-length", "connection"}
            }
            response = requests.request(
                self.command,
                f"{upstream_origin}{self.path}",
                data=body or None,
                headers=headers,
                timeout=10,
            )
            content = response.content
            if self.path.startswith("/api/v1/integrations/capabilities"):
                payload = response.json()
                payload["features"]["bom_multitable"]["supported"] = "drifted"
                payload["features"]["bom_multitable"]["entitled"] = "drifted"
                content = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

            self.send_response(response.status_code)
            for key, value in response.headers.items():
                if key.lower() not in {"content-length", "connection", "transfer-encoding"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        port = int(sock.getsockname()[1])

    server = ThreadingHTTPServer(("localhost", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://localhost:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def main() -> int:
    broker_url = os.environ.get("PACT_BROKER_BASE_URL", "").strip()
    if not broker_url:
        print("[pact-broker] PACT_BROKER_BASE_URL not set — skipping advisory broker verification.")
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
            with _drift_breaking_proxy(base_url) as broker_base_url:
                parsed_base_url = urlparse(broker_base_url)
                state_change_url = f"{broker_base_url}/_pact/provider_states"
                cmd = [
                    "pact_verifier_cli",
                    "--provider-name", PROVIDER,
                    "--hostname", parsed_base_url.hostname or "localhost",
                    "--port", str(parsed_base_url.port or 80),
                    "--transport", parsed_base_url.scheme or "http",
                    "--state-change-url", state_change_url,
                    "--broker-url", broker_url,
                    # Verify consumer main; broaden selectors at the Phase B flip.
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
                print(f"[pact-broker] pact_verifier_cli exit={proc.returncode} (advisory)")
                return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
