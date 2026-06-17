#!/usr/bin/env python3
"""Live Phase-0 smoke for ECM publish via Athena Transfer Receiver.

Default mode is a dry-run plan that performs no network I/O. Pass
``--yes-live`` only when a disposable controlled file and live Athena receiver
credentials are available.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, TextIO

import httpx

_TRANSFER_USER_HEADER = "X-Athena-Transfer-User"
_TRANSFER_SECRET_HEADER = "X-Athena-Transfer-Secret"
_NAMESPACE_PLM = uuid.uuid5(uuid.NAMESPACE_URL, "yuantus-plm:ecm-publication")
_SUCCESS_DISPOSITIONS = {"CREATED", "RENAMED", "OVERWRITTEN", "UNCHANGED", "SKIPPED"}


class SmokeFailure(RuntimeError):
    """Raised when a live smoke step fails."""


@dataclass(frozen=True)
class SmokeConfig:
    base_url: str
    transfer_user: str
    transfer_secret: str
    root_folder_id: str
    expected_repository_id: str
    source_repository_id: str
    conflict_policy: str
    file_path: Path | None
    prefix: str
    source_last_modified_at: str
    timeout_s: float = 30.0


def _env_value(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        for candidate in (f"YUANTUS_{name}", name):
            value = env.get(candidate)
            if value and value.strip():
                return value.strip()
    return ""


def _default_prefix(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"phase0-{stamp}"


def _local_timestamp(now: datetime | None = None) -> str:
    dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0, tzinfo=None
    )
    return dt.isoformat(timespec="seconds")


def config_from_env(
    env: Mapping[str, str] | None = None,
    *,
    file_path: str | None = None,
    prefix: str | None = None,
    now: datetime | None = None,
    timeout_s: float | None = None,
) -> SmokeConfig:
    source = env or os.environ
    configured_file = file_path or _env_value(source, "PUBLICATION_ECM_PHASE0_FILE")
    timeout_value = timeout_s
    if timeout_value is None:
        text = _env_value(source, "PUBLICATION_ECM_TIMEOUT_SECONDS")
        timeout_value = float(text) if text else 30.0
    return SmokeConfig(
        base_url=_env_value(source, "PUBLICATION_ECM_BASE_URL", "ATHENA_BASE_URL").rstrip("/"),
        transfer_user=_env_value(source, "PUBLICATION_ECM_TRANSFER_USER"),
        transfer_secret=_env_value(source, "PUBLICATION_ECM_TRANSFER_SECRET"),
        root_folder_id=_env_value(source, "PUBLICATION_ECM_ROOT_FOLDER_ID"),
        expected_repository_id=(
            _env_value(source, "PUBLICATION_ECM_EXPECTED_REPOSITORY_ID") or "athena"
        ),
        source_repository_id=_env_value(source, "PUBLICATION_ECM_SOURCE_REPOSITORY_ID")
        or "yuantus-plm",
        conflict_policy=(
            _env_value(source, "PUBLICATION_ECM_CONFLICT_POLICY") or "SKIP"
        ).upper(),
        file_path=Path(configured_file).expanduser() if configured_file else None,
        prefix=prefix or _env_value(source, "PUBLICATION_ECM_PHASE0_PREFIX") or _default_prefix(now),
        source_last_modified_at=_local_timestamp(now),
        timeout_s=timeout_value,
    )


def missing_live_inputs(config: SmokeConfig) -> list[str]:
    missing: list[str] = []
    if not config.base_url:
        missing.append("YUANTUS_PUBLICATION_ECM_BASE_URL or YUANTUS_ATHENA_BASE_URL")
    if not config.transfer_user:
        missing.append("YUANTUS_PUBLICATION_ECM_TRANSFER_USER")
    if not config.transfer_secret:
        missing.append("YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET")
    if not config.root_folder_id:
        missing.append("YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID")
    if not config.file_path:
        missing.append("YUANTUS_PUBLICATION_ECM_PHASE0_FILE or --file")
    elif not config.file_path.is_file():
        missing.append(f"readable file: {config.file_path}")
    return missing


def _source_uuid(*parts: object) -> str:
    return str(uuid.uuid5(_NAMESPACE_PLM, "|".join(str(p or "") for p in parts)))


def _folder_source_uuid(*parts: object) -> str:
    return str(uuid.uuid5(_NAMESPACE_PLM, "folder|" + "|".join(str(p or "") for p in parts)))


def _headers(config: SmokeConfig, *, json_body: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        _TRANSFER_USER_HEADER: config.transfer_user,
        _TRANSFER_SECRET_HEADER: config.transfer_secret,
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _json_response(response: httpx.Response, step: str, config: SmokeConfig) -> dict:
    if not (200 <= response.status_code < 300):
        text = response.text.replace(config.transfer_secret, "<redacted>")
        raise SmokeFailure(f"{step} returned HTTP {response.status_code}: {text[:500]}")
    try:
        body = response.json()
    except Exception as exc:  # noqa: BLE001 - smoke should report parse failures plainly.
        raise SmokeFailure(f"{step} response is not JSON") from exc
    if not isinstance(body, dict):
        raise SmokeFailure(f"{step} response is not a JSON object")
    return body


def _require_uuid(value: object, field_name: str, step: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except Exception as exc:
        raise SmokeFailure(f"{step} response field {field_name} is not a UUID") from exc


def _verify_root(client: httpx.Client, config: SmokeConfig) -> dict:
    response = client.get(
        "/api/v1/transfer/receiver/verify",
        params={"folderId": config.root_folder_id},
        headers=_headers(config),
    )
    body = _json_response(response, "U1 verify", config)
    repository_id = body.get("repositoryId")
    if repository_id != config.expected_repository_id:
        raise SmokeFailure(
            "U1 verify returned unexpected repositoryId "
            f"{repository_id!r}; expected {config.expected_repository_id!r}"
        )
    return {"id": "U1", "status": "passed", "repository_id": repository_id}


def _ensure_folder(
    client: httpx.Client,
    config: SmokeConfig,
    *,
    step: str,
    parent_folder_id: str,
    name: str,
    source_node_id: str,
    source_parent_node_id: str | None = None,
) -> tuple[str, dict]:
    body = {
        "parentFolderId": parent_folder_id,
        "name": name,
        "description": f"Yuantus PLM ECM Phase-0 smoke folder {name}",
        "conflictPolicy": config.conflict_policy,
        "sourceRepositoryId": config.source_repository_id,
        "sourceNodeId": source_node_id,
        "sourceLastModifiedAt": config.source_last_modified_at,
    }
    if source_parent_node_id:
        body["sourceParentNodeId"] = source_parent_node_id
    response = client.post(
        "/api/v1/transfer/receiver/folders",
        json=body,
        headers=_headers(config, json_body=True),
    )
    payload = _json_response(response, step, config)
    folder_id = _require_uuid(payload.get("folderId"), "folderId", step)
    return folder_id, {
        "id": step,
        "status": "passed",
        "folder_id": folder_id,
        "disposition": payload.get("disposition"),
    }


def _upload_document(
    client: httpx.Client,
    config: SmokeConfig,
    *,
    step: str,
    parent_folder_id: str,
    version_id: str,
    file_id: str,
    file_role: str,
    content: bytes,
    expected_disposition: str,
) -> tuple[str, dict]:
    if config.file_path is None:
        raise SmokeFailure("missing phase-0 file")
    filename = config.file_path.name
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    source_node_id = _source_uuid(config.prefix, version_id, file_id, file_role)
    source_parent_node_id = _folder_source_uuid(config.prefix, version_id)
    response = client.post(
        "/api/v1/transfer/receiver/documents",
        data={
            "parentFolderId": parent_folder_id,
            "description": (
                "Yuantus PLM ECM Phase-0 smoke "
                f"prefix={config.prefix} version={version_id}"
            ),
            "conflictPolicy": config.conflict_policy,
            "sourceRepositoryId": config.source_repository_id,
            "sourceNodeId": source_node_id,
            "sourceParentNodeId": source_parent_node_id,
            "sourceLastModifiedAt": config.source_last_modified_at,
        },
        files={"file": (filename, content, mime_type)},
        headers=_headers(config),
    )
    payload = _json_response(response, step, config)
    document_id = _require_uuid(payload.get("documentId"), "documentId", step)
    disposition = str(payload.get("disposition") or "").upper()
    if disposition not in _SUCCESS_DISPOSITIONS:
        raise SmokeFailure(f"{step} returned invalid disposition {disposition!r}")
    if disposition != expected_disposition:
        raise SmokeFailure(
            f"{step} returned disposition {disposition!r}; expected {expected_disposition!r}"
        )
    return document_id, {
        "id": step,
        "status": "passed",
        "document_id": document_id,
        "disposition": disposition,
        "source_node_id": source_node_id,
    }


def run_phase0(
    config: SmokeConfig,
    *,
    transport: httpx.BaseTransport | None = None,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> dict:
    missing = missing_live_inputs(config)
    if missing:
        raise SmokeFailure("missing live inputs: " + ", ".join(missing))
    assert config.file_path is not None  # for type checkers; missing_live_inputs checked it.
    content = config.file_path.read_bytes()
    steps: list[dict] = []
    item_id = f"{config.prefix}-item"
    version_id = f"{config.prefix}-v1"
    version2_id = f"{config.prefix}-v2"
    file_id = f"{config.prefix}-file"
    file_role = "phase0_controlled_file"

    kwargs = {"base_url": config.base_url, "timeout": config.timeout_s}
    if transport is not None:
        kwargs["transport"] = transport
    with client_factory(**kwargs) as client:
        steps.append(_verify_root(client, config))
        _item_folder_id, step = _ensure_folder(
            client,
            config,
            step="U2.item-folder",
            parent_folder_id=config.root_folder_id,
            name=item_id,
            source_node_id=_folder_source_uuid(config.prefix),
        )
        steps.append(step)
        _version_folder_id, step = _ensure_folder(
            client,
            config,
            step="U2.version-folder",
            parent_folder_id=config.root_folder_id,
            name=version_id,
            source_node_id=_folder_source_uuid(config.prefix, version_id),
            source_parent_node_id=_folder_source_uuid(config.prefix),
        )
        steps.append(step)
        # Receiver scope is checked against parentFolderId before
        # sourceParentNodeId is resolved. Keep parentFolderId at the receiver
        # root and let sourceParentNodeId map the actual version folder.
        document_id, step = _upload_document(
            client,
            config,
            step="U3.document-created",
            parent_folder_id=config.root_folder_id,
            version_id=version_id,
            file_id=file_id,
            file_role=file_role,
            content=content,
            expected_disposition="CREATED",
        )
        steps.append(step)
        replay_document_id, step = _upload_document(
            client,
            config,
            step="U4.replay-unchanged",
            parent_folder_id=config.root_folder_id,
            version_id=version_id,
            file_id=file_id,
            file_role=file_role,
            content=content,
            expected_disposition="UNCHANGED",
        )
        steps.append(step)
        _version2_folder_id, step = _ensure_folder(
            client,
            config,
            step="U5.version2-folder",
            parent_folder_id=config.root_folder_id,
            name=version2_id,
            source_node_id=_folder_source_uuid(config.prefix, version2_id),
            source_parent_node_id=_folder_source_uuid(config.prefix),
        )
        steps.append(step)
        version2_document_id, step = _upload_document(
            client,
            config,
            step="U5.version2-created",
            parent_folder_id=config.root_folder_id,
            version_id=version2_id,
            file_id=file_id,
            file_role=file_role,
            content=content,
            expected_disposition="CREATED",
        )
        steps.append(step)

    return {
        "status": "passed",
        "prefix": config.prefix,
        "source_repository_id": config.source_repository_id,
        "root_folder_id": config.root_folder_id,
        "source_last_modified_at": config.source_last_modified_at,
        "documents": {
            "v1": document_id,
            "v1_replay": replay_document_id,
            "v2": version2_document_id,
        },
        "steps": steps,
    }


def dry_run_plan(config: SmokeConfig) -> dict:
    return {
        "status": "dry_run",
        "network_io": False,
        "missing_live_inputs": missing_live_inputs(config),
        "prefix": config.prefix,
        "base_url_configured": bool(config.base_url),
        "root_folder_id_configured": bool(config.root_folder_id),
        "source_repository_id": config.source_repository_id,
        "conflict_policy": config.conflict_policy,
        "would_run": [
            "U1 verify receiver root folder",
            "U2 ensure item and version folders",
            "U3 upload controlled file and expect CREATED",
            "U4 replay same sourceNodeId/watermark and expect UNCHANGED",
            "U5 upload new version identity and expect CREATED",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run ECM Publish Phase-0 live smoke against Athena Transfer Receiver."
    )
    parser.add_argument("--yes-live", action="store_true", help="perform live Athena I/O")
    parser.add_argument("--file", help="controlled sample file for U3/U4/U5")
    parser.add_argument("--prefix", help="unique source identity prefix for this smoke run")
    parser.add_argument("--timeout", type=float, help="HTTP timeout in seconds")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = _parser().parse_args(argv)
    config = config_from_env(
        env,
        file_path=args.file,
        prefix=args.prefix,
        timeout_s=args.timeout,
    )
    if not args.yes_live:
        print(json.dumps(dry_run_plan(config), indent=2, sort_keys=True), file=out)
        return 0
    try:
        result = run_phase0(config)
    except SmokeFailure as exc:
        print(
            json.dumps({"status": "failed", "error": str(exc)}, indent=2, sort_keys=True),
            file=err,
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
