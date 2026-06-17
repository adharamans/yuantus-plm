"""Athena Transfer Receiver publication connector (ECM-P1D retarget).

This is the production-path adapter selected by the P1D retarget taskbook. It
publishes one released controlled file into Athena's system-to-system Transfer
Receiver surface:

* ``build_payload`` and ``validate_contract`` are local only (dry-run safe).
* ``send`` is the only method that reads file bytes and performs network I/O.
* The receiver-side idempotency key is folded into a deterministic UUID
  ``sourceNodeId`` derived from the PLM per-file identity.

The older CMIS adapter remains in-tree as a compliance reference but the
resolver targets this Transfer Receiver adapter.
"""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from yuantus.config import get_settings
from yuantus.integrations.athena import is_athena_breaker_failure
from yuantus.integrations.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    get_or_create_breaker,
)
from yuantus.meta_engine.ecm_publication.adapter import (
    EcmPublicationAdapter,
    SendResult,
    ValidationResult,
)
from yuantus.meta_engine.services.file_service import FileService

_BREAKER_NAME = "publication_ecm"
_COUNT_STATUS = {408, 429}
_IDENTITY_FIELDS = ("item_id", "version_id", "file_id", "file_role", "target_system")
_TRANSFER_USER_HEADER = "X-Athena-Transfer-User"
_TRANSFER_SECRET_HEADER = "X-Athena-Transfer-Secret"
_NAMESPACE_PLM = uuid.uuid5(uuid.NAMESPACE_URL, "yuantus-plm:ecm-publication")
_ALLOWED_CONFLICT_POLICIES = {"SKIP", "RENAME", "OVERWRITE"}
_SENT_DISPOSITIONS = {"CREATED", "RENAMED", "OVERWRITTEN", "UNCHANGED", "SKIPPED"}
_SENTINEL_WATERMARK = "1970-01-01T00:00:00"


class _PayloadProblem(ValueError):
    pass


@dataclass
class _HttpProblem(RuntimeError):
    status_code: int
    message: str = ""


def build_publication_ecm_transfer_breaker() -> CircuitBreaker:
    return get_or_create_breaker(
        CircuitBreakerConfig(
            name=_BREAKER_NAME, enabled=True, is_failure=is_athena_breaker_failure
        )
    )


def build_transfer_source_node_id(snapshot: dict) -> str:
    """Fold the PLM per-file identity into Athena's single UUID discriminator."""

    basis = "|".join(
        str(snapshot.get(k) or "")
        for k in ("item_id", "version_id", "file_id", "file_role")
    )
    return str(uuid.uuid5(_NAMESPACE_PLM, basis))


def _folder_source_id(*parts: object) -> str:
    return str(uuid.uuid5(_NAMESPACE_PLM, "folder|" + "|".join(str(p or "") for p in parts)))


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _strip(value: object) -> str:
    return str(value or "").strip()


def _uuid(value: object, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except Exception as exc:
        raise _PayloadProblem(f"{field_name} must be a UUID") from exc


def _local_datetime(value: object) -> str:
    if value is None:
        raise _PayloadProblem("missing released_at")
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.isoformat(timespec="seconds")
    text = str(value).strip()
    if not text:
        raise _PayloadProblem("missing released_at")
    # Athena expects LocalDateTime. Accept already-local strings and normalize a
    # trailing UTC marker to a naive UTC string.
    if text.endswith("Z"):
        text = text[:-1]
    if "+" in text:
        text = text.split("+", 1)[0]
    return text


class AthenaTransferReceiverAdapter(EcmPublicationAdapter):
    def __init__(
        self,
        *,
        settings: Any = None,
        transport: Optional[httpx.BaseTransport] = None,
        breaker: Optional[CircuitBreaker] = None,
        file_service: Optional[FileService] = None,
    ) -> None:
        s = settings or get_settings()
        self.base_url = (
            getattr(s, "PUBLICATION_ECM_BASE_URL", "")
            or getattr(s, "ATHENA_BASE_URL", "")
            or ""
        ).rstrip("/")
        self.target_system = _strip(getattr(s, "PUBLICATION_ECM_TARGET_SYSTEM", ""))
        self.timeout_s = float(getattr(s, "PUBLICATION_ECM_TIMEOUT_SECONDS", 30.0) or 30.0)
        self.transfer_user = _strip(getattr(s, "PUBLICATION_ECM_TRANSFER_USER", ""))
        self.transfer_secret = _strip(getattr(s, "PUBLICATION_ECM_TRANSFER_SECRET", ""))
        self.root_folder_id = _strip(getattr(s, "PUBLICATION_ECM_ROOT_FOLDER_ID", ""))
        self.source_repository_id = _strip(
            getattr(s, "PUBLICATION_ECM_SOURCE_REPOSITORY_ID", "")
        )
        self.conflict_policy = (
            _strip(getattr(s, "PUBLICATION_ECM_CONFLICT_POLICY", "SKIP")) or "SKIP"
        ).upper()
        self.max_bytes = int(getattr(s, "PUBLICATION_ECM_TRANSFER_MAX_BYTES", 0) or 0)
        self.allow_released_at_sentinel = _truthy(
            getattr(s, "PUBLICATION_ECM_ALLOW_RELEASED_AT_SENTINEL", False)
        )
        self._transport = transport
        self._breaker = breaker or build_publication_ecm_transfer_breaker()
        self._file_service = file_service or FileService()

    # -- local (no network) ---------------------------------------------
    def build_payload(self, snapshot: dict) -> dict:
        source_last_modified_at = None
        try:
            source_last_modified_at = _local_datetime(snapshot.get("released_at"))
        except _PayloadProblem:
            if self.allow_released_at_sentinel:
                source_last_modified_at = _SENTINEL_WATERMARK

        item_id = snapshot.get("item_id")
        version_id = snapshot.get("version_id")
        item_folder_source_id = _folder_source_id(item_id)
        version_folder_source_id = _folder_source_id(item_id, version_id)
        source_node_id = build_transfer_source_node_id(snapshot)
        filename = snapshot.get("filename") or snapshot.get("file_id")
        return {
            "target_system": snapshot.get("target_system"),
            "item_id": item_id,
            "version_id": version_id,
            "file_id": snapshot.get("file_id"),
            "file_role": snapshot.get("file_role"),
            "filename": filename,
            "mime_type": snapshot.get("mime_type") or "application/octet-stream",
            "file_size": snapshot.get("file_size"),
            "system_path": snapshot.get("system_path"),
            "released_at": snapshot.get("released_at"),
            "source_repository_id": self.source_repository_id,
            "source_node_id": source_node_id,
            "source_last_modified_at": source_last_modified_at,
            "root_folder_id": self.root_folder_id,
            "conflict_policy": self.conflict_policy,
            "description": (
                f"Yuantus PLM item={item_id} version={version_id} "
                f"file={snapshot.get('file_id')} role={snapshot.get('file_role')}"
            ),
            "folders": {
                "item": {
                    "name": str(item_id or ""),
                    "parent_folder_id": self.root_folder_id,
                    "source_node_id": item_folder_source_id,
                    "source_parent_node_id": None,
                    "source_last_modified_at": source_last_modified_at,
                },
                "version": {
                    "name": str(version_id or ""),
                    "parent_folder_id": None,  # filled from item-folder response
                    "source_node_id": version_folder_source_id,
                    "source_parent_node_id": item_folder_source_id,
                    "source_last_modified_at": source_last_modified_at,
                },
            },
        }

    def validate_contract(self, payload: dict) -> ValidationResult:
        errors = [f"missing {k}" for k in _IDENTITY_FIELDS if not payload.get(k)]
        for key in (
            "filename",
            "system_path",
            "root_folder_id",
            "source_repository_id",
            "source_node_id",
            "source_last_modified_at",
        ):
            if not payload.get(key):
                errors.append(f"missing {key}")
        if not self.transfer_secret:
            errors.append("missing transfer secret")
        if payload.get("conflict_policy") not in _ALLOWED_CONFLICT_POLICIES:
            errors.append("invalid conflict_policy")
        for key in ("root_folder_id", "source_node_id"):
            if payload.get(key):
                try:
                    _uuid(payload[key], key)
                except _PayloadProblem as exc:
                    errors.append(str(exc))
        folders = payload.get("folders") or {}
        for name in ("item", "version"):
            folder = folders.get(name) or {}
            if not folder.get("name"):
                errors.append(f"missing {name} folder name")
            if folder.get("source_node_id"):
                try:
                    _uuid(folder["source_node_id"], f"{name} folder source_node_id")
                except _PayloadProblem as exc:
                    errors.append(str(exc))
        file_size = payload.get("file_size")
        if self.max_bytes > 0 and file_size is not None:
            try:
                if int(file_size) > self.max_bytes:
                    errors.append("file_size exceeds PUBLICATION_ECM_TRANSFER_MAX_BYTES")
            except Exception:
                errors.append("invalid file_size")
        return ValidationResult(ok=not errors, errors=errors)

    # -- network (send only) --------------------------------------------
    def _client(self) -> httpx.Client:
        kwargs: dict = {
            "base_url": self.base_url or "http://athena.invalid",
            "timeout": self.timeout_s,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _headers(self, *, json: bool = False) -> dict:
        headers = {"Accept": "application/json"}
        if json:
            headers["Content-Type"] = "application/json"
        if self.transfer_user:
            headers[_TRANSFER_USER_HEADER] = self.transfer_user
        if self.transfer_secret:
            headers[_TRANSFER_SECRET_HEADER] = self.transfer_secret
        return headers

    def _post_json(self, path: str, payload: dict) -> dict:
        with self._client() as client:
            response = client.post(path, json=payload, headers=self._headers(json=True))
        if not (200 <= response.status_code < 300):
            raise _HttpProblem(response.status_code, response.text)
        return self._json_body(response)

    def _post_multipart(self, path: str, *, data: dict, files: dict) -> dict:
        with self._client() as client:
            response = client.post(path, data=data, files=files, headers=self._headers())
        if not (200 <= response.status_code < 300):
            raise _HttpProblem(response.status_code, response.text)
        return self._json_body(response)

    def _json_body(self, response: httpx.Response) -> dict:
        try:
            body = response.json()
        except Exception as exc:
            raise _PayloadProblem("Athena response is not JSON") from exc
        if not isinstance(body, dict):
            raise _PayloadProblem("Athena response is not an object")
        return body

    def _folder_body(self, folder: dict, parent_folder_id: str) -> dict:
        body = {
            "parentFolderId": _uuid(parent_folder_id, "parentFolderId"),
            "name": folder.get("name"),
            "description": folder.get("description") or None,
            "conflictPolicy": self.conflict_policy,
            "sourceRepositoryId": self.source_repository_id,
            "sourceNodeId": _uuid(folder.get("source_node_id"), "sourceNodeId"),
            "sourceLastModifiedAt": folder.get("source_last_modified_at"),
        }
        if folder.get("source_parent_node_id"):
            body["sourceParentNodeId"] = _uuid(
                folder.get("source_parent_node_id"), "sourceParentNodeId"
            )
        return body

    def _ensure_folder(self, folder: dict, parent_folder_id: str) -> str:
        body = self._folder_body(folder, parent_folder_id)
        response = self._post_json("/api/v1/transfer/receiver/folders", body)
        folder_id = response.get("folderId")
        if not folder_id:
            raise _PayloadProblem("Athena folder response missing folderId")
        return _uuid(folder_id, "folderId")

    def _read_content(self, payload: dict) -> bytes:
        buf = io.BytesIO()
        try:
            self._file_service.download_file(payload["system_path"], buf)
        except Exception as exc:
            raise httpx.TransportError(type(exc).__name__) from exc
        data = buf.getvalue()
        if self.max_bytes > 0 and len(data) > self.max_bytes:
            raise _PayloadProblem("downloaded file exceeds PUBLICATION_ECM_TRANSFER_MAX_BYTES")
        return data

    def _dispatch(self, payload: dict) -> SendResult:
        folders = payload.get("folders") or {}
        self._ensure_folder(folders.get("item") or {}, payload["root_folder_id"])
        version_folder = dict(folders.get("version") or {})
        # The Transfer Receiver authorizes the literal parentFolderId before it
        # resolves sourceParentNodeId. Keep the wire parent scoped to the receiver
        # root and let sourceParentNodeId resolve the actual item/version nesting.
        self._ensure_folder(version_folder, payload["root_folder_id"])
        content = self._read_content(payload)
        data = {
            "parentFolderId": payload["root_folder_id"],
            "description": payload.get("description") or "",
            "conflictPolicy": payload["conflict_policy"],
            "sourceRepositoryId": payload["source_repository_id"],
            "sourceNodeId": payload["source_node_id"],
            "sourceParentNodeId": version_folder["source_node_id"],
            "sourceLastModifiedAt": payload["source_last_modified_at"],
        }
        files = {
            "file": (
                payload["filename"],
                content,
                payload.get("mime_type") or "application/octet-stream",
            )
        }
        response = self._post_multipart(
            "/api/v1/transfer/receiver/documents", data=data, files=files
        )
        document_id = response.get("documentId")
        disposition = str(response.get("disposition") or "").upper()
        if not document_id:
            raise _PayloadProblem("Athena document response missing documentId")
        _uuid(document_id, "documentId")
        if disposition not in _SENT_DISPOSITIONS:
            raise _PayloadProblem("Athena document response has invalid disposition")
        return SendResult(
            ok=True,
            remote_id=str(document_id),
            properties={
                "athena_document_id": str(document_id),
                "athena_disposition": disposition,
                "athena_document_name": response.get("documentName"),
            },
        )

    def _classify_http(self, status_code: int) -> str:
        if status_code in _COUNT_STATUS or status_code >= 500 or 300 <= status_code < 400:
            return "remote_error"
        return "validation_error"

    def send(self, payload: dict) -> SendResult:
        try:
            return self._breaker.call_sync(self._dispatch, payload)
        except CircuitOpenError:
            return SendResult(
                ok=False, error="publication ECM circuit open", error_kind="remote_error"
            )
        except _HttpProblem as exc:
            return SendResult(
                ok=False,
                error=f"HTTP {exc.status_code}",
                error_kind=self._classify_http(exc.status_code),
            )
        except _PayloadProblem as exc:
            return SendResult(ok=False, error=str(exc), error_kind="validation_error")
        except httpx.RequestError as exc:
            return SendResult(ok=False, error=type(exc).__name__, error_kind="remote_error")
