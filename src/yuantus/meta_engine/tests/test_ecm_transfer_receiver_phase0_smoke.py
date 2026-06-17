from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/ecm_publish_phase0/transfer_receiver_smoke.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("transfer_receiver_smoke", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _env(file_path: Path) -> dict[str, str]:
    return {
        "YUANTUS_PUBLICATION_ECM_BASE_URL": "http://athena.example",
        "YUANTUS_PUBLICATION_ECM_TRANSFER_USER": "plm-phase0",
        "YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET": "super-secret",
        "YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID": "00000000-0000-0000-0000-000000000111",
        "YUANTUS_PUBLICATION_ECM_CONFLICT_POLICY": "SKIP",
        "YUANTUS_PUBLICATION_ECM_PHASE0_FILE": str(file_path),
    }


def test_dry_run_reports_missing_inputs_without_network_or_secret() -> None:
    smoke = _load_script()
    out = io.StringIO()

    rc = smoke.main([], env={"YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET": "dont-print"}, stdout=out)

    assert rc == 0
    body = json.loads(out.getvalue())
    assert body["status"] == "dry_run"
    assert body["network_io"] is False
    assert "U4 replay same sourceNodeId/watermark and expect UNCHANGED" in body["would_run"]
    assert "dont-print" not in out.getvalue()


def test_live_mode_requires_all_inputs() -> None:
    smoke = _load_script()
    err = io.StringIO()

    rc = smoke.main(["--yes-live"], env={}, stderr=err)

    assert rc == 1
    body = json.loads(err.getvalue())
    assert body["status"] == "failed"
    assert "YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET" in body["error"]


def test_phase0_runs_verify_folders_create_replay_and_new_version(tmp_path: Path) -> None:
    smoke = _load_script()
    sample = tmp_path / "gear.step"
    sample.write_bytes(b"phase0-cad")
    config = smoke.config_from_env(
        _env(sample),
        prefix="phase0-test",
        now=datetime(2026, 6, 16, 20, 0, 0, tzinfo=timezone.utc),
    )
    requests: list[httpx.Request] = []
    document_source_node_ids: list[str] = []
    folder_ids = iter(
        [
            "00000000-0000-0000-0000-000000000201",
            "00000000-0000-0000-0000-000000000202",
            "00000000-0000-0000-0000-000000000203",
        ]
    )
    document_ids = iter(
        [
            "00000000-0000-0000-0000-000000000301",
            "00000000-0000-0000-0000-000000000301",
            "00000000-0000-0000-0000-000000000302",
        ]
    )
    dispositions = iter(["CREATED", "UNCHANGED", "CREATED"])

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        headers = {k.lower(): v for k, v in req.headers.items()}
        assert headers["x-athena-transfer-user"] == "plm-phase0"
        assert headers["x-athena-transfer-secret"] == "super-secret"
        assert "authorization" not in headers
        if req.url.path.endswith("/verify"):
            assert req.url.params["folderId"] == "00000000-0000-0000-0000-000000000111"
            return httpx.Response(200, json={"repositoryId": "athena"})
        if req.url.path.endswith("/folders"):
            payload = json.loads(req.content.decode("utf-8"))
            assert payload["parentFolderId"] == "00000000-0000-0000-0000-000000000111"
            assert payload["conflictPolicy"] == "SKIP"
            assert payload["sourceRepositoryId"] == "yuantus-plm"
            assert payload["sourceLastModifiedAt"] == "2026-06-16T20:00:00"
            return httpx.Response(
                201,
                json={
                    "folderId": next(folder_ids),
                    "folderName": payload["name"],
                    "disposition": "CREATED",
                },
            )
        assert req.url.path.endswith("/documents")
        body = req.content.decode("latin-1")
        source_node_id = body.split('name="sourceNodeId"\r\n\r\n', 1)[1].split("\r\n", 1)[0]
        document_source_node_ids.append(source_node_id)
        assert (
            'name="parentFolderId"\r\n\r\n00000000-0000-0000-0000-000000000111'
            in body
        )
        assert 'name="sourceLastModifiedAt"\r\n\r\n2026-06-16T20:00:00' in body
        assert 'filename="gear.step"' in body
        assert "phase0-cad" in body
        return httpx.Response(
            201,
            json={
                "documentId": next(document_ids),
                "documentName": "gear.step",
                "disposition": next(dispositions),
            },
        )

    result = smoke.run_phase0(config, transport=httpx.MockTransport(handler))

    assert result["status"] == "passed"
    assert [req.url.path for req in requests] == [
        "/api/v1/transfer/receiver/verify",
        "/api/v1/transfer/receiver/folders",
        "/api/v1/transfer/receiver/folders",
        "/api/v1/transfer/receiver/documents",
        "/api/v1/transfer/receiver/documents",
        "/api/v1/transfer/receiver/folders",
        "/api/v1/transfer/receiver/documents",
    ]
    assert document_source_node_ids[0] == document_source_node_ids[1]
    assert document_source_node_ids[2] != document_source_node_ids[0]
    assert result["documents"]["v1"] == result["documents"]["v1_replay"]
    assert result["documents"]["v2"] != result["documents"]["v1"]


def test_verify_repository_is_receiver_identity_not_source_identity(tmp_path: Path) -> None:
    smoke = _load_script()
    sample = tmp_path / "gear.step"
    sample.write_bytes(b"phase0-cad")
    config = smoke.config_from_env(
        _env(sample),
        prefix="phase0-test",
        now=datetime(2026, 6, 16, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert config.expected_repository_id == "athena"
    assert config.source_repository_id == "yuantus-plm"
    seen_source_ids: list[str] = []
    folder_ids = iter(
        [
            "00000000-0000-0000-0000-000000000201",
            "00000000-0000-0000-0000-000000000202",
            "00000000-0000-0000-0000-000000000203",
        ]
    )
    document_ids = iter(
        [
            "00000000-0000-0000-0000-000000000301",
            "00000000-0000-0000-0000-000000000301",
            "00000000-0000-0000-0000-000000000302",
        ]
    )
    dispositions = iter(["CREATED", "UNCHANGED", "CREATED"])

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/verify"):
            return httpx.Response(200, json={"repositoryId": "athena"})
        if req.url.path.endswith("/folders"):
            payload = json.loads(req.content.decode("utf-8"))
            seen_source_ids.append(payload["sourceRepositoryId"])
            return httpx.Response(
                201,
                json={
                    "folderId": next(folder_ids),
                    "folderName": payload["name"],
                    "disposition": "CREATED",
                },
            )
        assert req.url.path.endswith("/documents")
        body = req.content.decode("latin-1")
        seen_source_ids.append(
            body.split('name="sourceRepositoryId"\r\n\r\n', 1)[1].split("\r\n", 1)[0]
        )
        return httpx.Response(
            201,
            json={
                "documentId": next(document_ids),
                "documentName": "gear.step",
                "disposition": next(dispositions),
            },
        )

    result = smoke.run_phase0(config, transport=httpx.MockTransport(handler))

    assert result["status"] == "passed"
    assert result["steps"][0]["repository_id"] == "athena"
    assert set(seen_source_ids) == {"yuantus-plm"}


def test_phase0_fails_when_replay_is_not_unchanged(tmp_path: Path) -> None:
    smoke = _load_script()
    sample = tmp_path / "gear.step"
    sample.write_bytes(b"phase0-cad")
    config = smoke.config_from_env(_env(sample), prefix="phase0-test")
    folder_id = "00000000-0000-0000-0000-000000000201"
    doc_id = "00000000-0000-0000-0000-000000000301"
    document_calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal document_calls
        if req.url.path.endswith("/verify"):
            return httpx.Response(200, json={"repositoryId": "athena"})
        if req.url.path.endswith("/folders"):
            return httpx.Response(201, json={"folderId": folder_id, "disposition": "CREATED"})
        document_calls += 1
        return httpx.Response(
            201,
            json={"documentId": doc_id, "documentName": "gear.step", "disposition": "CREATED"},
        )

    with pytest.raises(smoke.SmokeFailure, match="U4.replay-unchanged"):
        smoke.run_phase0(config, transport=httpx.MockTransport(handler))


def test_phase0_fails_when_verify_repository_is_unexpected(tmp_path: Path) -> None:
    smoke = _load_script()
    sample = tmp_path / "gear.step"
    sample.write_bytes(b"phase0-cad")
    config = smoke.config_from_env(_env(sample), prefix="phase0-test")

    with pytest.raises(smoke.SmokeFailure, match="unexpected repositoryId"):
        smoke.run_phase0(
            config,
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(200, json={"repositoryId": "wrong"})
            ),
        )
