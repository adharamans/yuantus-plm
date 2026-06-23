"""ECM-P1D retarget: Athena Transfer Receiver adapter.

All HTTP is mocked with ``httpx.MockTransport`` and all storage reads use a fake
FileService. These tests pin the real chosen surface from the P1D retarget
taskbook: folders + multipart documents with Transfer Receiver headers,
sourceNodeId folding, stable released_at watermark, and non-retryable receiver
contract/auth failures.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from yuantus.integrations.athena import is_athena_breaker_failure
from yuantus.integrations.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from yuantus.meta_engine.ecm_publication.adapter import NullEcmPublicationAdapter
from yuantus.meta_engine.ecm_publication.adapter_registry import resolve_adapter
from yuantus.meta_engine.ecm_publication.cmis_adapter import AthenaCmisPublicationAdapter
from yuantus.meta_engine.ecm_publication.transfer_receiver_adapter import (
    AthenaTransferReceiverAdapter,
    build_transfer_source_node_id,
)

_ROOT = "00000000-0000-0000-0000-000000000111"
_DOC = "00000000-0000-0000-0000-000000000222"
_ITEM_FOLDER = "00000000-0000-0000-0000-000000000333"
_VERSION_FOLDER = "00000000-0000-0000-0000-000000000444"

_SNAP = {
    "item_id": "P-001",
    "version_id": "v-1",
    "file_id": "f-1",
    "file_role": "native_cad",
    "target_system": "athena",
    "filename": "gear.step",
    "mime_type": "model/step",
    "file_size": 11,
    "system_path": "released/v-1/gear.step",
    "released_at": datetime(2026, 6, 16, 12, 30, 45),
}


class _FileService:
    def __init__(self, data: bytes = b"hello-athena", raises: Exception | None = None):
        self.data = data
        self.raises = raises
        self.paths: list[str] = []

    def download_file(self, path, output_file_obj):
        self.paths.append(path)
        if self.raises:
            raise self.raises
        output_file_obj.write(self.data)


def _settings(**over):
    base = dict(
        ATHENA_BASE_URL="http://athena",
        PUBLICATION_ECM_BASE_URL="",
        PUBLICATION_ECM_TARGET_SYSTEM="athena",
        PUBLICATION_ECM_TIMEOUT_SECONDS=5.0,
        PUBLICATION_ECM_TRANSFER_USER="plm",
        PUBLICATION_ECM_TRANSFER_SECRET="secret",
        PUBLICATION_ECM_ROOT_FOLDER_ID=_ROOT,
        PUBLICATION_ECM_SOURCE_REPOSITORY_ID="yuantus-plm",
        PUBLICATION_ECM_CONFLICT_POLICY="SKIP",
        PUBLICATION_ECM_TRANSFER_MAX_BYTES=1_000_000,
        PUBLICATION_ECM_ALLOW_RELEASED_AT_SENTINEL=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _breaker() -> CircuitBreaker:
    return CircuitBreaker(
        CircuitBreakerConfig(
            name="test-ecm-transfer",
            enabled=False,
            is_failure=is_athena_breaker_failure,
        )
    )


def _adapter(handler, *, settings=None, file_service=None):
    return AthenaTransferReceiverAdapter(
        settings=settings or _settings(),
        transport=httpx.MockTransport(handler),
        breaker=_breaker(),
        file_service=file_service or _FileService(),
    )


def _payload(settings=None):
    return AthenaTransferReceiverAdapter(
        settings=settings or _settings(), file_service=_FileService(), breaker=_breaker()
    ).build_payload(dict(_SNAP))


def _ok_handler(requests):
    def handler(req):
        requests.append(req)
        if req.url.path.endswith("/folders"):
            folder_id = _ITEM_FOLDER if len(requests) == 1 else _VERSION_FOLDER
            return httpx.Response(
                201,
                json={
                    "folderId": folder_id,
                    "folderName": "folder",
                    "disposition": "CREATED",
                },
            )
        return httpx.Response(
            201,
            json={
                "documentId": _DOC,
                "documentName": "gear.step",
                "disposition": "CREATED",
                "message": "Uploaded receiver document",
            },
        )

    return handler


def test_build_payload_folds_identity_into_stable_source_node_id():
    payload = _payload()
    assert payload["source_repository_id"] == "yuantus-plm"
    assert payload["source_node_id"] == build_transfer_source_node_id(_SNAP)
    assert payload["source_last_modified_at"] == "2026-06-16T12:30:45.000000"  # #848: microsecond watermark
    assert payload["conflict_policy"] == "SKIP"
    assert payload["root_folder_id"] == _ROOT
    assert (
        payload["folders"]["version"]["source_parent_node_id"]
        == payload["folders"]["item"]["source_node_id"]
    )

    # A1 (#848): source_node_id folds only (item_id, file_role) — STABLE across version + file,
    # changes only on role, so Athena revisions the same document in place (N+1 supersedes N).
    changed_version = dict(_SNAP, version_id="v-2")
    changed_file = dict(_SNAP, file_id="f-2")
    changed_role = dict(_SNAP, file_role="pdf")
    assert build_transfer_source_node_id(changed_version) == payload["source_node_id"]
    assert build_transfer_source_node_id(changed_file) == payload["source_node_id"]
    assert build_transfer_source_node_id(changed_role) != payload["source_node_id"]


def test_validate_contract_requires_released_at_unless_sentinel_enabled():
    missing = dict(_SNAP, released_at=None)
    no_sentinel = AthenaTransferReceiverAdapter(settings=_settings(), file_service=_FileService())
    assert (
        "missing source_last_modified_at"
        in no_sentinel.validate_contract(no_sentinel.build_payload(missing)).errors
    )

    with_sentinel = AthenaTransferReceiverAdapter(
        settings=_settings(PUBLICATION_ECM_ALLOW_RELEASED_AT_SENTINEL=True),
        file_service=_FileService(),
    )
    payload = with_sentinel.build_payload(missing)
    assert payload["source_last_modified_at"] == "1970-01-01T00:00:00"
    assert with_sentinel.validate_contract(payload).ok is True


@pytest.mark.parametrize(
    "over,missing",
    [
        ({"PUBLICATION_ECM_TRANSFER_SECRET": ""}, "missing transfer secret"),
        ({"PUBLICATION_ECM_ROOT_FOLDER_ID": ""}, "missing root_folder_id"),
        ({"PUBLICATION_ECM_SOURCE_REPOSITORY_ID": ""}, "missing source_repository_id"),
    ],
)
def test_validate_contract_requires_transfer_settings(over, missing):
    adapter = AthenaTransferReceiverAdapter(
        settings=_settings(**over), file_service=_FileService()
    )
    result = adapter.validate_contract(adapter.build_payload(dict(_SNAP)))
    assert result.ok is False
    assert missing in result.errors


def test_validate_contract_requires_system_path_and_size_cap():
    adapter = AthenaTransferReceiverAdapter(
        settings=_settings(PUBLICATION_ECM_TRANSFER_MAX_BYTES=10),
        file_service=_FileService(),
    )
    payload = adapter.build_payload(dict(_SNAP, system_path="", file_size=11))
    result = adapter.validate_contract(payload)
    assert "missing system_path" in result.errors
    assert "file_size exceeds PUBLICATION_ECM_TRANSFER_MAX_BYTES" in result.errors


def test_send_creates_item_version_folders_then_uploads_document():
    requests = []
    files = _FileService()
    result = _adapter(_ok_handler(requests), file_service=files).send(_payload())
    assert result.ok is True
    assert result.remote_id == _DOC
    assert result.properties["athena_document_id"] == _DOC
    assert result.properties["athena_disposition"] == "CREATED"
    assert files.paths == ["released/v-1/gear.step"]

    assert [r.url.path for r in requests] == [
        "/api/v1/transfer/receiver/folders",
        "/api/v1/transfer/receiver/folders",
        "/api/v1/transfer/receiver/documents",
    ]
    first_folder = json.loads(requests[0].content.decode("utf-8"))
    second_folder = json.loads(requests[1].content.decode("utf-8"))
    assert first_folder["parentFolderId"] == _ROOT
    assert first_folder["conflictPolicy"] == "SKIP"
    assert first_folder["sourceRepositoryId"] == "yuantus-plm"
    # Wire parent stays at the receiver root for scope authorization; Athena
    # resolves the actual nested parent from sourceParentNodeId mapping.
    assert second_folder["parentFolderId"] == _ROOT
    assert second_folder["sourceParentNodeId"] == first_folder["sourceNodeId"]

    doc = requests[2]
    headers = {k.lower(): v for k, v in doc.headers.items()}
    assert headers["x-athena-transfer-user"] == "plm"
    assert headers["x-athena-transfer-secret"] == "secret"
    assert "authorization" not in headers
    body = doc.content.decode("latin-1")
    assert 'name="parentFolderId"\r\n\r\n' + _ROOT in body
    assert 'name="conflictPolicy"' in body and "SKIP" in body
    assert 'name="sourceLastModifiedAt"' in body and "2026-06-16T12:30:45" in body
    assert 'filename="gear.step"' in body and "hello-athena" in body


@pytest.mark.parametrize("disposition", ["CREATED", "UNCHANGED", "SKIPPED", "OVERWRITTEN"])
def test_success_dispositions_are_sent(disposition):
    def handler(req):
        if req.url.path.endswith("/folders"):
            return httpx.Response(201, json={"folderId": _ITEM_FOLDER, "disposition": "CREATED"})
        return httpx.Response(201, json={"documentId": _DOC, "disposition": disposition})

    result = _adapter(handler).send(_payload())
    assert result.ok is True
    assert result.properties["athena_disposition"] == disposition


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 422])
def test_receiver_client_and_scope_failures_are_terminal_validation_error(code):
    result = _adapter(lambda req: httpx.Response(code, json={"error": "x"})).send(_payload())
    assert result.ok is False
    assert result.error_kind == "validation_error"


@pytest.mark.parametrize("code", [301, 302, 408, 429, 500, 502, 503])
def test_remote_failures_are_retryable_remote_error(code):
    result = _adapter(lambda req: httpx.Response(code, json={"error": "x"})).send(_payload())
    assert result.ok is False
    assert result.error_kind == "remote_error"


def test_timeout_and_storage_read_failures_are_remote_error():
    def timeout(req):
        raise httpx.ConnectTimeout("t", request=req)

    assert _adapter(timeout).send(_payload()).error_kind == "remote_error"
    result = _adapter(
        _ok_handler([]), file_service=_FileService(raises=OSError("gone"))
    ).send(_payload())
    assert result.ok is False
    assert result.error_kind == "remote_error"


@pytest.mark.parametrize(
    "body",
    [
        {"documentId": "", "disposition": "CREATED"},
        {"documentId": _DOC, "disposition": "BOGUS"},
    ],
)
def test_malformed_success_response_is_terminal_validation_error(body):
    def handler(req):
        if req.url.path.endswith("/folders"):
            return httpx.Response(201, json={"folderId": _ITEM_FOLDER, "disposition": "CREATED"})
        return httpx.Response(201, json=body)

    result = _adapter(handler).send(_payload())
    assert result.ok is False
    assert result.error_kind == "validation_error"


def test_resolver_configured_match_is_transfer_receiver_not_cmis():
    adapter = resolve_adapter("athena", settings=_settings())
    assert isinstance(adapter, AthenaTransferReceiverAdapter)
    assert not isinstance(adapter, AthenaCmisPublicationAdapter)


def test_resolver_unconfigured_and_no_base_stay_null():
    assert isinstance(
        resolve_adapter("athena", settings=_settings(PUBLICATION_ECM_TARGET_SYSTEM="")),
        NullEcmPublicationAdapter,
    )
    assert isinstance(
        resolve_adapter(
            "athena",
            settings=_settings(PUBLICATION_ECM_BASE_URL="", ATHENA_BASE_URL=""),
        ),
        NullEcmPublicationAdapter,
    )
