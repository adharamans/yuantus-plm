from __future__ import annotations

import base64
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from yuantus.config import (
    cad_connector_base_url_configured,
    cad_connector_enabled_for_profile,
    effective_cad_step_iges_backend,
    get_settings,
    normalize_cad_connector_mode,
)
from yuantus.integrations.cad_ml import CadMLClient
from yuantus.integrations.render_service import RenderServiceClient
from yuantus.integrations.dedup_vision import DedupVisionClient
from yuantus.integrations.cad_connector import CadConnectorClient
from yuantus.meta_engine.dedup.service import DedupService
from yuantus.meta_engine.models.file import ConversionStatus, FileContainer
from yuantus.meta_engine.services.cad_converter_service import CADConverterService
from yuantus.meta_engine.services.cadgf_converter_service import (
    CADGFConverterService,
    CadgfConversionError,
)
from yuantus.meta_engine.services.cad_backend_profile_service import (
    CadBackendProfileService,
)
from yuantus.meta_engine.services.cad_service import CadService, normalize_cad_attributes
from yuantus.meta_engine.services.cad_bom_import_service import CadBomImportService
from yuantus.meta_engine.services.file_service import FileService
from yuantus.meta_engine.services.job_errors import JobFatalError
from yuantus.context import get_request_context

logger = logging.getLogger(__name__)

_STEP_IGES_EXTENSIONS = {"step", "stp", "iges", "igs"}
_STEP_IGES_FORMATS = {"step", "iges"}


def _vault_base_path() -> str:
    return str(Path(get_settings().LOCAL_STORAGE_PATH).resolve())


def _is_s3_storage() -> bool:
    """Check if storage type is S3."""
    return get_settings().STORAGE_TYPE == "s3"


def _build_authorization_header(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    token = str(token).strip()
    if not token:
        return None
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def _parse_png_size(data: bytes) -> tuple[Optional[int], Optional[int]]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    if data[12:16] != b"IHDR":
        return None, None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def _create_minimal_png_bytes(width: int, height: int) -> bytes:
    import struct
    import zlib

    width = max(1, int(width))
    height = max(1, int(height))
    row = b"\x00" + (b"\x80\x80\x80" * width)
    raw = row * height
    compressed = zlib.compress(raw)

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        length = struct.pack(">I", len(payload))
        crc = struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        return length + tag + payload + crc

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", ihdr),
            _chunk(b"IDAT", compressed),
            _chunk(b"IEND", b""),
        ]
    )


def _ensure_preview_min_size(
    preview_bytes: bytes, *, min_size: int, label: str = "preview"
) -> bytes:
    if not preview_bytes or min_size <= 0:
        return preview_bytes

    try:
        from PIL import Image

        with Image.open(io.BytesIO(preview_bytes)) as img:
            width, height = img.size
            if width >= min_size and height >= min_size:
                return preview_bytes
            scale = max(min_size / width, min_size / height)
            new_size = (
                max(min_size, int(round(width * scale))),
                max(min_size, int(round(height * scale))),
            )
            resized = img.convert("RGB").resize(new_size, resample=Image.BICUBIC)
            out = io.BytesIO()
            resized.save(out, format="PNG")
            return out.getvalue()
    except Exception as exc:
        logger.warning("Failed to resize %s preview: %s", label, exc)

    width, height = _parse_png_size(preview_bytes)
    if width and height and (width < min_size or height < min_size):
        return _create_minimal_png_bytes(min_size, min_size)
    return preview_bytes


def _preview_meets_min_size(preview_bytes: Optional[bytes], *, min_size: int) -> bool:
    if not preview_bytes or min_size <= 0:
        return False
    try:
        from PIL import Image

        with Image.open(io.BytesIO(preview_bytes)) as img:
            width, height = img.size
            return width >= min_size and height >= min_size
    except Exception:
        width, height = _parse_png_size(preview_bytes)
        if width and height:
            return width >= min_size and height >= min_size
    return False


def _load_preview_bytes(
    file_container: FileContainer, file_service: FileService
) -> Optional[bytes]:
    if file_container.preview_data:
        try:
            return base64.b64decode(file_container.preview_data)
        except Exception:
            return None

    preview_path = file_container.preview_path
    if not preview_path:
        return None
    try:
        local_path = file_service.get_local_path(preview_path)
        if local_path and os.path.exists(local_path):
            with open(local_path, "rb") as handle:
                return handle.read()
    except Exception:
        pass
    try:
        buf = io.BytesIO()
        file_service.download_file(preview_path, buf)
        return buf.getvalue()
    except Exception:
        return None


def _cad_connector_mode() -> str:
    return normalize_cad_connector_mode(get_settings().CAD_CONNECTOR_MODE)


def _cad_backend_profile_resolution(
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    ctx = get_request_context()
    tenant_id = (ctx.tenant_id or "").strip()
    if not tenant_id:
        raise JobFatalError(
            "CAD backend profile resolution requires tenant context; "
            "check job payload includes tenant_id/org_id"
        )
    resolution = CadBackendProfileService(session, get_settings()).resolve(
        tenant_id=tenant_id,
        org_id=ctx.org_id,
    )
    return {
        "configured": resolution.configured,
        "effective": resolution.effective,
        "source": resolution.source,
        "scope": resolution.scope,
    }


def _cad_connector_enabled(session: Optional[Session] = None) -> bool:
    effective = _cad_backend_profile_resolution(session)["effective"]
    return effective != "local-baseline" and cad_connector_base_url_configured(get_settings())


def _cad_connector_required(session: Optional[Session] = None) -> bool:
    return _cad_backend_profile_resolution(session)["effective"] == "external-enterprise"


def _is_step_iges_file(file_container: FileContainer) -> bool:
    ext = ""
    try:
        ext = (file_container.get_extension() or "").strip().lower()
    except Exception:
        filename = str(getattr(file_container, "filename", "") or "")
        ext = Path(filename).suffix.lower().lstrip(".")
    cad_format = str(getattr(file_container, "cad_format", "") or "").strip().lower()
    return ext in _STEP_IGES_EXTENSIONS or cad_format in _STEP_IGES_FORMATS


def _cad_step_iges_backend(
    file_container: FileContainer, *, session: Optional[Session] = None
) -> Optional[str]:
    if not _is_step_iges_file(file_container):
        return None
    resolution = _cad_backend_profile_resolution(session)
    return effective_cad_step_iges_backend(
        get_settings(), effective_profile=resolution["effective"]
    )


def _cad_connector_enabled_for_file(
    file_container: FileContainer, *, session: Optional[Session] = None
) -> bool:
    step_iges_backend = _cad_step_iges_backend(file_container, session=session)
    if step_iges_backend == "local":
        return False
    return _cad_connector_enabled(session)


def _require_connector_for_remote_3d(
    file_container: FileContainer, *, operation: str, session: Optional[Session] = None
) -> None:
    if file_container.document_type != "3d":
        return
    if _cad_step_iges_backend(file_container, session=session) == "local":
        return
    if not _cad_connector_required(session):
        return
    if _cad_connector_enabled(session):
        return
    profile_name = _cad_backend_profile_resolution(session)["effective"]
    raise JobFatalError(
        f"CAD connector {operation} requires configured connector under backend profile {profile_name}"
    )


def _resolve_connector_authorization(payload: Dict[str, Any]) -> Optional[str]:
    settings = get_settings()
    auth = payload.get("authorization") or payload.get("connector_authorization")
    if not auth:
        auth = settings.CAD_CONNECTOR_SERVICE_TOKEN
    return _build_authorization_header(auth)


def _prepare_connector_source(
    file_service: FileService, file_container: FileContainer
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (file_url, file_path, temp_path). If file_url is present, file_path may be None.
    """
    settings = get_settings()
    file_url = None
    try:
        file_url = file_service.get_presigned_url(
            file_container.system_path,
            expiration=max(int(settings.CAD_CONNECTOR_TIMEOUT_SECONDS or 60), 30),
            http_method="GET",
        )
    except Exception:
        file_url = None

    if file_url:
        return file_url, None, None

    local_path = file_service.get_local_path(file_container.system_path)
    if local_path and os.path.exists(local_path):
        return None, local_path, None

    suffix = Path(file_container.filename or "").suffix
    temp_path = _download_to_temp(
        file_service,
        file_container.system_path,
        suffix=suffix,
    )
    return None, temp_path, temp_path


def _download_artifact(
    url: str, file_service: FileService, storage_key: str
) -> str:
    import httpx

    with httpx.Client(timeout=max(int(get_settings().CAD_CONNECTOR_TIMEOUT_SECONDS or 60), 10)) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return file_service.upload_file(io.BytesIO(resp.content), storage_key)


def _call_cad_connector_convert(
    *,
    payload: Dict[str, Any],
    file_container: FileContainer,
    file_service: FileService,
    mode: str,
) -> Dict[str, Any]:
    settings = get_settings()
    if not settings.CAD_CONNECTOR_BASE_URL:
        raise JobFatalError("CAD connector not configured")

    file_url, file_path, temp_path = _prepare_connector_source(file_service, file_container)
    ctx = get_request_context()
    authorization = _resolve_connector_authorization(payload)
    client = CadConnectorClient(timeout_s=settings.CAD_CONNECTOR_TIMEOUT_SECONDS)
    try:
        resp = client.convert_sync(
            file_path=file_path,
            file_url=file_url,
            filename=file_container.filename,
            cad_format=file_container.cad_format,
            cad_connector_id=file_container.cad_connector_id,
            mode=mode,
            tenant_id=ctx.tenant_id,
            org_id=ctx.org_id,
            authorization=authorization,
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

    if isinstance(resp, dict) and resp.get("ok") is False:
        raise JobFatalError(resp.get("error") or "CAD connector failed")
    if not isinstance(resp, dict):
        raise JobFatalError("CAD connector returned invalid payload")
    return resp

def _is_missing_storage_error(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code", "")
        if str(code) in {"404", "NoSuchKey", "NotFound"}:
            return True
    return False


def _ensure_source_exists(file_service: FileService, system_path: str) -> None:
    if not system_path:
        raise JobFatalError("Missing system_path for source file")
    exists = file_service.file_exists(system_path)
    if not exists:
        raise JobFatalError(f"Source file missing: {system_path}")


def _download_to_temp(file_service: FileService, system_path: str, suffix: str = "") -> str:
    """
    Download a file from storage to a temporary local file.
    Returns the path to the temporary file.
    """
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        file_service.download_file(system_path, temp_file)
        temp_file.close()
        return temp_file.name
    except Exception as exc:
        temp_file.close()
        os.unlink(temp_file.name)
        if _is_missing_storage_error(exc):
            raise JobFatalError(f"Source file missing: {system_path}") from exc
        raise


def _upload_from_local(file_service: FileService, local_path: str, storage_key: str) -> str:
    """
    Upload a local file to storage.
    Returns the storage key.
    """
    with open(local_path, "rb") as f:
        return file_service.upload_file(f, storage_key)


def _cadgf_storage_prefix(file_id: str) -> str:
    return f"cadgf/{file_id[:2]}/{file_id}"


def _cadgf_output_dir(vault_base_path: str, file_id: str) -> str:
    return os.path.join(vault_base_path, _cadgf_storage_prefix(file_id))


def _parse_schema_version(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _read_cadgf_document_schema_version(
    manifest_path: Path, document_path: Optional[Path]
) -> Optional[int]:
    candidates = [
        (manifest_path, "document_schema_version"),
        (document_path, "schema_version"),
    ]
    for path, key in candidates:
        if not path or not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            continue
        if isinstance(data, dict):
            version = _parse_schema_version(data.get(key))
            if version is not None:
                return version
    return None


def _resolve_dwg_converter() -> Optional[Path]:
    converter = str(get_settings().DWG_CONVERTER_BIN or "").strip()
    if not converter:
        return None
    path = Path(converter)
    if path.exists():
        return path
    resolved = shutil.which(converter)
    return Path(resolved) if resolved else None


def _select_dxf_output(output_dir: Path, stem: str) -> Path:
    candidates = [
        path
        for path in output_dir.rglob("*.dxf")
        if path.is_file() and path.suffix.lower() == ".dxf"
    ]
    if not candidates:
        raise CadgfConversionError("DWG conversion did not produce a DXF file.")
    for candidate in candidates:
        if candidate.stem == stem:
            return candidate
    if len(candidates) == 1:
        return candidates[0]
    raise CadgfConversionError(
        "DWG conversion produced multiple DXF outputs; unable to pick one."
    )


def _run_dwg_converter_simple(bin_path: Path, source: Path, output_dir: Path) -> Path:
    output_path = output_dir / f"{source.stem}.dxf"
    cmd = [str(bin_path), str(source), str(output_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise CadgfConversionError(
            f"DWG converter failed ({result.returncode}): {detail}"
        )
    if output_path.exists():
        return output_path
    return _select_dxf_output(output_dir, source.stem)


def _run_dwg_converter_oda(bin_path: Path, source: Path, output_dir: Path) -> Path:
    temp_input_dir = Path(tempfile.mkdtemp(prefix="dwg_input_"))
    try:
        temp_source = temp_input_dir / source.name
        shutil.copy2(source, temp_source)
        cmd = [
            str(bin_path),
            str(temp_input_dir),
            str(output_dir),
            "ACAD2013",
            "DXF",
            "0",
            "1",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise CadgfConversionError(
                f"DWG converter failed ({result.returncode}): {detail}"
            )
        return _select_dxf_output(output_dir, source.stem)
    finally:
        shutil.rmtree(temp_input_dir, ignore_errors=True)


def _convert_dwg_to_dxf(source_path: str, output_dir: str) -> Path:
    converter = _resolve_dwg_converter()
    if not converter:
        raise CadgfConversionError(
            "DWG converter not configured. Set YUANTUS_DWG_CONVERTER_BIN."
        )
    source = Path(source_path)
    if not source.exists():
        raise CadgfConversionError(f"DWG source missing: {source}")
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    if "odafileconverter" in converter.name.lower():
        return _run_dwg_converter_oda(converter, source, output_dir_path)
    return _run_dwg_converter_simple(converter, source, output_dir_path)


def _normalize_dxf_line_endings(source_path: str) -> Optional[str]:
    try:
        data = Path(source_path).read_bytes()
    except Exception:
        return None
    if b"\r\n" not in data:
        return None
    normalized = data.replace(b"\r\n", b"\n")
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    temp_file.write(normalized)
    temp_file.close()
    return temp_file.name


def _is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _merge_missing(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base or {})
    for key, value in (extra or {}).items():
        if key not in merged or merged[key] in (None, ""):
            merged[key] = value
    return merged


def _append_source(existing: Optional[str], addition: str) -> Optional[str]:
    if not addition:
        return existing
    if not existing:
        return addition
    parts = [part.strip() for part in existing.split("+") if part.strip()]
    if addition not in parts:
        parts.append(addition)
    return "+".join(parts) if parts else addition


def _extract_ocr_attributes(vision_resp: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(vision_resp, dict):
        return {}
    ocr = vision_resp.get("ocr")
    if not isinstance(ocr, dict):
        return {}
    title_block = ocr.get("title_block")
    if not isinstance(title_block, dict):
        return {}

    lower = {str(k).lower(): v for k, v in title_block.items()}

    def _pick(*keys: str) -> Optional[str]:
        for key in keys:
            value = title_block.get(key)
            if value is None:
                value = lower.get(key.lower())
            if isinstance(value, str):
                value = value.strip()
            if _is_nonempty(value):
                return str(value)
        return None

    attrs: Dict[str, Any] = {}
    drawing_no = _pick("drawing_number", "drawing_no", "drawingno")
    material = _pick("material")
    part_name = _pick("part_name", "partname", "name")
    revision = _pick("revision", "rev", "version")
    weight = _pick("weight", "mass", "net_weight", "gross_weight")

    if drawing_no:
        attrs["drawing_no"] = drawing_no
        attrs["part_number"] = drawing_no
    if material:
        attrs["material"] = material
    if part_name:
        attrs["part_name"] = part_name
    if revision:
        attrs["revision"] = revision
    if weight:
        attrs["weight"] = weight

    return attrs


def _normalize_ocr_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {
        "dimensions": raw.get("dimensions") or [],
        "symbols": raw.get("symbols") or [],
        "title_block": raw.get("title_block") or {},
        "fallback_level": raw.get("fallback_level"),
        "confidence": raw.get("confidence"),
    }


def cad_preview(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    file_id = str(payload.get("file_id") or "").strip()
    if not file_id:
        raise JobFatalError("Missing file_id")

    file_container: Optional[FileContainer] = session.get(FileContainer, file_id)
    if not file_container:
        raise JobFatalError("File not found")

    file_service = FileService()
    ext = (file_container.get_extension() or "").lower()
    if file_container.preview_path or file_container.preview_data:
        if ext != "dxf":
            return {
                "ok": True,
                "file_id": file_container.id,
                "preview_path": file_container.preview_path,
                "preview_url": f"/api/v1/file/{file_container.id}/preview",
                "skipped": True,
            }
        preview_bytes = _load_preview_bytes(file_container, file_service)
        if _preview_meets_min_size(preview_bytes, min_size=512):
            return {
                "ok": True,
                "file_id": file_container.id,
                "preview_path": file_container.preview_path,
                "preview_url": f"/api/v1/file/{file_container.id}/preview",
                "skipped": True,
            }
        file_container.preview_path = None
        file_container.preview_data = None
        session.add(file_container)
        session.flush()

    _ensure_source_exists(file_service, file_container.system_path)
    use_s3 = _is_s3_storage()
    vault_base_path = _vault_base_path()
    _require_connector_for_remote_3d(file_container, operation="preview", session=session)

    if (
        _cad_connector_enabled_for_file(file_container, session=session)
        and file_container.document_type == "3d"
    ):
        try:
            resp = _call_cad_connector_convert(
                payload=payload,
                file_container=file_container,
                file_service=file_service,
                mode="preview",
            )
            artifacts = resp.get("artifacts") or {}
            preview = artifacts.get("preview") or {}
            preview_url = (
                preview.get("png_url")
                or preview.get("jpg_url")
                or preview.get("jpeg_url")
            )
            if preview_url:
                preview_key = f"previews/{file_container.id[:2]}/{file_container.id}.png"
                _download_artifact(preview_url, file_service, preview_key)
                file_container.preview_path = preview_key
                file_container.conversion_status = ConversionStatus.COMPLETED.value
                session.add(file_container)
                session.flush()
                return {
                    "ok": True,
                    "file_id": file_container.id,
                    "preview_path": file_container.preview_path,
                    "preview_url": f"/api/v1/file/{file_container.id}/preview",
                    "source": "connector",
                }
        except Exception as exc:
            if _cad_connector_required(session):
                raise JobFatalError(f"CAD connector preview failed: {exc}") from exc
            logger.warning("CAD connector preview failed, fallback to local: %s", exc)

    # For S3: download source to temp, process, upload result back
    temp_source_path: Optional[str] = None
    temp_preview_path: Optional[str] = None

    try:
        if use_s3:
            # Download source file to temp directory
            ext = file_container.get_extension() or ""
            temp_source_path = _download_to_temp(
                file_service, file_container.system_path, suffix=f".{ext}" if ext else ""
            )
            source_path = temp_source_path
        else:
            converter = CADConverterService(session, vault_base_path=vault_base_path)
            source_path = converter._get_file_path(file_container)

        preview_bytes: Optional[bytes] = None
        settings = get_settings()
        authorization = _build_authorization_header(
            payload.get("authorization") or settings.CAD_ML_SERVICE_TOKEN
        )
        # Preferred: the VemCAD high-fidelity render service (DXF only — its v0
        # rejects .dwg; a DWG keeps the CAD-ML / local path below). Render is
        # done HERE in the task body and the bytes flow through the shared store
        # logic; preview_data is never pre-set elsewhere (that would early-skip).
        if ext == "dxf" and settings.RENDER_SERVICE_BASE_URL:
            try:
                preview_bytes = RenderServiceClient().render_preview_sync(
                    file_path=source_path,
                    filename=file_container.filename,
                    fmt="png",
                    bg="white",
                    authorization=payload.get("authorization"),
                )
                preview_bytes = _ensure_preview_min_size(
                    preview_bytes, min_size=512, label="render service DXF"
                )
            except Exception as exc:
                logger.warning("Render service preview failed, falling back: %s", exc)
                preview_bytes = None

        # Fallback: CAD-ML render (handles DWG too) — only if the render service
        # didn't already produce a preview.
        if preview_bytes is None and ext in {"dwg", "dxf"} and settings.CAD_ML_BASE_URL:
            try:
                client = CadMLClient()
                preview_bytes = client.render_cad_preview_sync(
                    file_path=source_path,
                    filename=file_container.filename,
                    authorization=authorization,
                )
                if ext == "dxf":
                    preview_bytes = _ensure_preview_min_size(
                        preview_bytes, min_size=512, label="CAD ML DXF"
                    )
            except Exception as exc:
                logger.warning("CAD ML render preview failed: %s", exc)
                preview_bytes = None

        if preview_bytes:
            if use_s3:
                preview_key = f"previews/{file_container.id[:2]}/{file_container.id}.png"
                file_service.upload_file(io.BytesIO(preview_bytes), preview_key)
                file_container.preview_path = preview_key
            else:
                converter = CADConverterService(session, vault_base_path=vault_base_path)
                output_dir = converter._get_generated_dir(file_container)
                preview_filename = f"{Path(file_container.filename).stem}_preview.png"
                preview_abs = os.path.join(output_dir, preview_filename)
                with open(preview_abs, "wb") as handle:
                    handle.write(preview_bytes)
                rel = os.path.relpath(preview_abs, vault_base_path)
                file_container.preview_path = preview_abs if rel.startswith("..") else rel
            file_container.preview_data = None
            temp_preview_path = None
        else:
            # Generate preview (works on local file)
            converter = CADConverterService(session, vault_base_path=vault_base_path)
            preview_abs = converter._generate_preview(source_path, file_container)
            temp_preview_path = preview_abs  # Mark for cleanup

            if use_s3:
                # Upload preview to S3
                preview_key = f"previews/{file_container.id[:2]}/{file_container.id}.png"
                _upload_from_local(file_service, preview_abs, preview_key)
                file_container.preview_path = preview_key
            else:
                # Local storage: store relative path
                rel = os.path.relpath(preview_abs, vault_base_path)
                file_container.preview_path = preview_abs if rel.startswith("..") else rel
            file_container.preview_data = None

        if file_container.conversion_status:
            file_container.conversion_status = ConversionStatus.COMPLETED.value
        session.add(file_container)
        session.flush()

        return {
            "ok": True,
            "file_id": file_container.id,
            "preview_path": file_container.preview_path,
            "preview_url": f"/api/v1/file/{file_container.id}/preview",
        }
    finally:
        # Cleanup temp files
        if use_s3:
            if temp_source_path and os.path.exists(temp_source_path):
                os.unlink(temp_source_path)
            if temp_preview_path and os.path.exists(temp_preview_path):
                os.unlink(temp_preview_path)


def cad_geometry(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    file_id = str(payload.get("file_id") or "").strip()
    if not file_id:
        raise JobFatalError("Missing file_id")

    target_format = str(payload.get("target_format") or "gltf").strip().lower()
    if target_format not in {"obj", "gltf", "glb", "stl"}:
        target_format = "gltf"

    file_container: Optional[FileContainer] = session.get(FileContainer, file_id)
    if not file_container:
        raise JobFatalError("File not found")

    if file_container.geometry_path:
        return {
            "ok": True,
            "file_id": file_container.id,
            "geometry_path": file_container.geometry_path,
            "geometry_url": f"/api/v1/file/{file_container.id}/geometry",
            "skipped": True,
        }

    file_service = FileService()
    _ensure_source_exists(file_service, file_container.system_path)

    ext = (file_container.get_extension() or "").lower()
    # Already viewable formats: just point geometry to the original file.
    if ext in {"stl", "obj", "gltf", "glb"}:
        file_container.geometry_path = file_container.system_path
        file_container.conversion_status = ConversionStatus.COMPLETED.value
        session.add(file_container)
        session.flush()
        return {
            "ok": True,
            "file_id": file_container.id,
            "geometry_path": file_container.geometry_path,
            "geometry_url": f"/api/v1/file/{file_container.id}/geometry",
            "note": "already_viewable",
        }

    _require_connector_for_remote_3d(file_container, operation="geometry", session=session)

    if (
        _cad_connector_enabled_for_file(file_container, session=session)
        and file_container.document_type == "3d"
        and ext not in {"dwg", "dxf"}
    ):
        try:
            resp = _call_cad_connector_convert(
                payload=payload,
                file_container=file_container,
                file_service=file_service,
                mode="geometry",
            )
            artifacts = resp.get("artifacts") or {}
            geometry = artifacts.get("geometry") or {}
            geometry_url = (
                geometry.get("gltf_url")
                or geometry.get("glb_url")
                or geometry.get("obj_url")
            )
            bin_url = geometry.get("bin_url")
            if geometry_url:
                suffix = Path(geometry_url).suffix.lower().lstrip(".")
                if suffix not in {"gltf", "glb", "obj", "stl"}:
                    suffix = target_format or "gltf"
                geometry_key = f"geometry/{file_container.id[:2]}/{file_container.id}.{suffix}"
                file_container.geometry_path = _download_artifact(
                    geometry_url, file_service, geometry_key
                )
                if bin_url:
                    _download_artifact(
                        bin_url,
                        file_service,
                        f"geometry/{file_container.id[:2]}/{file_container.id}.bin",
                    )
                file_container.conversion_status = ConversionStatus.COMPLETED.value
                file_container.conversion_error = None
                session.add(file_container)
                session.flush()
                return {
                    "ok": True,
                    "file_id": file_container.id,
                    "geometry_path": file_container.geometry_path,
                    "geometry_url": f"/api/v1/file/{file_container.id}/geometry",
                    "target_format": suffix,
                    "source": "connector",
                }
        except Exception as exc:
            if _cad_connector_required(session):
                raise JobFatalError(f"CAD connector geometry failed: {exc}") from exc
            logger.warning("CAD connector geometry failed, fallback to local: %s", exc)

    if ext in {"dxf", "dwg"}:
        vault_base_path = _vault_base_path()
        use_s3 = _is_s3_storage()

        temp_source_path: Optional[str] = None
        temp_output_dir: Optional[str] = None
        temp_dwg_dir: Optional[str] = None
        temp_dxf_path: Optional[str] = None

        try:
            if use_s3:
                source_ext = file_container.get_extension() or ""
                temp_source_path = _download_to_temp(
                    file_service,
                    file_container.system_path,
                    suffix=f".{source_ext}" if source_ext else "",
                )
                source_path = temp_source_path
                temp_output_dir = tempfile.mkdtemp(prefix="cadgf_")
                output_dir = temp_output_dir
            else:
                local_source = file_service.get_local_path(file_container.system_path)
                source_path = local_source or os.path.join(
                    vault_base_path, file_container.system_path
                )
                output_dir = _cadgf_output_dir(vault_base_path, file_container.id)

            if ext == "dwg":
                temp_dwg_dir = tempfile.mkdtemp(prefix="dwg2dxf_")
                source_path = str(_convert_dwg_to_dxf(source_path, temp_dwg_dir))
                ext = "dxf"

            if ext == "dxf":
                normalized = _normalize_dxf_line_endings(source_path)
                if normalized:
                    temp_dxf_path = normalized
                    source_path = normalized

            cadgf = CADGFConverterService()
            artifacts = cadgf.convert(source_path, output_dir, extension=ext)
            doc_schema_version = _read_cadgf_document_schema_version(
                artifacts.manifest_path,
                artifacts.document_path,
            )

            if not artifacts.mesh_gltf_path:
                raise CadgfConversionError(
                    "CADGF conversion did not produce mesh.gltf"
                )

            if use_s3:
                prefix = _cadgf_storage_prefix(file_container.id)

                def _upload(name: str, path: Path) -> str:
                    storage_key = f"{prefix}/{name}"
                    _upload_from_local(file_service, str(path), storage_key)
                    return storage_key

                file_container.cad_manifest_path = _upload(
                    "manifest.json", artifacts.manifest_path
                )
                file_container.cad_document_path = (
                    _upload("document.json", artifacts.document_path)
                    if artifacts.document_path
                    else None
                )
                file_container.cad_metadata_path = (
                    _upload("mesh_metadata.json", artifacts.mesh_metadata_path)
                    if artifacts.mesh_metadata_path
                    else None
                )
                if artifacts.mesh_bin_path:
                    _upload("mesh.bin", artifacts.mesh_bin_path)
                file_container.geometry_path = _upload(
                    "mesh.gltf", artifacts.mesh_gltf_path
                )
            else:
                def _rel(path: Path) -> str:
                    rel = os.path.relpath(path, vault_base_path)
                    return str(path) if rel.startswith("..") else rel

                file_container.cad_manifest_path = _rel(artifacts.manifest_path)
                file_container.cad_document_path = (
                    _rel(artifacts.document_path)
                    if artifacts.document_path
                    else None
                )
                file_container.cad_metadata_path = (
                    _rel(artifacts.mesh_metadata_path)
                    if artifacts.mesh_metadata_path
                    else None
                )
                file_container.geometry_path = _rel(artifacts.mesh_gltf_path)

            file_container.cad_document_schema_version = doc_schema_version
            file_container.conversion_status = ConversionStatus.COMPLETED.value
            file_container.conversion_error = None
            session.add(file_container)
            session.flush()
            return {
                "ok": True,
                "file_id": file_container.id,
                "geometry_path": file_container.geometry_path,
                "geometry_url": f"/api/v1/file/{file_container.id}/geometry",
                "cad_manifest_url": (
                    f"/api/v1/file/{file_container.id}/cad_manifest"
                    if file_container.cad_manifest_path
                    else None
                ),
                "cad_document_url": (
                    f"/api/v1/file/{file_container.id}/cad_document"
                    if file_container.cad_document_path
                    else None
                ),
                "cad_metadata_url": (
                    f"/api/v1/file/{file_container.id}/cad_metadata"
                    if file_container.cad_metadata_path
                    else None
                ),
                "target_format": "gltf",
            }
        except Exception as e:
            file_container.conversion_status = ConversionStatus.FAILED.value
            file_container.conversion_error = str(e)
            session.add(file_container)
            session.flush()
            return {
                "ok": False,
                "file_id": file_container.id,
                "error": str(e),
                "target_format": "gltf",
            }
        finally:
            if temp_dxf_path and os.path.exists(temp_dxf_path):
                os.unlink(temp_dxf_path)
            if temp_dwg_dir and os.path.exists(temp_dwg_dir):
                shutil.rmtree(temp_dwg_dir)
            if use_s3:
                if temp_source_path and os.path.exists(temp_source_path):
                    os.unlink(temp_source_path)
                if temp_output_dir and os.path.exists(temp_output_dir):
                    shutil.rmtree(temp_output_dir)

    vault_base_path = _vault_base_path()
    use_s3 = _is_s3_storage()

    # For S3: download source to temp, process, upload result back
    temp_source_path: Optional[str] = None
    temp_geometry_path: Optional[str] = None

    try:
        if use_s3:
            # Download source file to temp directory
            source_ext = file_container.get_extension() or ""
            temp_source_path = _download_to_temp(
                file_service, file_container.system_path, suffix=f".{source_ext}" if source_ext else ""
            )
            source_path = temp_source_path
        else:
            converter = CADConverterService(session, vault_base_path=vault_base_path)
            source_path = converter._get_file_path(file_container)

        converter = CADConverterService(session, vault_base_path=vault_base_path)

        try:
            geometry_abs = converter._convert_to_geometry(
                source_path, file_container, target_format
            )
            temp_geometry_path = geometry_abs  # Mark for cleanup if not same as source

            # Determine final geometry path
            if os.path.abspath(geometry_abs) == os.path.abspath(source_path):
                # Converter returned original file (no conversion needed)
                file_container.geometry_path = file_container.system_path
            elif use_s3:
                # Upload geometry to S3
                geometry_key = f"geometry/{file_container.id[:2]}/{file_container.id}.{target_format}"
                _upload_from_local(file_service, geometry_abs, geometry_key)
                file_container.geometry_path = geometry_key
            else:
                # Local storage: store relative path
                rel = os.path.relpath(geometry_abs, vault_base_path)
                file_container.geometry_path = geometry_abs if rel.startswith("..") else rel

            file_container.conversion_status = ConversionStatus.COMPLETED.value
            file_container.conversion_error = None
            session.add(file_container)
            session.flush()
            return {
                "ok": True,
                "file_id": file_container.id,
                "geometry_path": file_container.geometry_path,
                "geometry_url": f"/api/v1/file/{file_container.id}/geometry",
                "target_format": target_format,
            }
        except Exception as e:
            # Avoid retry storms when converters are not installed; mark result as a handled failure.
            file_container.conversion_status = ConversionStatus.FAILED.value
            file_container.conversion_error = str(e)
            session.add(file_container)
            session.flush()
            return {
                "ok": False,
                "file_id": file_container.id,
                "error": str(e),
                "target_format": target_format,
            }
    finally:
        # Cleanup temp files
        if use_s3:
            if temp_source_path and os.path.exists(temp_source_path):
                os.unlink(temp_source_path)
            # Only cleanup geometry if it's different from source and was uploaded
            if temp_geometry_path and temp_geometry_path != temp_source_path and os.path.exists(temp_geometry_path):
                os.unlink(temp_geometry_path)


def cad_dedup_vision(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    file_id = str(payload.get("file_id") or "").strip()
    if not file_id:
        raise JobFatalError("Missing file_id")

    mode = str(payload.get("mode") or "balanced").strip().lower()
    user_name = str(payload.get("user_name") or "anonymous").strip() or "anonymous"

    file_container: Optional[FileContainer] = session.get(FileContainer, file_id)
    if not file_container:
        raise JobFatalError("File not found")

    file_service = FileService()
    _ensure_source_exists(file_service, file_container.system_path)
    use_s3 = _is_s3_storage()
    temp_path: Optional[str] = None
    dedup_service = DedupService(session)

    rule = None
    rule_id = payload.get("rule_id")
    if rule_id:
        rule = dedup_service.get_rule(str(rule_id))
    if not rule:
        rule = dedup_service.get_applicable_rule(
            document_type=file_container.document_type
        )

    phash_threshold = rule.phash_threshold if rule else 10
    feature_threshold = rule.feature_threshold if rule else 0.85
    combined_threshold = rule.combined_threshold if rule else 0.80

    try:
        if use_s3:
            # Download to temp for S3
            ext = file_container.get_extension() or ""
            temp_path = _download_to_temp(
                file_service, file_container.system_path, suffix=f".{ext}" if ext else ""
            )
            local_path = temp_path
        else:
            local_path = file_service.get_local_path(file_container.system_path)
            if not local_path or not os.path.exists(local_path):
                return {"ok": False, "file_id": file_id, "error": "Local file not available"}

        upload_name = file_container.filename or (
            f"{file_container.id}.{file_container.get_extension() or 'bin'}"
        )

        client = DedupVisionClient()
        settings = get_settings()
        authorization = _build_authorization_header(
            payload.get("authorization") or settings.DEDUP_VISION_SERVICE_TOKEN
        )
        try:
            search = client.search_sync(
                file_path=local_path,
                upload_filename=upload_name,
                mode=mode,
                phash_threshold=phash_threshold,
                feature_threshold=feature_threshold,
                max_results=5,
                authorization=authorization,
            )
        except Exception as e:
            return {"ok": False, "file_id": file_id, "error": str(e)}

        dedup_service.ingest_search_results(
            source_file=file_container,
            search=search,
            mode=mode,
            phash_threshold=phash_threshold,
            feature_threshold=feature_threshold,
            combined_threshold=combined_threshold,
            rule_id=rule.id if rule else None,
            batch_id=payload.get("batch_id"),
        )

        indexed: Optional[Dict[str, Any]] = None
        if bool(payload.get("index", False)):
            try:
                indexed = client.index_add_sync(
                    file_path=local_path,
                    upload_filename=upload_name,
                    user_name=user_name,
                    upload_to_s3=False,
                    authorization=authorization,
                )
            except Exception as e:
                indexed = {"ok": False, "error": str(e)}

        stored_payload = {
            "kind": "cad_dedup",
            "file_id": file_container.id,
            "mode": mode,
            "params": {
                "phash_threshold": phash_threshold,
                "feature_threshold": feature_threshold,
                "combined_threshold": combined_threshold,
                "rule_id": rule.id if rule else None,
            },
            "searched_at": datetime.utcnow().isoformat() + "Z",
            "search": search,
            "indexed": indexed,
        }
        dedup_key = f"cad_dedup/{file_container.id[:2]}/{file_container.id}.json"
        stored_key = file_service.upload_file(
            file_obj=io.BytesIO(json.dumps(stored_payload, ensure_ascii=False).encode("utf-8")),
            file_path=dedup_key,
            metadata={"content-type": "application/json"},
        )
        file_container.cad_dedup_path = stored_key
        session.add(file_container)
        session.flush()

        return {
            "ok": True,
            "file_id": file_id,
            "search": search,
            "indexed": indexed,
            "cad_dedup_path": stored_key,
            "cad_dedup_url": f"/api/v1/file/{file_container.id}/cad_dedup",
        }
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def cad_ml_vision(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    file_id = str(payload.get("file_id") or "").strip()
    if not file_id:
        raise JobFatalError("Missing file_id")

    file_container: Optional[FileContainer] = session.get(FileContainer, file_id)
    if not file_container:
        raise JobFatalError("File not found")

    vault_base_path = _vault_base_path()
    file_service = FileService()
    use_s3 = _is_s3_storage()

    preview_path = file_container.preview_path
    preview_is_source = False
    if preview_path:
        if use_s3:
            if not file_service.file_exists(preview_path):
                file_container.preview_path = None
                session.add(file_container)
                session.flush()
                preview_path = None
        else:
            preview_abs = preview_path
            if not os.path.isabs(preview_abs):
                preview_abs = os.path.join(vault_base_path, preview_abs)
            if not os.path.exists(preview_abs):
                file_container.preview_path = None
                session.add(file_container)
                session.flush()
                preview_path = None

    if not preview_path and file_container.file_type in {"png", "jpg", "jpeg"}:
        if file_service.file_exists(file_container.system_path):
            preview_path = file_container.system_path
            preview_is_source = True

    if not preview_path:
        # Ensure we always have something to analyze (placeholder preview is fine).
        _ = cad_preview({"file_id": file_id}, session)
        # Refresh from DB
        session.refresh(file_container)
        preview_path = file_container.preview_path

    if not preview_path:
        return {"ok": False, "file_id": file_id, "error": "Preview not available"}

    temp_preview_path: Optional[str] = None

    try:
        if use_s3:
            # Download preview from S3 to temp
            suffix = ".png"
            if preview_is_source:
                ext = file_container.get_extension() or ""
                if ext:
                    suffix = f".{ext}"
            temp_preview_path = _download_to_temp(file_service, preview_path, suffix=suffix)
            preview_abs = temp_preview_path
        else:
            preview_abs = preview_path
            if not os.path.isabs(preview_abs):
                preview_abs = os.path.join(vault_base_path, preview_abs)

            if not os.path.exists(preview_abs):
                return {"ok": False, "file_id": file_id, "error": "Preview file missing"}

        with open(preview_abs, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("ascii")

        client = CadMLClient()
        settings = get_settings()
        authorization = _build_authorization_header(
            payload.get("authorization") or settings.CAD_ML_SERVICE_TOKEN
        )
        try:
            resp = client.vision_analyze_sync(
                image_base64=image_b64,
                include_description=True,
                include_ocr=True,
                authorization=authorization,
            )
            if not isinstance(resp.get("ocr"), dict):
                ocr_provider = payload.get("ocr_provider")
                try:
                    ocr_raw = client.ocr_extract_sync(
                        file_path=preview_abs,
                        filename=os.path.basename(preview_abs),
                        provider=str(ocr_provider).strip() if ocr_provider else None,
                        authorization=authorization,
                    )
                    if isinstance(ocr_raw, dict) and ocr_raw.get("success"):
                        resp["ocr"] = _normalize_ocr_payload(ocr_raw)
                except Exception:
                    pass
            ocr_attrs = normalize_cad_attributes(_extract_ocr_attributes(resp))
            if ocr_attrs:
                existing = dict(file_container.cad_attributes or {})
                merged = normalize_cad_attributes(_merge_missing(existing, ocr_attrs))
                if merged != existing:
                    file_container.cad_attributes = merged
                    file_container.cad_attributes_updated_at = datetime.utcnow()
                    file_container.cad_attributes_source = _append_source(
                        file_container.cad_attributes_source, "ocr"
                    )
                    session.add(file_container)
                    session.flush()
            return {
                "ok": True,
                "file_id": file_id,
                "vision": resp,
                "ocr_attributes": ocr_attrs,
            }
        except Exception as e:
            return {"ok": False, "file_id": file_id, "error": str(e)}
    finally:
        if temp_preview_path and os.path.exists(temp_preview_path):
            os.unlink(temp_preview_path)


def cad_extract(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    file_id = str(payload.get("file_id") or "").strip()
    if not file_id:
        raise JobFatalError("Missing file_id")

    file_container: Optional[FileContainer] = session.get(FileContainer, file_id)
    if not file_container:
        raise JobFatalError("File not found")

    cad_service = CadService(session)
    file_service = FileService()
    attributes, source = cad_service.extract_attributes_for_file(
        file_container, file_service=file_service, return_source=True
    )

    file_container.cad_attributes = dict(attributes or {})
    file_container.cad_attributes_source = source
    file_container.cad_attributes_updated_at = datetime.utcnow()
    if not file_container.cad_metadata_path:
        metadata_payload = {
            "kind": "cad_attributes",
            "file_id": file_container.id,
            "source": source,
            "attributes": dict(attributes or {}),
        }
        metadata_key = f"cad_metadata/{file_container.id[:2]}/{file_container.id}.json"
        stored_key = file_service.upload_file(
            file_obj=io.BytesIO(
                json.dumps(metadata_payload, ensure_ascii=False).encode("utf-8")
            ),
            file_path=metadata_key,
            metadata={"content-type": "application/json"},
        )
        file_container.cad_metadata_path = stored_key
    session.add(file_container)
    session.flush()

    return {
        "ok": True,
        "file_id": file_container.id,
        "cad_format": file_container.cad_format,
        "cad_connector_id": file_container.cad_connector_id,
        "extracted_attributes": attributes,
        "source": source,
    }


def cad_bom(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    file_id = str(payload.get("file_id") or "").strip()
    if not file_id:
        raise JobFatalError("Missing file_id")

    item_id = str(payload.get("item_id") or "").strip()
    if not item_id:
        raise JobFatalError("Missing item_id for BOM import")

    file_container: Optional[FileContainer] = session.get(FileContainer, file_id)
    if not file_container:
        raise JobFatalError("File not found")

    file_service = FileService()
    _ensure_source_exists(file_service, file_container.system_path)

    if not _cad_connector_enabled(session):
        raise JobFatalError("CAD connector not configured")

    resp = _call_cad_connector_convert(
        payload=payload,
        file_container=file_container,
        file_service=file_service,
        mode="bom",
    )
    artifacts = resp.get("artifacts") or {}
    bom_payload = artifacts.get("bom") or resp.get("bom") or {}
    if not bom_payload:
        return {
            "ok": True,
            "file_id": file_container.id,
            "skipped": True,
            "reason": "empty_bom",
        }

    importer = CadBomImportService(session)
    import_result = importer.import_bom(
        root_item_id=item_id,
        bom_payload=bom_payload,
        user_id=payload.get("user_id"),
        roles=payload.get("roles"),
    )

    stored_payload = {
        "kind": "cad_bom",
        "file_id": file_container.id,
        "item_id": item_id,
        "imported_at": datetime.utcnow().isoformat() + "Z",
        "import_result": import_result,
        "bom": bom_payload,
    }
    bom_key = f"cad_bom/{file_container.id[:2]}/{file_container.id}.json"
    stored_key = file_service.upload_file(
        file_obj=io.BytesIO(json.dumps(stored_payload, ensure_ascii=False).encode("utf-8")),
        file_path=bom_key,
        metadata={"content-type": "application/json"},
    )
    file_container.cad_bom_path = stored_key
    session.add(file_container)
    session.flush()

    return {
        "ok": True,
        "file_id": file_container.id,
        "item_id": item_id,
        "cad_bom_path": stored_key,
        "import_result": import_result,
    }


def _enrich_with_derived_files(
    result: Dict[str, Any],
    payload: Dict[str, Any],
    file_role: str,
) -> Dict[str, Any]:
    """Append derived-file binding hints for the worker post-processor."""
    if not result.get("ok"):
        return result
    file_id = result.get("file_id")
    version_id = (payload or {}).get("version_id")
    if file_id and version_id:
        result["derived_files"] = [
            {
                "file_id": file_id,
                "file_role": file_role,
                "version_id": version_id,
            }
        ]
    return result


def cad_preview_with_binding(
    payload: Dict[str, Any], session: Session
) -> Dict[str, Any]:
    """cad_preview + VersionFile binding enrichment."""
    result = cad_preview(payload, session)
    return _enrich_with_derived_files(result, payload, "preview")


def cad_geometry_with_binding(
    payload: Dict[str, Any], session: Session
) -> Dict[str, Any]:
    """cad_geometry + VersionFile binding enrichment."""
    result = cad_geometry(payload, session)
    return _enrich_with_derived_files(result, payload, "geometry")
