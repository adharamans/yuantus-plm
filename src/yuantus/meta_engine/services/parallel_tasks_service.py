from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
from sqlalchemy import func as sa_func
from sqlalchemy import or_ as sa_or
from sqlalchemy import select as sa_select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from yuantus.config import get_settings
from yuantus.meta_engine.events.domain_events import BreakageDesignLoopbackEcoEvent
from yuantus.meta_engine.events.transactional import enqueue_event
from yuantus.meta_engine.models.eco import ECO
from yuantus.meta_engine.models.job import ConversionJob, JobStatus
from yuantus.meta_engine.models.parallel_tasks import (
    BreakageIncident,
    ConsumptionPlan,
    ConsumptionRecord,
    ECOActivityGate,
    ECOActivityGateEvent,
    RemoteSite,
    ThreeDOverlay,
    WorkflowCustomActionRule,
    WorkflowCustomActionRun,
    WorkorderDocumentLink,
)
from yuantus.meta_engine.report_locale.service import ReportLocaleService
from yuantus.meta_engine.services.job_service import JobService
from yuantus.meta_engine.services.breakage_db_resolver_contract import (
    BreakageIncidentRow,
    resolve_breakage_eco_closure_descriptor,
)
from yuantus.meta_engine.services.breakage_eco_closeout_contract import (
    BreakageEcoClosureDescriptor,
    derive_breakage_change_reference,
    is_breakage_eligible_for_design_loopback,
    map_breakage_to_change_request_intake,
)
from yuantus.meta_engine.services.eco_service import ECOService
from yuantus.meta_engine.services.ecr_intake_contract import (
    ChangeRequestIntake,
    EcoDraftInputs,
    map_change_request_to_eco_draft_inputs,
)
from yuantus.meta_engine.version.models import ItemVersion


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.utcnow()


def _serialize_report_locale_profile(profile: Any) -> Dict[str, Any]:
    return {
        "id": getattr(profile, "id", None),
        "name": getattr(profile, "name", None),
        "lang": getattr(profile, "lang", None),
        "fallback_lang": getattr(profile, "fallback_lang", None),
        "number_format": getattr(profile, "number_format", None),
        "date_format": getattr(profile, "date_format", None),
        "time_format": getattr(profile, "time_format", None),
        "timezone": getattr(profile, "timezone", None),
        "paper_size": getattr(profile, "paper_size", None),
        "orientation": getattr(profile, "orientation", None),
        "header_text": getattr(profile, "header_text", None),
        "footer_text": getattr(profile, "footer_text", None),
        "logo_path": getattr(profile, "logo_path", None),
        "report_type": getattr(profile, "report_type", None),
        "is_default": bool(getattr(profile, "is_default", False)),
    }


def _resolve_report_locale_context(
    session: Session,
    *,
    locale_profile_id: Optional[str],
    report_lang: Optional[str],
    report_type: Optional[str],
) -> Optional[Dict[str, Any]]:
    profile_id = str(locale_profile_id or "").strip() or None
    lang = str(report_lang or "").strip() or None
    normalized_report_type = str(report_type or "").strip() or None
    if not profile_id and not lang:
        return None

    service = ReportLocaleService(session)
    profile = None
    if profile_id:
        profile = service.get_profile(profile_id)
    elif lang:
        profile = service.resolve_profile(lang=lang, report_type=normalized_report_type)
    if not profile:
        return None
    payload = _serialize_report_locale_profile(profile)
    payload["requested_lang"] = lang
    payload["requested_report_type"] = normalized_report_type
    payload["requested_profile_id"] = profile_id
    return payload


def _xor_bytes(raw: bytes, key: bytes) -> bytes:
    if not key:
        return raw
    return bytes(byte ^ key[idx % len(key)] for idx, byte in enumerate(raw))


def _stable_hash(values: Iterable[str]) -> str:
    h = hashlib.sha256()
    for value in values:
        h.update(str(value).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:20]


@dataclass(frozen=True)
class BreakageDesignLoopbackPreparation:
    """Read-only runtime preparation for a breakage design loopback.

    This is intentionally not an ECO creation result. It gives callers
    the resolved descriptor, eligibility decision, and pure ECR intake
    draft inputs so a later, explicit side-effecting slice can decide
    whether/how to create an ECO.
    """

    incident_id: str
    descriptor: BreakageEcoClosureDescriptor
    eligible: bool
    intake: Optional[ChangeRequestIntake] = None
    eco_draft_inputs: Optional[EcoDraftInputs] = None
    ineligible_reason: Optional[str] = None


@dataclass(frozen=True)
class BreakageDesignLoopbackEcoCreation:
    """Explicit breakage design-loopback ECO creation result."""

    incident_id: str
    preparation: BreakageDesignLoopbackPreparation
    reference: str
    eco: ECO
    created: bool


class BreakageDesignLoopbackLinkRace(RuntimeError):
    """Unrecoverable concurrent-link race in ``update_status``'s
    auto-loopback path (Tier-B #3 §3.3, taskbook `fb9d0b5` §3.C
    step 2).

    Raised ONLY when, after §3.2's CAS-loser
    ``self.session.rollback()``, the re-read finds **no incident**
    (deleted concurrently) or **no winner ECO** (``eco_id`` is
    NULL/dangling) — i.e. the linked ECO cannot be determined and
    the status change cannot be safely re-applied. The normal
    CAS-loser race is self-healed and returns an ordinary success;
    this exception is the unrecoverable boundary only. The route
    maps it to a retryable 409 ``breakage_loopback_link_race``.

    Deliberately a ``RuntimeError`` (NOT a ``ValueError``) so the
    status route's ``except ValueError -> 404`` clause does not
    swallow it.
    """


class DocumentMultiSiteService:
    TASK_PREFIX = "document_sync_"
    _ALLOWED_DIRECTIONS = {"push", "pull"}
    _ALLOWED_CHECKOUT_GATE_MODES = {"block", "warn"}
    _ALLOWED_JOB_STATUS = {
        JobStatus.PENDING.value,
        JobStatus.PROCESSING.value,
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    }

    def __init__(self, session: Session):
        self.session = session
        self._job_service = JobService(session)
        settings = get_settings()
        raw_key = (
            (settings.JWT_SECRET_KEY or "yuantus-dev-secret-change-me")
            .encode("utf-8")
        )
        self._cipher_key = hashlib.sha256(raw_key).digest()

    def _encrypt_secret(self, secret: str) -> str:
        encrypted = _xor_bytes(secret.encode("utf-8"), self._cipher_key)
        return base64.urlsafe_b64encode(encrypted).decode("utf-8")

    def _decrypt_secret(self, ciphertext: Optional[str]) -> Optional[str]:
        if not ciphertext:
            return None
        try:
            decoded = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))
            plain = _xor_bytes(decoded, self._cipher_key)
            return plain.decode("utf-8")
        except Exception:
            return None

    def _normalize_job_status(self, status: str) -> str:
        normalized = (status or "").strip().lower()
        if normalized not in self._ALLOWED_JOB_STATUS:
            allowed = ", ".join(sorted(self._ALLOWED_JOB_STATUS))
            raise ValueError(f"status must be one of: {allowed}")
        return normalized

    def _resolve_retry_attempts(self, metadata_json: Optional[Dict[str, Any]]) -> int:
        default_attempts = max(1, int(get_settings().JOB_MAX_ATTEMPTS_DEFAULT or 1))
        if not isinstance(metadata_json, dict):
            return default_attempts
        configured = metadata_json.get("retry_max_attempts")
        if configured is None:
            return default_attempts
        try:
            attempts = int(configured)
        except Exception as exc:
            raise ValueError("retry_max_attempts must be an integer between 1 and 10") from exc
        if attempts < 1 or attempts > 10:
            raise ValueError("retry_max_attempts must be between 1 and 10")
        return attempts

    def _normalize_window_days(self, window_days: int) -> int:
        try:
            normalized = int(window_days)
        except (TypeError, ValueError) as exc:
            raise ValueError("window_days must be an integer between 1 and 90") from exc
        if normalized < 1 or normalized > 90:
            raise ValueError("window_days must be between 1 and 90")
        return normalized

    def _normalize_sync_limit(self, limit: int) -> int:
        try:
            normalized = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer between 1 and 500") from exc
        if normalized < 1 or normalized > 500:
            raise ValueError("limit must be between 1 and 500")
        return normalized

    def _normalize_non_negative_int(self, value: int, *, name: str) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a non-negative integer") from exc
        if normalized < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return normalized

    def _site_default_direction(self, site: Optional[RemoteSite]) -> Optional[str]:
        if site is None:
            return None
        metadata = site.metadata_json if isinstance(site.metadata_json, dict) else {}
        direction = str(metadata.get("direction") or "").strip().lower() or None
        if direction in self._ALLOWED_DIRECTIONS:
            return direction
        if direction == "bidirectional":
            return None
        return None

    def _normalize_checkout_gate_mode(self, mode: Any) -> str:
        normalized = str(mode or "block").strip().lower() or "block"
        if normalized not in self._ALLOWED_CHECKOUT_GATE_MODES:
            raise ValueError("mode must be block or warn")
        return normalized

    def _normalize_direction_thresholds(
        self,
        direction_thresholds: Any,
    ) -> Dict[str, Dict[str, int]]:
        if direction_thresholds is None:
            return {}
        if not isinstance(direction_thresholds, dict):
            raise ValueError("direction_thresholds must be an object")

        normalized: Dict[str, Dict[str, int]] = {}
        for raw_direction, raw_thresholds in direction_thresholds.items():
            direction_key = str(raw_direction or "").strip().lower()
            if direction_key not in self._ALLOWED_DIRECTIONS:
                raise ValueError("direction_thresholds keys must be push or pull")
            if not isinstance(raw_thresholds, dict):
                raise ValueError(
                    f"direction_thresholds[{direction_key}] must be an object"
                )
            current: Dict[str, int] = {}
            for status, raw_value in raw_thresholds.items():
                status_key = str(status or "").strip().lower()
                if status_key not in {"pending", "processing", "failed", "dead_letter"}:
                    raise ValueError(
                        "direction_thresholds status keys must be pending, processing, failed, or dead_letter"
                    )
                current[status_key] = self._normalize_non_negative_int(
                    raw_value,
                    name=f"direction_thresholds[{direction_key}][{status_key}]",
                )
            normalized[direction_key] = current
        return normalized

    def _normalize_remote_auth_mode(self, auth_mode: str) -> str:
        normalized = str(auth_mode or "token").strip().lower() or "token"
        if normalized not in {"token", "basic", "none"}:
            raise ValueError("auth_mode must be one of: token, basic, none")
        return normalized

    def _build_remote_probe_auth(self, site: RemoteSite) -> Dict[str, Any]:
        metadata = site.metadata_json if isinstance(site.metadata_json, dict) else {}
        mode = self._normalize_remote_auth_mode(site.auth_mode)
        secret = self._decrypt_secret(site.auth_secret_ciphertext)
        headers: Dict[str, str] = {}
        auth = None
        if mode == "token":
            token = str(metadata.get("token") or "").strip() or secret
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif mode == "basic":
            username = (
                str(metadata.get("basic_username") or "").strip()
                or str(metadata.get("username") or "").strip()
            )
            password = (
                str(metadata.get("basic_password") or "").strip()
                or str(metadata.get("password") or "").strip()
            )
            if secret:
                if not username and not password and ":" in secret:
                    username, password = secret.split(":", 1)
                elif username and not password:
                    password = secret
            if username or password:
                auth = (username, password)
        return {"headers": headers, "auth": auth, "auth_mode": mode}

    def _build_remote_probe_targets(self, site: RemoteSite) -> List[str]:
        metadata = site.metadata_json if isinstance(site.metadata_json, dict) else {}
        configured = (
            str(metadata.get("health_path") or "").strip()
            or str(metadata.get("probe_path") or "").strip()
            or None
        )
        endpoint = str(site.endpoint or "").strip().rstrip("/")
        candidates: List[str] = []
        for raw in [configured, "/health", "/healthz", "/document_is_there/0"]:
            path = str(raw or "").strip()
            if not path or path in candidates:
                continue
            candidates.append(path)
        targets: List[str] = []
        for path in candidates:
            if path.startswith("http://") or path.startswith("https://"):
                targets.append(path)
            else:
                targets.append(f"{endpoint}/{path.lstrip('/')}")
        return targets

    def upsert_remote_site(
        self,
        *,
        name: str,
        endpoint: str,
        auth_mode: str = "token",
        auth_secret: Optional[str] = None,
        is_active: bool = True,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> RemoteSite:
        site = self.session.query(RemoteSite).filter(RemoteSite.name == name).first()
        if not site:
            site = RemoteSite(id=_uuid(), name=name)
            self.session.add(site)

        site.endpoint = endpoint.rstrip("/")
        site.auth_mode = self._normalize_remote_auth_mode(auth_mode)
        site.is_active = bool(is_active)
        site.metadata_json = metadata_json or {}
        if auth_secret:
            site.auth_secret_ciphertext = self._encrypt_secret(auth_secret.strip())
        site.updated_at = _utcnow()
        self.session.flush()
        return site

    def list_remote_sites(self, *, active_only: bool = False) -> List[RemoteSite]:
        query = self.session.query(RemoteSite).order_by(RemoteSite.name.asc())
        if active_only:
            query = query.filter(RemoteSite.is_active.is_(True))
        return query.all()

    def get_remote_site(self, site_id: str) -> Optional[RemoteSite]:
        return self.session.get(RemoteSite, site_id)

    def probe_remote_site(
        self, site_id: str, *, timeout_s: float = 3.0
    ) -> Dict[str, Any]:
        site = self.session.get(RemoteSite, site_id)
        if not site:
            raise ValueError(f"Remote site not found: {site_id}")

        status = "unhealthy"
        detail = ""
        code = None
        checked_target = None
        try:
            auth_payload = self._build_remote_probe_auth(site)
            headers = auth_payload.get("headers") or {}
            auth = auth_payload.get("auth")
            targets = self._build_remote_probe_targets(site)
            with httpx.Client(timeout=timeout_s) as client:
                for target in targets:
                    checked_target = target
                    try:
                        resp = client.get(target, headers=headers, auth=auth)
                        code = int(resp.status_code)
                        if 200 <= code < 300:
                            status = "healthy"
                            detail = ""
                            break
                        detail = f"http_{code}"
                    except Exception as exc:
                        detail = str(exc)
                        continue
        except Exception as exc:
            detail = str(exc)

        site.last_health_status = status
        site.last_health_error = detail or None
        site.last_health_at = _utcnow()
        self.session.flush()

        return {
            "site_id": site.id,
            "endpoint": site.endpoint,
            "status": status,
            "http_code": code,
            "error": detail or None,
            "checked_target": checked_target,
            "auth_mode": site.auth_mode,
            "checked_at": site.last_health_at.isoformat(),
        }

    def enqueue_sync(
        self,
        *,
        site_id: str,
        direction: str,
        document_ids: List[str],
        user_id: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> ConversionJob:
        site = self.session.get(RemoteSite, site_id)
        if not site:
            raise ValueError(f"Remote site not found: {site_id}")
        if not site.is_active:
            raise ValueError(f"Remote site is inactive: {site_id}")

        normalized_direction = (direction or "").strip().lower()
        if normalized_direction not in self._ALLOWED_DIRECTIONS:
            raise ValueError("direction must be push or pull")

        normalized_docs = sorted({str(doc_id).strip() for doc_id in document_ids if str(doc_id).strip()})
        if not normalized_docs:
            raise ValueError("document_ids must not be empty")

        metadata = metadata_json if isinstance(metadata_json, dict) else {}
        dedupe_key = idempotency_key
        if not dedupe_key:
            dedupe_key = (
                f"doc-sync:{site_id}:{normalized_direction}:{_stable_hash(normalized_docs)}"
            )
        retry_attempts = self._resolve_retry_attempts(metadata)
        payload_hash = _stable_hash(
            [
                site_id,
                normalized_direction,
                ",".join(normalized_docs),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ]
        )
        trace_id = _uuid()
        now_iso = _utcnow().isoformat()

        existing = (
            self.session.query(ConversionJob)
            .filter(
                ConversionJob.task_type == f"{self.TASK_PREFIX}{normalized_direction}",
                ConversionJob.dedupe_key == dedupe_key,
                ConversionJob.status.in_(
                    [JobStatus.PENDING.value, JobStatus.PROCESSING.value]
                ),
            )
            .order_by(ConversionJob.created_at.desc())
            .first()
        )
        if existing:
            existing_payload = (
                dict(existing.payload) if isinstance(existing.payload, dict) else {}
            )
            existing_payload["idempotency_conflicts"] = int(
                existing_payload.get("idempotency_conflicts") or 0
            ) + 1
            existing_payload["idempotency_last_seen_at"] = now_iso
            existing_payload["idempotency_last_request"] = {
                "site_id": site_id,
                "direction": normalized_direction,
                "document_ids": normalized_docs,
                "trace_id": trace_id,
                "payload_hash": payload_hash,
            }
            existing.payload = existing_payload
            self.session.flush()
            return existing

        payload = {
            "site_id": site_id,
            "site_name": site.name,
            "endpoint": site.endpoint,
            "direction": normalized_direction,
            "document_ids": normalized_docs,
            "metadata": metadata,
            "sync_trace": {
                "trace_id": trace_id,
                "origin_site": site.name,
                "payload_hash": payload_hash,
                "created_at": now_iso,
            },
            "retry_policy": {"max_attempts": retry_attempts},
            "idempotency_conflicts": 0,
        }
        return self._job_service.create_job(
            task_type=f"{self.TASK_PREFIX}{normalized_direction}",
            payload=payload,
            user_id=user_id,
            max_attempts=retry_attempts,
            dedupe=True,
            dedupe_key=dedupe_key,
        )

    def get_sync_job(self, job_id: str) -> Optional[ConversionJob]:
        job = self.session.get(ConversionJob, job_id)
        if not job:
            return None
        if not str(job.task_type or "").startswith(self.TASK_PREFIX):
            return None
        return job

    def list_sync_jobs(
        self,
        *,
        site_id: Optional[str] = None,
        status: Optional[str] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[ConversionJob]:
        cap = max(1, min(limit, 500))
        query = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type.like(f"{self.TASK_PREFIX}%"))
            .order_by(ConversionJob.created_at.desc())
        )
        if status:
            query = query.filter(
                ConversionJob.status == self._normalize_job_status(status)
            )
        if created_from:
            query = query.filter(ConversionJob.created_at >= created_from)
        if created_to:
            query = query.filter(ConversionJob.created_at <= created_to)
        jobs = query.limit(cap).all()
        if not site_id:
            return jobs

        target = str(site_id)
        filtered: List[ConversionJob] = []
        for job in jobs:
            payload = job.payload or {}
            if isinstance(payload, dict) and str(payload.get("site_id") or "") == target:
                filtered.append(job)
        return filtered

    def build_sync_job_view(self, job: ConversionJob) -> Dict[str, Any]:
        payload = job.payload if isinstance(job.payload, dict) else {}
        trace = payload.get("sync_trace")
        if not isinstance(trace, dict):
            trace = {}
        retry_policy = payload.get("retry_policy")
        if not isinstance(retry_policy, dict):
            retry_policy = {}
        attempt_count = int(job.attempt_count or 0)
        max_attempts = int(job.max_attempts or retry_policy.get("max_attempts") or 0)
        remaining_attempts = (
            max(max_attempts - attempt_count, 0) if max_attempts > 0 else None
        )
        dead_letter = payload.get("dead_letter")
        if not isinstance(dead_letter, dict):
            dead_letter = {}
        is_dead_letter = bool(dead_letter.get("is_dead_letter")) or (
            str(job.status or "").lower() == JobStatus.FAILED.value
            and max_attempts > 0
            and attempt_count >= max_attempts
        )
        dead_letter_reason = (
            dead_letter.get("reason") if dead_letter.get("reason") else None
        ) or (job.last_error if is_dead_letter else None)

        return {
            "id": job.id,
            "task_type": job.task_type,
            "status": job.status,
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "last_error": job.last_error,
            "payload": payload,
            "dedupe_key": job.dedupe_key,
            "sync_trace": {
                "trace_id": trace.get("trace_id"),
                "origin_site": trace.get("origin_site"),
                "payload_hash": trace.get("payload_hash"),
            },
            "idempotency_conflicts": int(payload.get("idempotency_conflicts") or 0),
            "retry_budget": {
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
                "remaining_attempts": remaining_attempts,
            },
            "is_dead_letter": is_dead_letter,
            "dead_letter_reason": dead_letter_reason,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    def _is_dead_letter_job(self, job: ConversionJob) -> bool:
        payload = job.payload if isinstance(job.payload, dict) else {}
        dead_letter = payload.get("dead_letter")
        if isinstance(dead_letter, dict) and bool(dead_letter.get("is_dead_letter")):
            return True
        status = str(job.status or "").strip().lower()
        attempt_count = int(job.attempt_count or 0)
        max_attempts = int(job.max_attempts or 0)
        return (
            status == JobStatus.FAILED.value
            and max_attempts > 0
            and attempt_count >= max_attempts
        )

    def sync_summary(
        self,
        *,
        site_id: Optional[str] = None,
        window_days: int = 7,
    ) -> Dict[str, Any]:
        normalized_window_days = self._normalize_window_days(window_days)
        since = _utcnow() - timedelta(days=normalized_window_days)
        query = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type.like(f"{self.TASK_PREFIX}%"))
            .filter(ConversionJob.created_at >= since)
            .order_by(ConversionJob.created_at.desc())
        )
        jobs = query.all()
        target_site_id = str(site_id).strip() if site_id is not None else None

        overall_by_status: Counter[str] = Counter()
        overall_dead_letter_total = 0
        total_jobs = 0
        buckets: Dict[str, Dict[str, Any]] = {}

        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            current_site_id = str(payload.get("site_id") or "").strip() or "unknown"
            if target_site_id and current_site_id != target_site_id:
                continue
            total_jobs += 1

            bucket = buckets.get(current_site_id)
            if not bucket:
                bucket = {
                    "site_id": current_site_id,
                    "site_name": payload.get("site_name") or None,
                    "total": 0,
                    "by_status": {},
                    "dead_letter_total": 0,
                    "directions": {"push": 0, "pull": 0},
                    "last_job_at": None,
                    "_last_job_dt": None,
                }
                buckets[current_site_id] = bucket

            status = str(job.status or "").strip().lower() or JobStatus.PENDING.value
            bucket["total"] += 1
            bucket["by_status"][status] = int(bucket["by_status"].get(status) or 0) + 1
            overall_by_status[status] += 1

            direction = str(payload.get("direction") or "").strip().lower()
            if direction in {"push", "pull"}:
                bucket["directions"][direction] = int(bucket["directions"].get(direction) or 0) + 1

            attempt_count = int(job.attempt_count or 0)
            max_attempts = int(job.max_attempts or 0)
            is_dead_letter = (
                status == JobStatus.FAILED.value
                and max_attempts > 0
                and attempt_count >= max_attempts
            )
            if is_dead_letter:
                bucket["dead_letter_total"] = int(bucket["dead_letter_total"] or 0) + 1
                overall_dead_letter_total += 1

            if job.created_at:
                previous_last_dt = bucket.get("_last_job_dt")
                if not isinstance(previous_last_dt, datetime) or job.created_at > previous_last_dt:
                    bucket["_last_job_dt"] = job.created_at
                    bucket["last_job_at"] = job.created_at.isoformat()

        sites: List[Dict[str, Any]] = []
        for key in sorted(buckets):
            bucket = buckets[key]
            completed = int(bucket["by_status"].get(JobStatus.COMPLETED.value) or 0)
            total = int(bucket["total"] or 0)
            bucket["success_rate"] = (
                round(completed / total, 4) if total > 0 else None
            )
            bucket["failure_rate"] = (
                round(int(bucket["by_status"].get(JobStatus.FAILED.value) or 0) / total, 4)
                if total > 0
                else None
            )
            bucket["by_status"] = dict(sorted(bucket["by_status"].items()))
            bucket.pop("_last_job_dt", None)
            sites.append(bucket)

        return {
            "window_days": normalized_window_days,
            "since": since.isoformat(),
            "site_filter": target_site_id,
            "total_jobs": total_jobs,
            "total_sites": len(sites),
            "overall_by_status": dict(sorted(overall_by_status.items())),
            "overall_dead_letter_total": overall_dead_letter_total,
            "sites": sites,
        }

    def list_dead_letter_sync_jobs(
        self,
        *,
        site_id: Optional[str] = None,
        window_days: int = 7,
        limit: int = 100,
    ) -> List[ConversionJob]:
        normalized_window_days = self._normalize_window_days(window_days)
        cap = self._normalize_sync_limit(limit)
        since = _utcnow() - timedelta(days=normalized_window_days)
        query = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type.like(f"{self.TASK_PREFIX}%"))
            .filter(ConversionJob.status == JobStatus.FAILED.value)
            .filter(ConversionJob.created_at >= since)
            .order_by(ConversionJob.created_at.desc())
        )
        target_site_id = str(site_id).strip() if site_id is not None else None
        results: List[ConversionJob] = []
        for job in query.all():
            payload = job.payload if isinstance(job.payload, dict) else {}
            current_site_id = str(payload.get("site_id") or "").strip()
            if target_site_id and current_site_id != target_site_id:
                continue
            if not self._is_dead_letter_job(job):
                continue
            results.append(job)
            if len(results) >= cap:
                break
        return results

    def replay_sync_jobs_batch(
        self,
        *,
        job_ids: Optional[List[str]] = None,
        site_id: Optional[str] = None,
        only_dead_letter: bool = True,
        window_days: int = 7,
        limit: int = 50,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        cap = self._normalize_sync_limit(limit)
        ordered_ids: List[str] = []
        seen_ids = set()
        for raw in (job_ids or []):
            job_id = str(raw or "").strip()
            if not job_id or job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            ordered_ids.append(job_id)

        source = "job_ids"
        candidates: List[ConversionJob] = []
        if ordered_ids:
            for job_id in ordered_ids:
                job = self.get_sync_job(job_id)
                if not job:
                    continue
                candidates.append(job)
        else:
            source = "dead_letter" if only_dead_letter else "failed"
            if only_dead_letter:
                candidates = self.list_dead_letter_sync_jobs(
                    site_id=site_id,
                    window_days=window_days,
                    limit=cap,
                )
            else:
                candidates = self.list_sync_jobs(
                    site_id=site_id,
                    status=JobStatus.FAILED.value,
                    created_from=_utcnow() - timedelta(days=self._normalize_window_days(window_days)),
                    limit=cap,
                )

        requested = min(len(candidates), cap)
        replayed = 0
        skipped_non_dead_letter = 0
        failures: List[Dict[str, Any]] = []
        replayed_jobs: List[Dict[str, Any]] = []

        for job in candidates[:cap]:
            if only_dead_letter and not self._is_dead_letter_job(job):
                skipped_non_dead_letter += 1
                continue
            try:
                new_job = self.replay_sync_job(job.id, user_id=user_id)
                replayed += 1
                replayed_jobs.append(
                    {
                        "source_job_id": job.id,
                        "replayed_job_id": new_job.id,
                        "task_type": new_job.task_type,
                        "status": new_job.status,
                        "created_at": new_job.created_at.isoformat() if new_job.created_at else None,
                    }
                )
            except Exception as exc:
                failures.append({"job_id": job.id, "error": str(exc)})

        return {
            "source": source,
            "site_id": site_id,
            "only_dead_letter": bool(only_dead_letter),
            "window_days": int(window_days),
            "requested": requested,
            "replayed": replayed,
            "failed": len(failures),
            "skipped_non_dead_letter": skipped_non_dead_letter,
            "failures": failures,
            "replayed_jobs": replayed_jobs,
        }

    def evaluate_checkout_sync_gate(
        self,
        *,
        item_id: str,
        site_id: str,
        direction: Optional[str] = None,
        mode: str = "block",
        direction_thresholds: Optional[Dict[str, Dict[str, int]]] = None,
        version_id: Optional[str] = None,
        document_ids: Optional[List[str]] = None,
        window_days: int = 7,
        limit: int = 200,
        block_on_dead_letter_only: bool = False,
        max_pending: int = 0,
        max_processing: int = 0,
        max_failed: int = 0,
        max_dead_letter: int = 0,
    ) -> Dict[str, Any]:
        normalized_item_id = str(item_id or "").strip()
        if not normalized_item_id:
            raise ValueError("item_id must not be empty")
        normalized_site_id = str(site_id or "").strip()
        if not normalized_site_id:
            raise ValueError("site_id must not be empty")
        site = self.get_remote_site(normalized_site_id)
        normalized_direction = str(direction or "").strip().lower() or None
        if normalized_direction and normalized_direction not in self._ALLOWED_DIRECTIONS:
            raise ValueError("direction must be push or pull")
        normalized_mode = self._normalize_checkout_gate_mode(mode)
        effective_direction = normalized_direction or self._site_default_direction(site)
        normalized_version_id = str(version_id or "").strip() or None
        monitored_document_ids = {
            str(doc_id).strip()
            for doc_id in (document_ids or [])
            if str(doc_id).strip()
        }
        if not monitored_document_ids:
            monitored_document_ids.add(normalized_item_id)
        if normalized_version_id:
            monitored_document_ids.add(normalized_version_id)
        normalized_window_days = self._normalize_window_days(window_days)
        cap = self._normalize_sync_limit(limit)
        thresholds = {
            "pending": self._normalize_non_negative_int(
                max_pending, name="max_pending"
            ),
            "processing": self._normalize_non_negative_int(
                max_processing, name="max_processing"
            ),
            "failed": self._normalize_non_negative_int(max_failed, name="max_failed"),
            "dead_letter": self._normalize_non_negative_int(
                max_dead_letter, name="max_dead_letter"
            ),
        }
        normalized_direction_thresholds = self._normalize_direction_thresholds(
            direction_thresholds
        )
        effective_thresholds = dict(thresholds)
        if effective_direction and normalized_direction_thresholds.get(effective_direction):
            effective_thresholds.update(normalized_direction_thresholds[effective_direction])
        dead_letter_only = bool(block_on_dead_letter_only)
        since = _utcnow() - timedelta(days=normalized_window_days)

        if effective_direction:
            query = (
                self.session.query(ConversionJob)
                .filter(ConversionJob.task_type == f"{self.TASK_PREFIX}{effective_direction}")
                .filter(ConversionJob.created_at >= since)
                .order_by(ConversionJob.created_at.desc())
            )
        else:
            query = (
                self.session.query(ConversionJob)
                .filter(ConversionJob.task_type.like(f"{self.TASK_PREFIX}%"))
                .filter(ConversionJob.created_at >= since)
                .order_by(ConversionJob.created_at.desc())
            )

        pending_views: List[Dict[str, Any]] = []
        dead_letter_views: List[Dict[str, Any]] = []
        matched_document_ids = set()
        counts = {"pending": 0, "processing": 0, "failed": 0, "dead_letter": 0}
        for job in query.all():
            payload = job.payload if isinstance(job.payload, dict) else {}
            current_site_id = str(payload.get("site_id") or "").strip()
            if current_site_id != normalized_site_id:
                continue
            raw_document_ids = payload.get("document_ids")
            job_document_ids = (
                {
                    str(doc_id).strip()
                    for doc_id in raw_document_ids
                    if str(doc_id).strip()
                }
                if isinstance(raw_document_ids, list)
                else set()
            )
            overlap_ids = sorted(job_document_ids.intersection(monitored_document_ids))
            if not overlap_ids:
                continue
            status = str(job.status or "").strip().lower()
            if status not in {
                JobStatus.PENDING.value,
                JobStatus.PROCESSING.value,
                JobStatus.FAILED.value,
            }:
                continue
            counts[status] = int(counts.get(status) or 0) + 1
            is_dead_letter = self._is_dead_letter_job(job)
            if is_dead_letter:
                counts["dead_letter"] = int(counts.get("dead_letter") or 0) + 1
            matched_document_ids.update(overlap_ids)
            view = self.build_sync_job_view(job)
            view["matched_document_ids"] = overlap_ids
            if is_dead_letter:
                dead_letter_views.append(view)
            if not dead_letter_only:
                pending_views.append(view)
            tracked_total = len(dead_letter_views) if dead_letter_only else len(pending_views)
            if tracked_total >= cap:
                break

        considered = (
            ("dead_letter",)
            if dead_letter_only
            else ("pending", "processing", "failed", "dead_letter")
        )
        blocking_reasons = [
            {
                "status": status,
                "count": int(counts.get(status) or 0),
                "threshold": int(effective_thresholds.get(status) or 0),
            }
            for status in considered
            if int(counts.get(status) or 0) > int(effective_thresholds.get(status) or 0)
        ]
        threshold_exceeded = bool(blocking_reasons)
        warning = threshold_exceeded and normalized_mode == "warn"
        blocking = threshold_exceeded and normalized_mode == "block"
        verdict = "clear"
        if blocking:
            verdict = "block"
        elif warning:
            verdict = "warn"

        if dead_letter_only:
            blocking_views = dead_letter_views[:cap]
        else:
            blocking_views = pending_views[:cap]
        return {
            "item_id": normalized_item_id,
            "site_id": normalized_site_id,
            "direction": normalized_direction,
            "effective_direction": effective_direction,
            "mode": normalized_mode,
            "verdict": verdict,
            "direction_thresholds": normalized_direction_thresholds,
            "version_id": normalized_version_id,
            "monitored_document_ids": sorted(monitored_document_ids),
            "matched_document_ids": sorted(matched_document_ids),
            "window_days": normalized_window_days,
            "since": since.isoformat(),
            "checked_at": _utcnow().isoformat(),
            "policy": {
                "block_on_dead_letter_only": dead_letter_only,
                "mode": normalized_mode,
            },
            "thresholds": effective_thresholds,
            "blocking_reasons": blocking_reasons,
            "threshold_exceeded": threshold_exceeded,
            "warning": warning,
            "blocking": blocking,
            "blocking_total": len(blocking_views),
            "blocking_counts": counts,
            "blocking_jobs": blocking_views,
        }

    def export_sync_summary(
        self,
        *,
        site_id: Optional[str] = None,
        window_days: int = 7,
        export_format: str = "json",
    ) -> Dict[str, Any]:
        summary = self.sync_summary(site_id=site_id, window_days=window_days)
        normalized = str(export_format or "json").strip().lower()
        if normalized == "json":
            return {
                "content": json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8"),
                "media_type": "application/json",
                "filename": "doc-sync-summary.json",
            }

        rows: List[Dict[str, Any]] = []
        rows.append(
            {
                "scope": "overall",
                "site_id": summary.get("site_filter") or "*",
                "site_name": "",
                "total": int(summary.get("total_jobs") or 0),
                "completed": int((summary.get("overall_by_status") or {}).get("completed") or 0),
                "failed": int((summary.get("overall_by_status") or {}).get("failed") or 0),
                "processing": int((summary.get("overall_by_status") or {}).get("processing") or 0),
                "pending": int((summary.get("overall_by_status") or {}).get("pending") or 0),
                "cancelled": int((summary.get("overall_by_status") or {}).get("cancelled") or 0),
                "dead_letter_total": int(summary.get("overall_dead_letter_total") or 0),
                "push_total": "",
                "pull_total": "",
                "success_rate": "",
                "failure_rate": "",
                "last_job_at": "",
            }
        )
        for site in summary.get("sites") or []:
            by_status = site.get("by_status") if isinstance(site.get("by_status"), dict) else {}
            directions = site.get("directions") if isinstance(site.get("directions"), dict) else {}
            rows.append(
                {
                    "scope": "site",
                    "site_id": site.get("site_id") or "",
                    "site_name": site.get("site_name") or "",
                    "total": int(site.get("total") or 0),
                    "completed": int(by_status.get("completed") or 0),
                    "failed": int(by_status.get("failed") or 0),
                    "processing": int(by_status.get("processing") or 0),
                    "pending": int(by_status.get("pending") or 0),
                    "cancelled": int(by_status.get("cancelled") or 0),
                    "dead_letter_total": int(site.get("dead_letter_total") or 0),
                    "push_total": int(directions.get("push") or 0),
                    "pull_total": int(directions.get("pull") or 0),
                    "success_rate": site.get("success_rate"),
                    "failure_rate": site.get("failure_rate"),
                    "last_job_at": site.get("last_job_at") or "",
                }
            )

        if normalized == "csv":
            csv_io = io.StringIO()
            writer = csv.DictWriter(
                csv_io,
                fieldnames=[
                    "scope",
                    "site_id",
                    "site_name",
                    "total",
                    "completed",
                    "failed",
                    "processing",
                    "pending",
                    "cancelled",
                    "dead_letter_total",
                    "push_total",
                    "pull_total",
                    "success_rate",
                    "failure_rate",
                    "last_job_at",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": "doc-sync-summary.csv",
            }

        if normalized == "md":
            lines = [
                "# Doc Sync Summary",
                "",
                f"- window_days: {summary.get('window_days') or 0}",
                f"- since: {summary.get('since') or ''}",
                f"- site_filter: {summary.get('site_filter') or '<none>'}",
                f"- total_jobs: {summary.get('total_jobs') or 0}",
                f"- total_sites: {summary.get('total_sites') or 0}",
                f"- overall_dead_letter_total: {summary.get('overall_dead_letter_total') or 0}",
                "",
                "| Site ID | Site Name | Total | Completed | Failed | Processing | Dead Letter | Push | Pull | Success Rate | Failure Rate | Last Job At |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
            for row in rows[1:]:
                lines.append(
                    "| "
                    f"{row.get('site_id') or ''} | "
                    f"{row.get('site_name') or ''} | "
                    f"{row.get('total') or 0} | "
                    f"{row.get('completed') or 0} | "
                    f"{row.get('failed') or 0} | "
                    f"{row.get('processing') or 0} | "
                    f"{row.get('dead_letter_total') or 0} | "
                    f"{row.get('push_total') or 0} | "
                    f"{row.get('pull_total') or 0} | "
                    f"{row.get('success_rate') if row.get('success_rate') is not None else ''} | "
                    f"{row.get('failure_rate') if row.get('failure_rate') is not None else ''} | "
                    f"{row.get('last_job_at') or ''} |"
                )
            return {
                "content": ("\n".join(lines) + "\n").encode("utf-8"),
                "media_type": "text/markdown",
                "filename": "doc-sync-summary.md",
            }

        raise ValueError("export_format must be json, csv or md")

    def replay_sync_job(
        self, job_id: str, *, user_id: Optional[int] = None
    ) -> ConversionJob:
        job = self.get_sync_job(job_id)
        if not job:
            raise ValueError(f"Sync job not found: {job_id}")
        payload = dict(job.payload or {})
        payload["replay_of"] = job.id
        payload["replayed_at"] = _utcnow().isoformat()
        dedupe_key = f"doc-sync-replay:{job.id}:{_uuid()}"
        return self._job_service.create_job(
            task_type=str(job.task_type),
            payload=payload,
            user_id=user_id,
            dedupe=True,
            dedupe_key=dedupe_key,
        )


class ECOActivityValidationService:
    _TERMINAL = {"completed", "canceled", "exception"}
    _OPEN = {"pending", "active"}
    _VALID_STATUS = {"pending", "active", "completed", "canceled", "exception"}
    _STATUS_ALIASES = {
        "draft": "pending",
        "in_progress": "active",
        "eco": "active",
        "done": "completed",
        "cancel": "canceled",
        "cancelled": "canceled",
    }
    _TRANSITIONS = {
        "pending": {"active", "completed", "canceled", "exception"},
        "active": {"completed", "canceled", "exception"},
        "completed": {"pending"},
        "canceled": {"pending"},
        "exception": {"pending", "active"},
    }

    def __init__(self, session: Session):
        self.session = session

    def _normalize_activity_status(self, value: str, *, field_name: str = "status") -> str:
        normalized = str(value or "").strip().lower()
        if normalized in self._VALID_STATUS:
            return normalized
        alias = self._STATUS_ALIASES.get(normalized)
        if alias:
            return alias
        allowed = sorted(
            set(self._VALID_STATUS).union(self._STATUS_ALIASES.keys())
        )
        raise ValueError(f"{field_name} must be one of: {', '.join(allowed)}")

    def create_activity(
        self,
        *,
        eco_id: str,
        name: str,
        depends_on_activity_ids: Optional[List[str]] = None,
        is_blocking: bool = True,
        assignee_id: Optional[int] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> ECOActivityGate:
        activity = ECOActivityGate(
            id=_uuid(),
            eco_id=eco_id,
            name=name,
            status="pending",
            is_blocking=bool(is_blocking),
            assignee_id=assignee_id,
            depends_on_activity_ids=sorted(
                {
                    str(activity_id).strip()
                    for activity_id in (depends_on_activity_ids or [])
                    if str(activity_id).strip()
                }
            ),
            properties=properties or {},
        )
        self.session.add(activity)
        self.session.flush()
        self._record_event(
            eco_id=eco_id,
            activity_id=activity.id,
            from_status=None,
            to_status="pending",
            reason="created",
            user_id=assignee_id,
        )
        return activity

    def list_activities(self, eco_id: str) -> List[ECOActivityGate]:
        return (
            self.session.query(ECOActivityGate)
            .filter(ECOActivityGate.eco_id == eco_id)
            .order_by(ECOActivityGate.created_at.asc())
            .all()
        )

    def get_activity(self, activity_id: str) -> Optional[ECOActivityGate]:
        return self.session.get(ECOActivityGate, activity_id)

    def _dependency_ids(self, activity: ECOActivityGate) -> List[str]:
        raw = activity.depends_on_activity_ids or []
        if not isinstance(raw, list):
            return []
        result = []
        for value in raw:
            normalized = str(value).strip()
            if normalized:
                result.append(normalized)
        return sorted(set(result))

    def _dependency_blockers(self, activity: ECOActivityGate) -> List[Dict[str, Any]]:
        dep_ids = self._dependency_ids(activity)
        if not dep_ids:
            return []
        blockers: List[Dict[str, Any]] = []
        for dep_id in dep_ids:
            dep = self.session.get(ECOActivityGate, dep_id)
            if not dep:
                blockers.append({"activity_id": dep_id, "reason": "dependency_not_found"})
                continue
            if dep.status != "completed":
                blockers.append(
                    {
                        "activity_id": dep.id,
                        "name": dep.name,
                        "status": dep.status,
                        "reason": "dependency_not_completed",
                    }
                )
        return blockers

    def evaluate_transition(
        self,
        *,
        activity_id: str,
        to_status: str,
    ) -> Dict[str, Any]:
        activity = self.session.get(ECOActivityGate, activity_id)
        if not activity:
            raise ValueError(f"Activity not found: {activity_id}")

        current_status = self._normalize_activity_status(
            str(activity.status or "pending"),
            field_name="current_status",
        )
        target_status = self._normalize_activity_status(
            to_status,
            field_name="to_status",
        )
        allowed_targets = sorted(
            set(self._TRANSITIONS.get(current_status, set())).union({current_status})
        )
        blockers: List[Dict[str, Any]] = []
        reason_code = "ok"
        can_transition = True

        if target_status != current_status and target_status not in allowed_targets:
            can_transition = False
            reason_code = "invalid_transition"
        elif target_status in {"active", "completed"}:
            blockers = self._dependency_blockers(activity)
            if blockers:
                can_transition = False
                reason_code = "blocking_dependencies"

        return {
            "activity_id": activity.id,
            "eco_id": activity.eco_id,
            "name": activity.name,
            "from_status": current_status,
            "to_status": target_status,
            "requested_status": str(to_status or "").strip(),
            "allowed_targets": allowed_targets,
            "can_transition": can_transition,
            "reason_code": reason_code,
            "blockers": blockers,
        }

    def evaluate_transitions_bulk(
        self,
        eco_id: str,
        *,
        to_status: str,
        activity_ids: Optional[List[str]] = None,
        include_terminal: bool = False,
        include_non_blocking: bool = True,
        limit: int = 200,
    ) -> Dict[str, Any]:
        try:
            cap = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer between 1 and 500") from exc
        if cap < 1 or cap > 500:
            raise ValueError("limit must be between 1 and 500")

        target_status = self._normalize_activity_status(
            to_status,
            field_name="to_status",
        )
        activities = self.list_activities(eco_id)
        index = {str(activity.id): activity for activity in activities}
        selected_ids: List[str]
        if activity_ids:
            selected_ids = []
            seen = set()
            for value in activity_ids:
                normalized = str(value or "").strip()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                selected_ids.append(normalized)
        else:
            selected_ids = [str(activity.id) for activity in activities]

        decisions: List[Dict[str, Any]] = []
        excluded_ids: List[str] = []
        missing_ids: List[str] = []
        for activity_id in selected_ids:
            activity = index.get(activity_id)
            if activity is None:
                missing_ids.append(activity_id)
                continue
            current = self._normalize_activity_status(
                str(activity.status or "pending"),
                field_name="status",
            )
            if not include_terminal and current in self._TERMINAL:
                excluded_ids.append(activity_id)
                continue
            if not include_non_blocking and not bool(activity.is_blocking):
                excluded_ids.append(activity_id)
                continue
            decision = self.evaluate_transition(
                activity_id=activity.id,
                to_status=target_status,
            )
            decision["is_blocking"] = bool(activity.is_blocking)
            decision["assignee_id"] = activity.assignee_id
            decisions.append(decision)

        total_candidates = len(decisions)
        ready_total = 0
        blocked_total = 0
        invalid_total = 0
        noop_total = 0
        for row in decisions:
            if not bool(row.get("can_transition")):
                if str(row.get("reason_code")) == "blocking_dependencies":
                    blocked_total += 1
                else:
                    invalid_total += 1
                continue
            if str(row.get("from_status")) == str(row.get("to_status")):
                noop_total += 1
            else:
                ready_total += 1

        truncated = total_candidates > cap
        page = decisions[:cap]
        return {
            "eco_id": eco_id,
            "to_status": target_status,
            "requested_status": str(to_status or "").strip(),
            "include_terminal": bool(include_terminal),
            "include_non_blocking": bool(include_non_blocking),
            "selected_total": len(selected_ids),
            "total": total_candidates,
            "ready_total": ready_total,
            "blocked_total": blocked_total,
            "invalid_total": invalid_total,
            "noop_total": noop_total,
            "missing_total": len(missing_ids),
            "excluded_total": len(excluded_ids),
            "missing_activity_ids": missing_ids,
            "excluded_activity_ids": excluded_ids,
            "truncated": truncated,
            "decisions": page,
        }

    def transition_activities_bulk(
        self,
        eco_id: str,
        *,
        to_status: str,
        activity_ids: Optional[List[str]] = None,
        include_terminal: bool = False,
        include_non_blocking: bool = True,
        limit: int = 200,
        user_id: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        checked = self.evaluate_transitions_bulk(
            eco_id,
            to_status=to_status,
            activity_ids=activity_ids,
            include_terminal=include_terminal,
            include_non_blocking=include_non_blocking,
            limit=limit,
        )
        if bool(checked.get("truncated")):
            raise ValueError(
                "bulk execution truncated by limit; increase limit and retry"
            )

        target_status = str(checked.get("to_status") or "").strip().lower()
        candidate_ids = [
            str((row or {}).get("activity_id") or "").strip()
            for row in (checked.get("decisions") or [])
            if str((row or {}).get("activity_id") or "").strip()
        ]

        actions: Dict[str, str] = {}
        decisions: Dict[str, Dict[str, Any]] = {}
        remaining = set(candidate_ids)
        max_rounds = max(len(candidate_ids), 1)
        for _ in range(max_rounds):
            progressed = False
            for activity_id in list(remaining):
                decision = self.evaluate_transition(
                    activity_id=activity_id,
                    to_status=target_status,
                )
                decisions[activity_id] = decision
                if not bool(decision.get("can_transition")):
                    continue
                if str(decision.get("from_status")) == str(decision.get("to_status")):
                    actions[activity_id] = "noop"
                    remaining.discard(activity_id)
                    progressed = True
                    continue

                self.transition_activity(
                    activity_id=activity_id,
                    to_status=target_status,
                    user_id=user_id,
                    reason=reason,
                )
                actions[activity_id] = "executed"
                remaining.discard(activity_id)
                progressed = True
            if not progressed:
                break

        rows: List[Dict[str, Any]] = []
        executed_total = 0
        noop_total = 0
        blocked_total = 0
        invalid_total = 0
        for activity_id in candidate_ids:
            decision = decisions.get(activity_id) or self.evaluate_transition(
                activity_id=activity_id,
                to_status=target_status,
            )
            action = actions.get(activity_id)
            if action == "executed":
                executed_total += 1
            elif action == "noop":
                noop_total += 1
            else:
                action = "skipped"
                if str(decision.get("reason_code") or "") == "blocking_dependencies":
                    blocked_total += 1
                else:
                    invalid_total += 1

            rows.append(
                {
                    **decision,
                    "action": action,
                }
            )

        return {
            "eco_id": eco_id,
            "to_status": target_status,
            "requested_status": str(to_status or "").strip(),
            "selected_total": int(checked.get("selected_total") or 0),
            "total": len(candidate_ids),
            "executed_total": executed_total,
            "noop_total": noop_total,
            "blocked_total": blocked_total,
            "invalid_total": invalid_total,
            "missing_total": int(checked.get("missing_total") or 0),
            "excluded_total": int(checked.get("excluded_total") or 0),
            "missing_activity_ids": checked.get("missing_activity_ids") or [],
            "excluded_activity_ids": checked.get("excluded_activity_ids") or [],
            "decisions": rows,
        }

    def transition_activity(
        self,
        *,
        activity_id: str,
        to_status: str,
        user_id: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> ECOActivityGate:
        decision = self.evaluate_transition(activity_id=activity_id, to_status=to_status)
        if not decision.get("can_transition"):
            if str(decision.get("reason_code")) == "blocking_dependencies":
                blocker_text = ", ".join(
                    f"{entry.get('activity_id')}({entry.get('status', entry.get('reason'))})"
                    for entry in (decision.get("blockers") or [])
                )
                raise ValueError(f"Blocking dependencies: {blocker_text}")
            raise ValueError(
                "Invalid transition: "
                f"{decision.get('from_status')} -> {decision.get('to_status')}"
            )

        activity = self.session.get(ECOActivityGate, activity_id)
        if not activity:
            raise ValueError(f"Activity not found: {activity_id}")
        target = str(decision.get("to_status") or "").strip().lower()

        from_status = activity.status
        activity.status = target
        activity.updated_at = _utcnow()
        if target in self._TERMINAL:
            activity.closed_at = _utcnow()
            activity.closed_by_id = user_id
        else:
            activity.closed_at = None
            activity.closed_by_id = None
        self.session.flush()
        self._record_event(
            eco_id=activity.eco_id,
            activity_id=activity.id,
            from_status=from_status,
            to_status=target,
            reason=reason,
            user_id=user_id,
        )
        return activity

    def blockers_for_eco(self, eco_id: str) -> Dict[str, Any]:
        activities = self.list_activities(eco_id)
        blockers: List[Dict[str, Any]] = []
        for activity in activities:
            if not activity.is_blocking:
                continue
            normalized_status = self._normalize_activity_status(
                str(activity.status or "pending"),
                field_name="status",
            )
            if normalized_status in self._TERMINAL:
                continue
            deps = self._dependency_blockers(activity)
            blockers.append(
                {
                    "activity_id": activity.id,
                    "name": activity.name,
                    "status": normalized_status,
                    "dependencies": deps,
                }
            )
        return {"eco_id": eco_id, "total": len(blockers), "blockers": blockers}

    def recent_events(self, eco_id: str, *, limit: int = 20) -> List[ECOActivityGateEvent]:
        cap = max(1, min(limit, 200))
        return (
            self.session.query(ECOActivityGateEvent)
            .filter(ECOActivityGateEvent.eco_id == eco_id)
            .order_by(ECOActivityGateEvent.created_at.desc())
            .limit(cap)
            .all()
        )

    def _normalize_due_soon_hours(self, due_soon_hours: int) -> int:
        try:
            normalized = int(due_soon_hours)
        except (TypeError, ValueError) as exc:
            raise ValueError("due_soon_hours must be an integer between 1 and 720") from exc
        if normalized < 1 or normalized > 720:
            raise ValueError("due_soon_hours must be between 1 and 720")
        return normalized

    def _normalize_sla_limit(self, limit: int) -> int:
        try:
            normalized = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer between 1 and 500") from exc
        if normalized < 1 or normalized > 500:
            raise ValueError("limit must be between 1 and 500")
        return normalized

    def _activity_due_at(self, activity: ECOActivityGate) -> Optional[datetime]:
        properties = activity.properties if isinstance(activity.properties, dict) else {}
        raw = properties.get("due_at")
        if isinstance(raw, datetime):
            parsed = raw
        else:
            value = str(raw or "").strip()
            if not value:
                return None
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    def activity_sla(
        self,
        eco_id: str,
        *,
        now: Optional[datetime] = None,
        due_soon_hours: int = 24,
        include_closed: bool = False,
        assignee_id: Optional[int] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        window_hours = self._normalize_due_soon_hours(due_soon_hours)
        cap = self._normalize_sla_limit(limit)
        evaluated_at = now or _utcnow()
        if evaluated_at.tzinfo is not None:
            evaluated_at = evaluated_at.astimezone(timezone.utc).replace(tzinfo=None)
        due_soon_deadline = evaluated_at + timedelta(hours=window_hours)

        activities = self.list_activities(eco_id)
        rows: List[Dict[str, Any]] = []
        status_counts: Counter[str] = Counter()
        overdue_total = 0
        due_soon_total = 0
        on_track_total = 0
        no_due_date_total = 0
        closed_total = 0

        for activity in activities:
            if assignee_id is not None and int(activity.assignee_id or 0) != int(assignee_id):
                continue
            status = self._normalize_activity_status(
                str(activity.status or "pending"),
                field_name="status",
            )
            is_terminal = status in self._TERMINAL
            if is_terminal and not include_closed:
                continue
            status_counts[status] += 1

            due_at = self._activity_due_at(activity)
            hours_to_due = None
            if due_at is not None:
                hours_to_due = round(
                    (due_at - evaluated_at).total_seconds() / 3600.0,
                    3,
                )

            if is_terminal:
                classification = "closed"
                closed_total += 1
            elif due_at is None:
                classification = "no_due_date"
                no_due_date_total += 1
            elif due_at < evaluated_at:
                classification = "overdue"
                overdue_total += 1
            elif due_at <= due_soon_deadline:
                classification = "due_soon"
                due_soon_total += 1
            else:
                classification = "on_track"
                on_track_total += 1

            rows.append(
                {
                    "id": activity.id,
                    "name": activity.name,
                    "status": status,
                    "is_blocking": bool(activity.is_blocking),
                    "assignee_id": activity.assignee_id,
                    "depends_on_activity_ids": self._dependency_ids(activity),
                    "due_at": due_at.isoformat() if due_at else None,
                    "classification": classification,
                    "hours_to_due": hours_to_due,
                    "closed_at": activity.closed_at.isoformat() if activity.closed_at else None,
                    "updated_at": activity.updated_at.isoformat() if activity.updated_at else None,
                }
            )

        order = {
            "overdue": 0,
            "due_soon": 1,
            "on_track": 2,
            "no_due_date": 3,
            "closed": 4,
        }
        rows.sort(
            key=lambda row: (
                order.get(str(row.get("classification")), 99),
                row.get("due_at") is None,
                str(row.get("due_at") or ""),
                str(row.get("id") or ""),
            )
        )

        total = len(rows)
        page = rows[:cap]
        open_total = sum(status_counts.get(status, 0) for status in self._OPEN)

        return {
            "eco_id": eco_id,
            "evaluated_at": evaluated_at.isoformat(),
            "due_soon_hours": window_hours,
            "due_soon_deadline": due_soon_deadline.isoformat(),
            "include_closed": bool(include_closed),
            "assignee_id": assignee_id,
            "total": total,
            "open_total": open_total,
            "closed_total": closed_total,
            "overdue_total": overdue_total,
            "due_soon_total": due_soon_total,
            "on_track_total": on_track_total,
            "no_due_date_total": no_due_date_total,
            "status_counts": dict(sorted(status_counts.items())),
            "truncated": total > cap,
            "activities": page,
        }

    def _normalize_alert_rate_threshold(self, value: Optional[float], *, field: str) -> float:
        try:
            normalized = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be between 0 and 1") from exc
        if normalized < 0 or normalized > 1:
            raise ValueError(f"{field} must be between 0 and 1")
        return normalized

    def _normalize_alert_count_threshold(self, value: Optional[int], *, field: str) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be between 0 and 100000") from exc
        if normalized < 0 or normalized > 100000:
            raise ValueError(f"{field} must be between 0 and 100000")
        return normalized

    def activity_sla_alerts(
        self,
        eco_id: str,
        *,
        now: Optional[datetime] = None,
        due_soon_hours: int = 24,
        include_closed: bool = False,
        assignee_id: Optional[int] = None,
        limit: int = 100,
        overdue_rate_warn: float = 0.2,
        due_soon_count_warn: int = 5,
        blocking_overdue_warn: int = 1,
    ) -> Dict[str, Any]:
        rate_warn = self._normalize_alert_rate_threshold(
            overdue_rate_warn,
            field="overdue_rate_warn",
        )
        due_soon_warn = self._normalize_alert_count_threshold(
            due_soon_count_warn,
            field="due_soon_count_warn",
        )
        blocking_warn = self._normalize_alert_count_threshold(
            blocking_overdue_warn,
            field="blocking_overdue_warn",
        )

        summary = self.activity_sla(
            eco_id,
            now=now,
            due_soon_hours=due_soon_hours,
            include_closed=include_closed,
            assignee_id=assignee_id,
            limit=limit,
        )
        open_total = int(summary.get("open_total") or 0)
        overdue_total = int(summary.get("overdue_total") or 0)
        due_soon_total = int(summary.get("due_soon_total") or 0)
        overdue_rate = round(overdue_total / open_total, 4) if open_total > 0 else 0.0
        activities = summary.get("activities") if isinstance(summary.get("activities"), list) else []
        overdue_blocking_rows = [
            row
            for row in activities
            if str(row.get("classification") or "") == "overdue" and bool(row.get("is_blocking"))
        ]
        blocking_overdue_total = len(overdue_blocking_rows)

        alerts: List[Dict[str, Any]] = []
        if overdue_rate > rate_warn:
            alerts.append(
                {
                    "code": "eco_activity_sla_overdue_rate_high",
                    "level": "warn",
                    "message": (
                        f"overdue_rate {overdue_rate:.4f} exceeds threshold {rate_warn:.4f}"
                    ),
                    "current": overdue_rate,
                    "threshold": rate_warn,
                }
            )
        if due_soon_total > due_soon_warn:
            alerts.append(
                {
                    "code": "eco_activity_sla_due_soon_count_high",
                    "level": "warn",
                    "message": (
                        f"due_soon_total {due_soon_total} exceeds threshold {due_soon_warn}"
                    ),
                    "current": due_soon_total,
                    "threshold": due_soon_warn,
                }
            )
        if blocking_overdue_total > blocking_warn:
            alerts.append(
                {
                    "code": "eco_activity_sla_blocking_overdue_high",
                    "level": "warn",
                    "message": (
                        f"blocking_overdue_total {blocking_overdue_total} exceeds threshold {blocking_warn}"
                    ),
                    "current": blocking_overdue_total,
                    "threshold": blocking_warn,
                }
            )

        return {
            "eco_id": eco_id,
            "status": "warning" if alerts else "ok",
            "generated_at": _utcnow().isoformat(),
            "thresholds": {
                "overdue_rate_warn": rate_warn,
                "due_soon_count_warn": due_soon_warn,
                "blocking_overdue_warn": blocking_warn,
            },
            "metrics": {
                "open_total": open_total,
                "overdue_total": overdue_total,
                "due_soon_total": due_soon_total,
                "blocking_overdue_total": blocking_overdue_total,
                "overdue_rate": overdue_rate,
            },
            "alerts": alerts,
            "summary": summary,
            "top_overdue_blocking": overdue_blocking_rows[:20],
        }

    def export_activity_sla_alerts(
        self,
        eco_id: str,
        *,
        now: Optional[datetime] = None,
        due_soon_hours: int = 24,
        include_closed: bool = False,
        assignee_id: Optional[int] = None,
        limit: int = 100,
        overdue_rate_warn: float = 0.2,
        due_soon_count_warn: int = 5,
        blocking_overdue_warn: int = 1,
        export_format: str = "json",
    ) -> Dict[str, Any]:
        alerts = self.activity_sla_alerts(
            eco_id,
            now=now,
            due_soon_hours=due_soon_hours,
            include_closed=include_closed,
            assignee_id=assignee_id,
            limit=limit,
            overdue_rate_warn=overdue_rate_warn,
            due_soon_count_warn=due_soon_count_warn,
            blocking_overdue_warn=blocking_overdue_warn,
        )
        normalized = str(export_format or "json").strip().lower()
        if normalized == "json":
            return {
                "content": json.dumps(alerts, ensure_ascii=False, indent=2).encode("utf-8"),
                "media_type": "application/json",
                "filename": "eco-activity-sla-alerts.json",
            }

        rows: List[Dict[str, Any]] = []
        metrics = alerts.get("metrics") if isinstance(alerts.get("metrics"), dict) else {}
        base = {
            "eco_id": eco_id,
            "status": alerts.get("status") or "ok",
            "open_total": int(metrics.get("open_total") or 0),
            "overdue_total": int(metrics.get("overdue_total") or 0),
            "due_soon_total": int(metrics.get("due_soon_total") or 0),
            "blocking_overdue_total": int(metrics.get("blocking_overdue_total") or 0),
            "overdue_rate": metrics.get("overdue_rate"),
        }
        for alert in alerts.get("alerts") or []:
            rows.append(
                {
                    **base,
                    "alert_code": alert.get("code") or "",
                    "alert_level": alert.get("level") or "",
                    "alert_message": alert.get("message") or "",
                    "current": alert.get("current"),
                    "threshold": alert.get("threshold"),
                }
            )
        if not rows:
            rows.append(
                {
                    **base,
                    "alert_code": "",
                    "alert_level": "",
                    "alert_message": "",
                    "current": "",
                    "threshold": "",
                }
            )

        if normalized == "csv":
            csv_io = io.StringIO()
            writer = csv.DictWriter(
                csv_io,
                fieldnames=[
                    "eco_id",
                    "status",
                    "open_total",
                    "overdue_total",
                    "due_soon_total",
                    "blocking_overdue_total",
                    "overdue_rate",
                    "alert_code",
                    "alert_level",
                    "alert_message",
                    "current",
                    "threshold",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": "eco-activity-sla-alerts.csv",
            }

        if normalized == "md":
            lines = [
                "# ECO Activity SLA Alerts",
                "",
                f"- eco_id: {eco_id}",
                f"- status: {alerts.get('status') or 'ok'}",
                f"- open_total: {base['open_total']}",
                f"- overdue_total: {base['overdue_total']}",
                f"- due_soon_total: {base['due_soon_total']}",
                f"- blocking_overdue_total: {base['blocking_overdue_total']}",
                f"- overdue_rate: {base['overdue_rate']}",
                "",
                "| Alert Code | Level | Current | Threshold | Message |",
                "| --- | --- | --- | --- | --- |",
            ]
            if alerts.get("alerts"):
                for alert in alerts.get("alerts") or []:
                    lines.append(
                        f"| {alert.get('code') or ''} | {alert.get('level') or ''} | "
                        f"{alert.get('current') if alert.get('current') is not None else ''} | "
                        f"{alert.get('threshold') if alert.get('threshold') is not None else ''} | "
                        f"{alert.get('message') or ''} |"
                    )
            else:
                lines.append("| <none> | <none> |  |  | no alerts |")
            return {
                "content": ("\n".join(lines) + "\n").encode("utf-8"),
                "media_type": "text/markdown",
                "filename": "eco-activity-sla-alerts.md",
            }

        raise ValueError("export_format must be json, csv or md")

    def _record_event(
        self,
        *,
        eco_id: str,
        activity_id: str,
        from_status: Optional[str],
        to_status: str,
        reason: Optional[str],
        user_id: Optional[int],
    ) -> ECOActivityGateEvent:
        event = ECOActivityGateEvent(
            id=_uuid(),
            eco_id=eco_id,
            activity_id=activity_id,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            user_id=user_id,
        )
        self.session.add(event)
        self.session.flush()
        return event


class WorkflowCustomActionService:
    _ALLOWED_TYPES = {"emit_event", "create_job", "set_eco_priority"}
    _ALLOWED_PHASES = {"before", "after"}
    _ALLOWED_FAIL = {"block", "warn", "retry"}
    _ALLOWED_MATCH_PREDICATES = {
        "stage_id",
        "eco_priority",
        "actor_roles",
        "product_id",
        "eco_type",
    }
    _ALLOWED_ECO_PRIORITIES = {"low", "normal", "high", "urgent"}
    _ALLOWED_ECO_TYPES = {"bom", "product", "document"}
    _RESULT_OK = "OK"
    _RESULT_WARN = "WARN"
    _RESULT_BLOCK = "BLOCK"
    _RESULT_RETRY_EXHAUSTED = "RETRY_EXHAUSTED"

    def __init__(self, session: Session):
        self.session = session
        self._job_service = JobService(session)

    def _normalize_retry_max(self, fail_strategy: str, params: Dict[str, Any]) -> int:
        if fail_strategy != "retry":
            return 0
        raw = params.get("max_retries", 1)
        try:
            value = int(raw)
        except Exception as exc:
            raise ValueError("max_retries must be an integer between 1 and 5") from exc
        if value < 1 or value > 5:
            raise ValueError("max_retries must be between 1 and 5 for retry strategy")
        return value

    def _normalize_priority(self, params: Dict[str, Any]) -> int:
        raw = params.get("priority", 100)
        try:
            value = int(raw)
        except Exception as exc:
            raise ValueError("priority must be an integer between 0 and 1000") from exc
        if value < 0 or value > 1000:
            raise ValueError("priority must be between 0 and 1000")
        return value

    def _normalize_timeout_s(self, params: Dict[str, Any]) -> float:
        raw = params.get("timeout_s", 5.0)
        try:
            value = float(raw)
        except Exception as exc:
            raise ValueError("timeout_s must be a number between 0.01 and 60") from exc
        if value < 0.01 or value > 60:
            raise ValueError("timeout_s must be between 0.01 and 60")
        return value

    def _normalize_action_params(
        self,
        params: Optional[Dict[str, Any]],
        fail_strategy: str,
        match_predicates: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized = dict(params or {})
        normalized["priority"] = self._normalize_priority(normalized)
        normalized["timeout_s"] = self._normalize_timeout_s(normalized)
        normalized["max_retries"] = self._normalize_retry_max(fail_strategy, normalized)
        raw_match_predicates = (
            match_predicates
            if match_predicates is not None
            else normalized.get("match_predicates")
        )
        normalized_match_predicates = self._normalize_match_predicates(raw_match_predicates)
        if normalized_match_predicates:
            normalized["match_predicates"] = normalized_match_predicates
        else:
            normalized.pop("match_predicates", None)
        return normalized

    def _normalize_optional_string(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _normalize_string_list(
        self,
        value: Any,
        *,
        field: str,
        lowercase: bool = False,
    ) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, (list, tuple, set)):
            raise ValueError(f"{field} must be an array of strings")
        seen = set()
        normalized: List[str] = []
        for raw in value:
            item = self._normalize_optional_string(raw)
            if not item:
                continue
            if lowercase:
                item = item.lower()
            if item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

    def _normalize_match_predicates(
        self, match_predicates: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if match_predicates is None:
            return {}
        if not isinstance(match_predicates, dict):
            raise ValueError("match_predicates must be an object")

        unsupported = sorted(
            key
            for key in match_predicates.keys()
            if key not in self._ALLOWED_MATCH_PREDICATES
        )
        if unsupported:
            raise ValueError(
                "match_predicates only supports: actor_roles, eco_priority, eco_type, product_id, stage_id"
            )

        normalized: Dict[str, Any] = {}
        stage_id = self._normalize_optional_string(match_predicates.get("stage_id"))
        if stage_id:
            normalized["stage_id"] = stage_id

        eco_priority = self._normalize_optional_string(
            match_predicates.get("eco_priority")
        )
        if eco_priority:
            eco_priority = eco_priority.lower()
            if eco_priority not in self._ALLOWED_ECO_PRIORITIES:
                raise ValueError(
                    "match_predicates.eco_priority must be one of: low, normal, high, urgent"
                )
            normalized["eco_priority"] = eco_priority

        actor_roles = self._normalize_string_list(
            match_predicates.get("actor_roles"),
            field="match_predicates.actor_roles",
            lowercase=True,
        )
        if actor_roles:
            normalized["actor_roles"] = actor_roles

        product_id = self._normalize_optional_string(match_predicates.get("product_id"))
        if product_id:
            normalized["product_id"] = product_id

        eco_type = self._normalize_optional_string(match_predicates.get("eco_type"))
        if eco_type:
            eco_type = eco_type.lower()
            if eco_type not in self._ALLOWED_ECO_TYPES:
                raise ValueError(
                    "match_predicates.eco_type must be one of: bom, product, document"
                )
            normalized["eco_type"] = eco_type

        return normalized

    def _normalize_runtime_context(self, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        normalized = dict(context or {})
        normalized["workflow_map_id"] = self._normalize_optional_string(
            normalized.get("workflow_map_id")
        )
        normalized["stage_id"] = self._normalize_optional_string(normalized.get("stage_id"))
        eco_priority = self._normalize_optional_string(normalized.get("eco_priority"))
        normalized["eco_priority"] = eco_priority.lower() if eco_priority else None
        normalized["product_id"] = self._normalize_optional_string(normalized.get("product_id"))
        eco_type = self._normalize_optional_string(normalized.get("eco_type"))
        normalized["eco_type"] = eco_type.lower() if eco_type else None
        normalized["actor_roles"] = self._normalize_string_list(
            normalized.get("actor_roles"),
            field="context.actor_roles",
            lowercase=True,
        )
        return normalized

    def _rule_matches_runtime_scope(
        self,
        *,
        rule: WorkflowCustomActionRule,
        context: Dict[str, Any],
    ) -> bool:
        # Delegate to the merged automation predicate contract
        # (`automation_rule_predicate_contract`, PR #577 `36ad043`).
        # The contract is the single source of truth for matcher
        # semantics; this body now exists only to thread rule data
        # into the contract's pure API. Bit-for-bit behavior is
        # pinned by the spec-derived 21-row
        # `_AUTOMATION_PARITY_SNAPSHOT` in the contract's tests
        # (taskbook PR #598 `397422e`, §5).
        from yuantus.meta_engine.services.automation_rule_predicate_contract import (
            WorkflowRuleFacts,
            evaluate_rule_predicate,
            normalize_workflow_rule_predicate,
        )

        params = rule.action_params if isinstance(rule.action_params, dict) else {}
        predicate = normalize_workflow_rule_predicate(
            rule.workflow_map_id,
            params.get("match_predicates"),
        )
        # `context` is already normalized by the caller
        # (`_normalize_runtime_context` at evaluate_transition).
        # `WorkflowRuleFacts.from_context` normalization is idempotent
        # on already-normalized input (lowercasing an already-lowercase
        # string is a no-op) — verified by the parity snapshot.
        facts = WorkflowRuleFacts.from_context(context)
        return evaluate_rule_predicate(predicate, facts)

    def _rule_priority(self, rule: WorkflowCustomActionRule) -> int:
        params = rule.action_params if isinstance(rule.action_params, dict) else {}
        try:
            return int(params.get("priority") or 100)
        except Exception:
            return 100

    def _rule_timeout(self, rule: WorkflowCustomActionRule) -> float:
        params = rule.action_params if isinstance(rule.action_params, dict) else {}
        value = params.get("timeout_s", 5.0)
        try:
            return float(value)
        except Exception:
            return 5.0

    def _rule_max_retries(self, rule: WorkflowCustomActionRule) -> int:
        params = rule.action_params if isinstance(rule.action_params, dict) else {}
        try:
            return int(params.get("max_retries") or 0)
        except Exception:
            return 0

    def _find_scope_conflicts(
        self,
        *,
        target_object: str,
        trigger_phase: str,
        from_state: Optional[str],
        to_state: Optional[str],
        workflow_map_id: Optional[str],
        exclude_rule_id: Optional[str] = None,
    ) -> List[WorkflowCustomActionRule]:
        query = self.session.query(WorkflowCustomActionRule).filter(
            WorkflowCustomActionRule.is_enabled.is_(True),
            WorkflowCustomActionRule.target_object == target_object,
            WorkflowCustomActionRule.trigger_phase == trigger_phase,
            WorkflowCustomActionRule.from_state == from_state,
            WorkflowCustomActionRule.to_state == to_state,
            WorkflowCustomActionRule.workflow_map_id == workflow_map_id,
        )
        rules = query.order_by(WorkflowCustomActionRule.name.asc()).all()
        if not exclude_rule_id:
            return rules
        return [rule for rule in rules if str(rule.id) != str(exclude_rule_id)]

    def create_rule(
        self,
        *,
        name: str,
        target_object: str,
        from_state: Optional[str],
        to_state: Optional[str],
        trigger_phase: str,
        action_type: str,
        action_params: Optional[Dict[str, Any]] = None,
        match_predicates: Optional[Dict[str, Any]] = None,
        fail_strategy: str = "block",
        workflow_map_id: Optional[str] = None,
        is_enabled: bool = True,
    ) -> WorkflowCustomActionRule:
        normalized_phase = (trigger_phase or "before").strip().lower()
        if normalized_phase not in self._ALLOWED_PHASES:
            raise ValueError("trigger_phase must be before or after")
        normalized_action = (action_type or "").strip().lower()
        if normalized_action not in self._ALLOWED_TYPES:
            raise ValueError(
                "action_type must be one of: emit_event, create_job, set_eco_priority"
            )
        normalized_fail = (fail_strategy or "block").strip().lower()
        if normalized_fail not in self._ALLOWED_FAIL:
            raise ValueError("fail_strategy must be one of: block, warn, retry")
        normalized_params = self._normalize_action_params(
            action_params,
            normalized_fail,
            match_predicates,
        )

        existing = (
            self.session.query(WorkflowCustomActionRule)
            .filter(WorkflowCustomActionRule.name == name)
            .first()
        )
        if existing:
            rule = existing
        else:
            rule = WorkflowCustomActionRule(id=_uuid(), name=name)
            self.session.add(rule)

        rule.target_object = (target_object or "ECO").strip().upper()
        rule.workflow_map_id = workflow_map_id
        rule.from_state = from_state
        rule.to_state = to_state
        rule.trigger_phase = normalized_phase
        rule.action_type = normalized_action
        conflicts = self._find_scope_conflicts(
            target_object=(target_object or "ECO").strip().upper(),
            trigger_phase=normalized_phase,
            from_state=from_state,
            to_state=to_state,
            workflow_map_id=workflow_map_id,
            exclude_rule_id=rule.id,
        )
        if conflicts:
            normalized_params["conflict_scope"] = {
                "count": len(conflicts),
                "rule_ids": [entry.id for entry in conflicts[:20]],
            }
        else:
            normalized_params.pop("conflict_scope", None)
        rule.action_params = normalized_params
        rule.fail_strategy = normalized_fail
        rule.is_enabled = bool(is_enabled)
        rule.updated_at = _utcnow()
        self.session.flush()
        return rule

    def list_rules(
        self,
        *,
        target_object: Optional[str] = None,
        enabled_only: bool = True,
    ) -> List[WorkflowCustomActionRule]:
        query = self.session.query(WorkflowCustomActionRule).order_by(
            WorkflowCustomActionRule.name.asc()
        )
        if enabled_only:
            query = query.filter(WorkflowCustomActionRule.is_enabled.is_(True))
        if target_object:
            query = query.filter(
                WorkflowCustomActionRule.target_object == target_object.strip().upper()
            )
        return query.all()

    def evaluate_transition(
        self,
        *,
        object_id: str,
        target_object: str,
        from_state: Optional[str],
        to_state: Optional[str],
        trigger_phase: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[WorkflowCustomActionRun]:
        normalized_target = (target_object or "ECO").strip().upper()
        normalized_phase = (trigger_phase or "before").strip().lower()
        if normalized_phase not in self._ALLOWED_PHASES:
            raise ValueError("trigger_phase must be before or after")

        query = self.session.query(WorkflowCustomActionRule).filter(
            WorkflowCustomActionRule.is_enabled.is_(True),
            WorkflowCustomActionRule.target_object == normalized_target,
            WorkflowCustomActionRule.trigger_phase == normalized_phase,
        )
        rules = query.order_by(WorkflowCustomActionRule.name.asc()).all()
        normalized_context = self._normalize_runtime_context(context)
        matched: List[WorkflowCustomActionRule] = []
        for rule in rules:
            if rule.from_state and str(rule.from_state) != str(from_state):
                continue
            if rule.to_state and str(rule.to_state) != str(to_state):
                continue
            if not self._rule_matches_runtime_scope(
                rule=rule,
                context=normalized_context,
            ):
                continue
            matched.append(rule)

        matched.sort(
            key=lambda rule: (
                self._rule_priority(rule),
                str(rule.name or ""),
                str(rule.id or ""),
            )
        )
        runs: List[WorkflowCustomActionRun] = []
        for idx, rule in enumerate(matched, start=1):
            run = self._execute_rule(
                rule=rule,
                object_id=object_id,
                target_object=normalized_target,
                from_state=from_state,
                to_state=to_state,
                trigger_phase=normalized_phase,
                context=normalized_context,
                execution_order=idx,
            )
            runs.append(run)
        return runs

    def _execute_rule(
        self,
        *,
        rule: WorkflowCustomActionRule,
        object_id: str,
        target_object: str,
        from_state: Optional[str],
        to_state: Optional[str],
        trigger_phase: str,
        context: Dict[str, Any],
        execution_order: int,
    ) -> WorkflowCustomActionRun:
        max_retries = self._rule_max_retries(rule)
        max_attempts = 1 + max_retries if rule.fail_strategy == "retry" else 1
        timeout_s = self._rule_timeout(rule)
        attempts = 0
        status = "completed"
        last_error = None
        result: Dict[str, Any] = {}
        result_code = self._RESULT_OK

        while attempts < max_attempts:
            attempts += 1
            started = time.monotonic()
            try:
                result = self._run_action(
                    rule=rule,
                    object_id=object_id,
                    target_object=target_object,
                    from_state=from_state,
                    to_state=to_state,
                    context=context,
                )
                elapsed_s = time.monotonic() - started
                if elapsed_s > timeout_s:
                    raise TimeoutError(
                        f"workflow custom action timeout: {elapsed_s:.3f}s > {timeout_s:.3f}s"
                    )
                status = "completed"
                last_error = None
                result_code = self._RESULT_OK
                break
            except Exception as exc:
                last_error = str(exc)
                if rule.fail_strategy == "retry" and attempts < max_attempts:
                    continue
                if rule.fail_strategy == "warn":
                    status = "warning"
                    result_code = self._RESULT_WARN
                elif rule.fail_strategy == "retry":
                    status = "failed"
                    result_code = self._RESULT_RETRY_EXHAUSTED
                else:
                    status = "failed"
                    result_code = self._RESULT_BLOCK

        if not isinstance(result, dict):
            result = {"value": result}
        result["result_code"] = result_code
        result["execution"] = {
            "order": int(execution_order),
            "priority": int(self._rule_priority(rule)),
            "timeout_s": float(timeout_s),
            "max_retries": int(max_retries),
        }
        if last_error:
            result["error"] = last_error

        run = WorkflowCustomActionRun(
            id=_uuid(),
            rule_id=rule.id,
            object_id=object_id,
            target_object=target_object,
            from_state=from_state,
            to_state=to_state,
            trigger_phase=trigger_phase,
            status=status,
            attempts=attempts,
            last_error=last_error,
            result=result,
        )
        self.session.add(run)
        self.session.flush()

        if status == "failed" and rule.fail_strategy == "block":
            raise ValueError(
                f"[{result_code}] {last_error or 'workflow custom action failed'}"
            )

        return run

    def _run_action(
        self,
        *,
        rule: WorkflowCustomActionRule,
        object_id: str,
        target_object: str,
        from_state: Optional[str],
        to_state: Optional[str],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        action = str(rule.action_type or "").strip().lower()
        params = rule.action_params or {}

        if action == "emit_event":
            return {
                "event": params.get("event") or "workflow.transition",
                "object_id": object_id,
                "target_object": target_object,
                "from_state": from_state,
                "to_state": to_state,
                "context": context,
            }

        if action == "create_job":
            task_type = str(params.get("task_type") or "workflow_action")
            payload = {
                "rule_id": rule.id,
                "object_id": object_id,
                "target_object": target_object,
                "from_state": from_state,
                "to_state": to_state,
                "context": context,
                "params": params,
            }
            job = self._job_service.create_job(
                task_type=task_type,
                payload=payload,
                priority=int(params.get("priority") or 10),
                dedupe=True,
                dedupe_key=f"wf-action:{rule.id}:{object_id}:{from_state}:{to_state}",
            )
            return {"job_id": job.id, "task_type": job.task_type}

        if action == "set_eco_priority":
            if target_object != "ECO":
                raise ValueError("set_eco_priority only supports ECO target_object")
            eco = self.session.get(ECO, object_id)
            if not eco:
                raise ValueError(f"ECO not found: {object_id}")
            priority = str(params.get("priority") or "").strip().lower()
            if priority not in {"low", "normal", "high", "urgent"}:
                raise ValueError("priority must be one of: low, normal, high, urgent")
            eco.priority = priority
            eco.updated_at = _utcnow()
            self.session.add(eco)
            self.session.flush()
            return {"eco_id": eco.id, "priority": eco.priority}

        raise ValueError(f"Unsupported action_type: {action}")


class ConsumptionPlanService:
    def __init__(self, session: Session):
        self.session = session

    def _template_key_from_plan(self, plan: ConsumptionPlan) -> Optional[str]:
        props = plan.properties if isinstance(plan.properties, dict) else {}
        template = props.get("template")
        if not isinstance(template, dict):
            return None
        key = str(template.get("key") or "").strip()
        return key or None

    def _template_meta_from_plan(self, plan: ConsumptionPlan) -> Dict[str, Any]:
        props = plan.properties if isinstance(plan.properties, dict) else {}
        template = props.get("template")
        if not isinstance(template, dict):
            template = {}
        return {
            "key": str(template.get("key") or "").strip() or None,
            "version": str(template.get("version") or "").strip() or None,
            "is_template_version": bool(template.get("is_template_version")),
            "is_active": bool(template.get("is_active")),
        }

    def _plans_for_template(self, template_key: str) -> List[ConsumptionPlan]:
        key = str(template_key or "").strip()
        if not key:
            return []
        plans = (
            self.session.query(ConsumptionPlan)
            .order_by(ConsumptionPlan.created_at.desc())
            .all()
        )
        return [
            plan
            for plan in plans
            if self._template_key_from_plan(plan) == key
            and bool(self._template_meta_from_plan(plan).get("is_template_version"))
        ]

    def create_plan(
        self,
        *,
        name: str,
        planned_quantity: float,
        uom: str = "EA",
        period_unit: str = "week",
        item_id: Optional[str] = None,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        created_by_id: Optional[int] = None,
        properties: Optional[Dict[str, Any]] = None,
        state: str = "active",
    ) -> ConsumptionPlan:
        plan = ConsumptionPlan(
            id=_uuid(),
            name=name,
            state=(state or "active").strip().lower(),
            planned_quantity=float(planned_quantity),
            uom=(uom or "EA").strip().upper(),
            period_unit=(period_unit or "week").strip().lower(),
            item_id=item_id,
            period_start=period_start,
            period_end=period_end,
            created_by_id=created_by_id,
            properties=properties or {},
        )
        self.session.add(plan)
        self.session.flush()
        return plan

    def create_template_version(
        self,
        *,
        template_key: str,
        name: str,
        planned_quantity: float,
        version_label: Optional[str] = None,
        uom: str = "EA",
        period_unit: str = "week",
        item_id: Optional[str] = None,
        activate: bool = True,
        created_by_id: Optional[int] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> ConsumptionPlan:
        key = str(template_key or "").strip()
        if not key:
            raise ValueError("template_key must not be empty")

        existing = self._plans_for_template(key)
        if version_label:
            normalized_version = str(version_label).strip()
        else:
            normalized_version = f"v{len(existing) + 1}"

        props = dict(properties or {})
        template_props = props.get("template")
        if not isinstance(template_props, dict):
            template_props = {}
        template_props.update(
            {
                "key": key,
                "version": normalized_version,
                "is_template_version": True,
                "is_active": bool(activate),
            }
        )
        props["template"] = template_props

        plan = self.create_plan(
            name=name,
            planned_quantity=planned_quantity,
            uom=uom,
            period_unit=period_unit,
            item_id=item_id,
            created_by_id=created_by_id,
            properties=props,
            state="active" if activate else "inactive",
        )
        if activate:
            for other in existing:
                if other.id == plan.id:
                    continue
                other_props = (
                    dict(other.properties) if isinstance(other.properties, dict) else {}
                )
                other_template = other_props.get("template")
                if not isinstance(other_template, dict):
                    other_template = {}
                other_template["is_active"] = False
                other_props["template"] = other_template
                other.properties = other_props
                other.state = "inactive"
                other.updated_at = _utcnow()
                self.session.add(other)
        self.session.flush()
        return plan

    def list_template_versions(
        self, template_key: str, *, include_inactive: bool = True
    ) -> List[Dict[str, Any]]:
        plans = self._plans_for_template(template_key)
        rows: List[Dict[str, Any]] = []
        for plan in plans:
            meta = self._template_meta_from_plan(plan)
            if not include_inactive and not meta.get("is_active"):
                continue
            rows.append(
                {
                    "id": plan.id,
                    "name": plan.name,
                    "state": plan.state,
                    "planned_quantity": float(plan.planned_quantity or 0.0),
                    "uom": plan.uom,
                    "period_unit": plan.period_unit,
                    "item_id": plan.item_id,
                    "template": meta,
                    "created_at": plan.created_at.isoformat() if plan.created_at else None,
                    "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
                }
            )
        return rows

    def set_template_version_state(
        self,
        plan_id: str,
        *,
        activate: bool,
    ) -> ConsumptionPlan:
        plan = self.session.get(ConsumptionPlan, plan_id)
        if not plan:
            raise ValueError(f"Consumption plan not found: {plan_id}")
        meta = self._template_meta_from_plan(plan)
        key = meta.get("key")
        if not key or not meta.get("is_template_version"):
            raise ValueError("plan is not a template version")

        props = dict(plan.properties or {})
        template = props.get("template")
        if not isinstance(template, dict):
            template = {}
        template["is_active"] = bool(activate)
        props["template"] = template
        plan.properties = props
        plan.state = "active" if activate else "inactive"
        plan.updated_at = _utcnow()
        self.session.add(plan)

        if activate:
            for other in self._plans_for_template(str(key)):
                if other.id == plan.id:
                    continue
                other_props = (
                    dict(other.properties) if isinstance(other.properties, dict) else {}
                )
                other_template = other_props.get("template")
                if not isinstance(other_template, dict):
                    other_template = {}
                other_template["is_active"] = False
                other_props["template"] = other_template
                other.properties = other_props
                other.state = "inactive"
                other.updated_at = _utcnow()
                self.session.add(other)
        self.session.flush()
        return plan

    def preview_template_impact(
        self,
        *,
        template_key: str,
        planned_quantity: float,
        uom: Optional[str] = None,
        period_unit: Optional[str] = None,
    ) -> Dict[str, Any]:
        versions = self.list_template_versions(template_key, include_inactive=True)
        active = next(
            (row for row in versions if bool((row.get("template") or {}).get("is_active"))),
            None,
        )
        candidate_qty = float(planned_quantity)
        baseline_qty = float(active.get("planned_quantity") or 0.0) if active else 0.0
        delta_qty = candidate_qty - baseline_qty

        impacts = []
        for row in versions:
            row_qty = float(row.get("planned_quantity") or 0.0)
            impacts.append(
                {
                    "plan_id": row.get("id"),
                    "name": row.get("name"),
                    "template_version": (row.get("template") or {}).get("version"),
                    "is_active": bool((row.get("template") or {}).get("is_active")),
                    "current_quantity": row_qty,
                    "candidate_quantity": candidate_qty,
                    "delta_quantity": candidate_qty - row_qty,
                }
            )

        return {
            "template_key": str(template_key),
            "candidate": {
                "planned_quantity": candidate_qty,
                "uom": (uom or (active.get("uom") if active else "EA")),
                "period_unit": (period_unit or (active.get("period_unit") if active else "week")),
            },
            "active_version": active,
            "summary": {
                "versions_total": len(versions),
                "impacted_versions": len(impacts),
                "baseline_quantity": baseline_qty,
                "candidate_quantity": candidate_qty,
                "delta_quantity": delta_qty,
            },
            "impacts": impacts,
        }

    def list_plans(
        self, *, state: Optional[str] = None, item_id: Optional[str] = None
    ) -> List[ConsumptionPlan]:
        query = self.session.query(ConsumptionPlan).order_by(ConsumptionPlan.created_at.desc())
        if state:
            query = query.filter(ConsumptionPlan.state == state)
        if item_id:
            query = query.filter(ConsumptionPlan.item_id == item_id)
        return query.all()

    def add_actual(
        self,
        *,
        plan_id: str,
        actual_quantity: float,
        source_type: str = "workorder",
        source_id: Optional[str] = None,
        recorded_at: Optional[datetime] = None,
        properties: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> ConsumptionRecord:
        # `idempotency_key` defaults to None so the manual `/actuals` path is
        # byte-for-byte unchanged (NULL key, never deduped). The MES ingestion
        # path passes the R1-derived key; the unique column is what makes a
        # retried MES delivery idempotent. The 1:1 mapper alignment is enforced
        # by the drift tests in test_consumption_mes_ingestion_contract.py.
        plan = self.session.get(ConsumptionPlan, plan_id)
        if not plan:
            raise ValueError(f"Consumption plan not found: {plan_id}")
        record = ConsumptionRecord(
            id=_uuid(),
            plan_id=plan.id,
            source_type=(source_type or "workorder").strip().lower(),
            source_id=source_id,
            actual_quantity=float(actual_quantity),
            recorded_at=recorded_at or _utcnow(),
            properties=properties or {},
            idempotency_key=idempotency_key,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def ingest_mes_consumption(
        self, inputs: "ConsumptionRecordInputs"
    ) -> "tuple[ConsumptionRecord, str]":
        """Idempotent MES consumption ingest. Returns ``(record, disposition)``
        where disposition is ``CREATED`` / ``DUPLICATE`` / ``CONFLICT``.

        Race-safe by construction: it INSERTs inside a SAVEPOINT and lets the
        unique ``idempotency_key`` constraint be the arbiter (never a
        look-then-insert pre-check). On the unique violation ``begin_nested()``
        rolls back **only to the savepoint**, re-reads the winning row by key,
        and compares the **business payload** (``actual_quantity`` + ``source_id``
        -- the latter is NOT in the key, so a same-key attribution change must
        surface as a conflict, not be silently kept). Equal -> ``DUPLICATE``;
        divergent -> ``CONFLICT``. A missing plan raises ``ValueError`` before
        any insert.

        Transaction safety: because the failed insert is unwound to a SAVEPOINT
        (not a full ``session.rollback()``), this method is safe to call inside a
        larger caller transaction -- a DUPLICATE/CONFLICT does NOT discard the
        caller's other uncommitted writes. The caller still owns the final
        commit/rollback (the route commits CREATED/DUPLICATE and 409s CONFLICT).
        """
        from sqlalchemy.exc import IntegrityError

        try:
            with self.session.begin_nested():
                record = self.add_actual(**inputs.as_kwargs())
        except IntegrityError:
            # begin_nested() already rolled back to the SAVEPOINT; the outer
            # transaction (and any prior caller writes) is intact and usable.
            existing = (
                self.session.query(ConsumptionRecord)
                .filter(ConsumptionRecord.idempotency_key == inputs.idempotency_key)
                .one_or_none()
            )
            if existing is None:
                # Pathological (constraint fired but no row visible): do not
                # swallow it as a success.
                raise
            same_payload = (
                float(existing.actual_quantity or 0.0) == float(inputs.actual_quantity)
                and (existing.source_id or None) == (inputs.source_id or None)
            )
            return existing, ("DUPLICATE" if same_payload else "CONFLICT")
        return record, "CREATED"

    def variance(self, plan_id: str) -> Dict[str, Any]:
        plan = self.session.get(ConsumptionPlan, plan_id)
        if not plan:
            raise ValueError(f"Consumption plan not found: {plan_id}")
        records = (
            self.session.query(ConsumptionRecord)
            .filter(ConsumptionRecord.plan_id == plan_id)
            .all()
        )
        actual_total = sum(float(record.actual_quantity or 0.0) for record in records)
        planned = float(plan.planned_quantity or 0.0)
        delta = actual_total - planned
        delta_ratio = (delta / planned) if planned else None
        return {
            "plan_id": plan_id,
            "planned_quantity": planned,
            "actual_quantity": actual_total,
            "delta_quantity": delta,
            "delta_ratio": delta_ratio,
            "uom": plan.uom,
            "records": len(records),
        }

    def dashboard(self, *, item_id: Optional[str] = None) -> Dict[str, Any]:
        plans = self.list_plans(item_id=item_id)
        rows = []
        for plan in plans:
            row = self.variance(plan.id)
            row["name"] = plan.name
            row["state"] = plan.state
            row["item_id"] = plan.item_id
            rows.append(row)
        return {"total": len(rows), "plans": rows}


class BreakageIncidentService:
    _HELPDESK_PROVIDER_STATUS_ALIASES = {
        "todo": "open",
        "new": "open",
        "triage": "open",
        "assigned": "in_progress",
        "working": "in_progress",
        "wip": "in_progress",
        "fixed": "resolved",
        "done": "resolved",
        "completed": "resolved",
        "cancelled": "canceled",
        "error": "failed",
    }
    _HELPDESK_PROVIDER_TO_INCIDENT_STATUS = {
        "open": "open",
        "pending": "open",
        "in_progress": "in_progress",
        "on_hold": "in_progress",
        "resolved": "resolved",
        "closed": "closed",
        "canceled": "closed",
        "failed": "open",
    }
    _HELPDESK_PROVIDER_TO_SYNC_STATUS = {
        "open": "queued",
        "pending": "queued",
        "in_progress": "in_progress",
        "on_hold": "in_progress",
        "resolved": "completed",
        "closed": "completed",
        "canceled": "failed",
        "failed": "failed",
    }

    def __init__(self, session: Session):
        self.session = session
        self._job_service = JobService(session)
        self._helpdesk_task_type = "breakage_helpdesk_sync_stub"
        self._incidents_export_task_type = "breakage_incidents_export"
        self._incidents_export_cleanup_task_type = "breakage_incidents_export_cleanup"
        self._group_by_fields = {
            "bom_id": "bom_id",
            "product_item_id": "product_item_id",
            "batch_code": "batch_code",
            "bom_line_item_id": "bom_line_item_id",
            "mbom_id": "mbom_id",
            "responsibility": "responsibility",
            "routing_id": "routing_id",
        }

    def _normalize_trend_window_days(self, window_days: int) -> int:
        allowed = {7, 14, 30}
        try:
            value = int(window_days)
        except Exception as exc:
            raise ValueError("trend_window_days must be one of: 7, 14, 30") from exc
        if value not in allowed:
            raise ValueError("trend_window_days must be one of: 7, 14, 30")
        return value

    def _normalize_page(self, page: int) -> int:
        value = int(page or 1)
        return 1 if value < 1 else value

    def _normalize_page_size(self, page_size: int) -> int:
        value = int(page_size or 20)
        if value < 1:
            value = 1
        if value > 200:
            value = 200
        return value

    def _normalize_group_by(self, group_by: str) -> str:
        value = str(group_by or "").strip().lower()
        if value not in self._group_by_fields:
            allowed = ", ".join(sorted(self._group_by_fields.keys()))
            raise ValueError(f"group_by must be one of: {allowed}")
        return value

    def _normalize_export_format(self, export_format: str) -> str:
        normalized = str(export_format or "json").strip().lower()
        if normalized not in {"json", "csv", "md"}:
            raise ValueError("export_format must be json, csv or md")
        return normalized

    def _next_incident_code(self) -> str:
        max_sequence = 0
        for (raw_code,) in self.session.query(BreakageIncident.incident_code).all():
            normalized = str(raw_code or "").strip().upper()
            if not normalized.startswith("BRK-"):
                continue
            suffix = normalized[4:]
            if suffix.isdigit():
                max_sequence = max(max_sequence, int(suffix))
        return f"BRK-{max_sequence + 1:06d}"

    def _resolve_incident_dimensions(
        self,
        *,
        bom_id: Optional[str] = None,
        mbom_id: Optional[str] = None,
        version_id: Optional[str] = None,
        routing_id: Optional[str] = None,
        production_order_id: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        normalized_bom_id = str(bom_id or "").strip() or None
        normalized_mbom_id = str(mbom_id or "").strip() or None
        normalized_version_id = str(version_id or "").strip() or None
        normalized_routing_id = str(routing_id or "").strip() or None
        normalized_production_order_id = (
            str(production_order_id or "").strip() or None
        )

        if (
            normalized_mbom_id
            and normalized_version_id
            and normalized_mbom_id != normalized_version_id
        ):
            raise ValueError("mbom_id and version_id must match when both are provided")
        if (
            normalized_routing_id
            and normalized_production_order_id
            and normalized_routing_id != normalized_production_order_id
        ):
            raise ValueError(
                "routing_id and production_order_id must match when both are provided"
            )

        normalized_mbom = normalized_mbom_id or normalized_version_id
        normalized_routing = normalized_routing_id or normalized_production_order_id
        return {
            "bom_id": normalized_bom_id,
            "mbom_id": normalized_mbom,
            "version_id": normalized_mbom,
            "routing_id": normalized_routing,
            "production_order_id": normalized_routing,
        }

    def _incident_code(self, incident: BreakageIncident) -> Optional[str]:
        normalized = str(getattr(incident, "incident_code", None) or "").strip()
        if normalized:
            return normalized
        incident_id = str(getattr(incident, "id", None) or "").strip()
        if not incident_id:
            return None
        compact = incident_id.replace("-", "").upper()
        return f"BRK-{compact[:8]}"

    def _incident_bom_id(self, incident: BreakageIncident) -> Optional[str]:
        return str(getattr(incident, "bom_id", None) or "").strip() or None

    def _incident_mbom_id(self, incident: BreakageIncident) -> Optional[str]:
        return (
            str(
                getattr(incident, "mbom_id", None)
                or getattr(incident, "version_id", None)
                or ""
            ).strip()
            or None
        )

    def _incident_routing_id(self, incident: BreakageIncident) -> Optional[str]:
        return (
            str(
                getattr(incident, "routing_id", None)
                or getattr(incident, "production_order_id", None)
                or ""
            ).strip()
            or None
        )

    def _apply_incident_filters(
        self,
        incidents: List[BreakageIncident],
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        product_item_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        responsibility: Optional[str] = None,
    ) -> List[BreakageIncident]:
        filtered: List[BreakageIncident] = []
        normalized_status = (status or "").strip().lower()
        normalized_severity = (severity or "").strip().lower()
        normalized_product = str(product_item_id or "").strip()
        normalized_bom_line = str(bom_line_item_id or "").strip()
        normalized_batch = str(batch_code or "").strip()
        normalized_resp = str(responsibility or "").strip().lower()

        for incident in incidents:
            if normalized_status and str(incident.status or "").lower() != normalized_status:
                continue
            if normalized_severity and str(incident.severity or "").lower() != normalized_severity:
                continue
            if normalized_product and str(incident.product_item_id or "") != normalized_product:
                continue
            if normalized_bom_line and str(incident.bom_line_item_id or "") != normalized_bom_line:
                continue
            if normalized_batch and str(incident.batch_code or "") != normalized_batch:
                continue
            if normalized_resp and str(incident.responsibility or "").strip().lower() != normalized_resp:
                continue
            filtered.append(incident)
        return filtered

    def create_incident(
        self,
        *,
        description: str,
        severity: str = "medium",
        status: str = "open",
        product_item_id: Optional[str] = None,
        bom_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        routing_id: Optional[str] = None,
        mbom_id: Optional[str] = None,
        production_order_id: Optional[str] = None,
        version_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        customer_name: Optional[str] = None,
        responsibility: Optional[str] = None,
        created_by_id: Optional[int] = None,
    ) -> BreakageIncident:
        dimensions = self._resolve_incident_dimensions(
            bom_id=bom_id,
            mbom_id=mbom_id,
            version_id=version_id,
            routing_id=routing_id,
            production_order_id=production_order_id,
        )
        incident = BreakageIncident(
            id=_uuid(),
            incident_code=self._next_incident_code(),
            description=description.strip(),
            severity=(severity or "medium").strip().lower(),
            status=(status or "open").strip().lower(),
            product_item_id=product_item_id,
            bom_id=dimensions["bom_id"],
            bom_line_item_id=bom_line_item_id,
            production_order_id=dimensions["production_order_id"],
            version_id=dimensions["version_id"],
            mbom_id=dimensions["mbom_id"],
            routing_id=dimensions["routing_id"],
            batch_code=batch_code,
            customer_name=customer_name,
            responsibility=responsibility,
            created_by_id=created_by_id,
        )
        self.session.add(incident)
        self.session.flush()
        return incident

    def get_incident(self, incident_id: str) -> Optional[BreakageIncident]:
        return self.session.get(BreakageIncident, incident_id)

    def list_incidents(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        product_item_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        responsibility: Optional[str] = None,
    ) -> List[BreakageIncident]:
        incidents = (
            self.session.query(BreakageIncident)
            .order_by(BreakageIncident.created_at.desc())
            .all()
        )
        return self._apply_incident_filters(
            incidents,
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
        )

    def _serialize_incident(self, incident: BreakageIncident) -> Dict[str, Any]:
        incident_code = self._incident_code(incident)
        bom_id = self._incident_bom_id(incident)
        mbom_id = self._incident_mbom_id(incident)
        routing_id = self._incident_routing_id(incident)
        return {
            "id": incident.id,
            "incident_code": incident_code,
            "description": incident.description,
            "severity": incident.severity,
            "status": incident.status,
            "product_item_id": incident.product_item_id,
            "bom_id": bom_id,
            "bom_line_item_id": incident.bom_line_item_id,
            "production_order_id": routing_id,
            "version_id": mbom_id,
            "mbom_id": mbom_id,
            "routing_id": routing_id,
            "batch_code": incident.batch_code,
            "customer_name": incident.customer_name,
            "responsibility": incident.responsibility,
            "created_at": incident.created_at.isoformat() if incident.created_at else None,
            "updated_at": incident.updated_at.isoformat() if incident.updated_at else None,
        }

    def _summarize_latest_helpdesk_job(
        self, job_view: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "job_id": job_view.get("id"),
            "job_status": job_view.get("status"),
            "sync_status": job_view.get("sync_status"),
            "provider": job_view.get("provider"),
            "external_ticket_id": job_view.get("external_ticket_id"),
            "provider_ticket_status": job_view.get("provider_ticket_status"),
            "provider_assignee": job_view.get("provider_assignee"),
            "provider_ticket_updated_at": job_view.get("provider_ticket_updated_at"),
            "failure_category": job_view.get("failure_category"),
            "last_error": job_view.get("last_error"),
            "updated_at": (
                job_view.get("completed_at")
                or job_view.get("started_at")
                or job_view.get("created_at")
            ),
        }

    def build_latest_helpdesk_summary_map(
        self, incident_ids: Iterable[str]
    ) -> Dict[str, Dict[str, Any]]:
        incident_id_set = {
            str(incident_id or "").strip()
            for incident_id in incident_ids
            if str(incident_id or "").strip()
        }
        if not incident_id_set:
            return {}

        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type == self._helpdesk_task_type)
            .order_by(ConversionJob.created_at.desc())
            .all()
        )
        summary_by_incident: Dict[str, Dict[str, Any]] = {}
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            incident_id = str(payload.get("incident_id") or "").strip()
            if (
                not incident_id
                or incident_id not in incident_id_set
                or incident_id in summary_by_incident
            ):
                continue
            job_view = self._build_helpdesk_sync_job_view(job)
            summary_by_incident[incident_id] = self._summarize_latest_helpdesk_job(
                job_view
            )
        return summary_by_incident

    def serialize_incident_view(
        self,
        incident: BreakageIncident,
        *,
        helpdesk_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        row = self._serialize_incident(incident)
        row["helpdesk_ticket_summary"] = (
            dict(helpdesk_summary) if isinstance(helpdesk_summary, dict) else None
        )
        return row

    def export_incidents(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        product_item_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        responsibility: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        export_format: str = "json",
        report_lang: Optional[str] = None,
        report_type: Optional[str] = None,
        locale_profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        incidents_all = self.list_incidents(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
        )
        current_page = self._normalize_page(page)
        current_page_size = self._normalize_page_size(page_size)
        total = len(incidents_all)
        total_pages = max(1, (total + current_page_size - 1) // current_page_size)
        if current_page > total_pages:
            current_page = total_pages
        offset = (current_page - 1) * current_page_size
        incidents_page = incidents_all[offset : offset + current_page_size]
        helpdesk_summary_by_incident = self.build_latest_helpdesk_summary_map(
            incident.id for incident in incidents_page
        )
        serialized = [
            self.serialize_incident_view(
                incident,
                helpdesk_summary=helpdesk_summary_by_incident.get(str(incident.id)),
            )
            for incident in incidents_page
        ]
        exported = {
            "total": total,
            "filters": {
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
            },
            "pagination": {
                "page": current_page,
                "page_size": current_page_size,
                "pages": total_pages,
                "total": total,
            },
            "incidents": serialized,
        }
        locale_context = _resolve_report_locale_context(
            self.session,
            locale_profile_id=locale_profile_id,
            report_lang=report_lang,
            report_type=report_type or "breakage_incidents",
        )
        normalized = str(export_format or "json").strip().lower()
        if normalized == "json":
            payload = dict(exported)
            if locale_context:
                payload["locale"] = locale_context
            return {
                "content": json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                "media_type": "application/json",
                "filename": "breakage-incidents.json",
            }
        if normalized == "csv":
            csv_io = io.StringIO()
            writer = csv.DictWriter(
                csv_io,
                fieldnames=[
                    "id",
                    "incident_code",
                    "description",
                    "severity",
                    "status",
                    "product_item_id",
                    "bom_id",
                    "bom_line_item_id",
                    "production_order_id",
                    "version_id",
                    "mbom_id",
                    "routing_id",
                    "batch_code",
                    "customer_name",
                    "responsibility",
                    "helpdesk_external_ticket_id",
                    "helpdesk_provider",
                    "helpdesk_provider_ticket_status",
                    "helpdesk_provider_assignee",
                    "helpdesk_sync_status",
                    "helpdesk_job_id",
                    "created_at",
                    "updated_at",
                    "status_filter",
                    "severity_filter",
                    "product_item_id_filter",
                    "bom_line_item_id_filter",
                    "batch_code_filter",
                    "responsibility_filter",
                ],
            )
            writer.writeheader()
            for row in serialized:
                helpdesk_summary = (
                    row.get("helpdesk_ticket_summary")
                    if isinstance(row.get("helpdesk_ticket_summary"), dict)
                    else {}
                )
                writer.writerow(
                    {
                        **{k: v for k, v in row.items() if k != "helpdesk_ticket_summary"},
                        "helpdesk_external_ticket_id": helpdesk_summary.get(
                            "external_ticket_id"
                        ),
                        "helpdesk_provider": helpdesk_summary.get("provider"),
                        "helpdesk_provider_ticket_status": helpdesk_summary.get(
                            "provider_ticket_status"
                        ),
                        "helpdesk_provider_assignee": helpdesk_summary.get(
                            "provider_assignee"
                        ),
                        "helpdesk_sync_status": helpdesk_summary.get("sync_status"),
                        "helpdesk_job_id": helpdesk_summary.get("job_id"),
                        "status_filter": status,
                        "severity_filter": severity,
                        "product_item_id_filter": product_item_id,
                        "bom_line_item_id_filter": bom_line_item_id,
                        "batch_code_filter": batch_code,
                        "responsibility_filter": responsibility,
                    }
                )
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": "breakage-incidents.csv",
            }
        if normalized == "md":
            lines = [
                "# Breakage Incidents",
                "",
                f"- total: {total}",
                f"- page: {current_page}",
                f"- page_size: {current_page_size}",
                f"- filters: {json.dumps(exported['filters'], ensure_ascii=False)}",
                "",
            ]
            if locale_context:
                lines.extend(
                    [
                        "## Locale",
                        "",
                        f"- lang: {locale_context.get('lang') or ''}",
                        f"- profile_id: {locale_context.get('id') or ''}",
                        f"- report_type: {locale_context.get('report_type') or locale_context.get('requested_report_type') or ''}",
                        f"- timezone: {locale_context.get('timezone') or ''}",
                        "",
                    ]
                )
            lines.extend(
                [
                    "| Code | ID | Status | Severity | Product | BOM | MBOM | Routing | BOM Line | Batch | Responsibility | Ticket | Provider | Ticket Status | Assignee | Sync |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for row in serialized:
                helpdesk_summary = (
                    row.get("helpdesk_ticket_summary")
                    if isinstance(row.get("helpdesk_ticket_summary"), dict)
                    else {}
                )
                lines.append(
                    f"| {row['incident_code'] or ''} | {row['id'] or ''} | {row['status'] or ''} | "
                    f"{row['severity'] or ''} | {row['product_item_id'] or ''} | {row['bom_id'] or ''} | "
                    f"{row['mbom_id'] or ''} | {row['routing_id'] or ''} | {row['bom_line_item_id'] or ''} | "
                    f"{row['batch_code'] or ''} | {row['responsibility'] or ''} | "
                    f"{helpdesk_summary.get('external_ticket_id') or ''} | {helpdesk_summary.get('provider') or ''} | "
                    f"{helpdesk_summary.get('provider_ticket_status') or ''} | {helpdesk_summary.get('provider_assignee') or ''} | "
                    f"{helpdesk_summary.get('sync_status') or ''} |"
                )
            return {
                "content": ("\n".join(lines) + "\n").encode("utf-8"),
                "media_type": "text/markdown",
                "filename": "breakage-incidents.md",
            }
        raise ValueError("export_format must be json, csv or md")

    def _build_incidents_export_job_dedupe_key(
        self,
        *,
        status: Optional[str],
        severity: Optional[str],
        product_item_id: Optional[str],
        bom_line_item_id: Optional[str],
        batch_code: Optional[str],
        responsibility: Optional[str],
        page: int,
        page_size: int,
        export_format: str,
    ) -> str:
        token = _stable_hash(
            [
                str(status or ""),
                str(severity or ""),
                str(product_item_id or ""),
                str(bom_line_item_id or ""),
                str(batch_code or ""),
                str(responsibility or ""),
                str(page),
                str(page_size),
                str(export_format),
            ]
        )
        return f"breakage-incidents-export:{token}"

    def _get_incidents_export_job(self, job_id: str) -> ConversionJob:
        job = self.session.get(ConversionJob, job_id)
        if not job or str(job.task_type or "") != self._incidents_export_task_type:
            raise ValueError(f"Breakage incidents export job not found: {job_id}")
        return job

    def _build_incidents_export_job_view(self, job: ConversionJob) -> Dict[str, Any]:
        payload = job.payload if isinstance(job.payload, dict) else {}
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        export_info = payload.get("export") if isinstance(payload.get("export"), dict) else {}
        export_result = (
            payload.get("export_result")
            if isinstance(payload.get("export_result"), dict)
            else {}
        )
        size_bytes = export_result.get("size_bytes")
        try:
            result_size = int(size_bytes) if size_bytes is not None else None
        except Exception:
            result_size = None
        attempt_count = int(job.attempt_count or 0)
        max_attempts = int(job.max_attempts or 0)
        return {
            "job_id": job.id,
            "task_type": job.task_type,
            "status": job.status,
            "sync_status": (
                str(export_info.get("sync_status") or "").strip().lower()
                or str(job.status or "").strip().lower()
            ),
            "filters": filters,
            "pagination": pagination,
            "export_format": payload.get("export_format"),
            "filename": export_result.get("filename"),
            "media_type": export_result.get("media_type"),
            "result_size_bytes": result_size,
            "download_ready": bool(
                str(job.status or "") == JobStatus.COMPLETED.value
                and export_result.get("content_b64")
            ),
            "last_error": job.last_error,
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "retry_budget": {
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
                "remaining_attempts": max(max_attempts - attempt_count, 0),
            },
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    def enqueue_incidents_export_job(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        product_item_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        responsibility: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        export_format: str = "json",
        execute_immediately: bool = False,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        current_page = self._normalize_page(page)
        current_page_size = self._normalize_page_size(page_size)
        normalized_format = self._normalize_export_format(export_format)
        payload = {
            "filters": {
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
            },
            "pagination": {
                "page": current_page,
                "page_size": current_page_size,
            },
            "export_format": normalized_format,
            "export": {
                "sync_status": "queued",
                "created_at": _utcnow().isoformat(),
                "created_by_id": user_id,
            },
        }
        dedupe_key = self._build_incidents_export_job_dedupe_key(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            page=current_page,
            page_size=current_page_size,
            export_format=normalized_format,
        )
        job = self._job_service.create_job(
            task_type=self._incidents_export_task_type,
            payload=payload,
            user_id=user_id,
            dedupe=True,
            dedupe_key=dedupe_key,
        )
        if execute_immediately:
            self.execute_incidents_export_job(job.id, user_id=user_id)
            refreshed = self.session.get(ConversionJob, job.id)
            if refreshed:
                job = refreshed
        return self._build_incidents_export_job_view(job)

    def execute_incidents_export_job(
        self,
        job_id: str,
        *,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        job = self._get_incidents_export_job(job_id)
        payload = job.payload if isinstance(job.payload, dict) else {}
        export_result = (
            payload.get("export_result")
            if isinstance(payload.get("export_result"), dict)
            else {}
        )
        if (
            str(job.status or "") == JobStatus.COMPLETED.value
            and export_result.get("content_b64")
        ):
            return self._build_incidents_export_job_view(job)

        now = _utcnow()
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        export_format = payload.get("export_format")
        normalized_format = self._normalize_export_format(str(export_format or "json"))
        current_page = self._normalize_page(int(pagination.get("page") or 1))
        current_page_size = self._normalize_page_size(int(pagination.get("page_size") or 20))

        job.status = JobStatus.PROCESSING.value
        job.started_at = now
        job.attempt_count = int(job.attempt_count or 0) + 1
        self.session.add(job)
        self.session.flush()

        try:
            exported = self.export_incidents(
                status=filters.get("status"),
                severity=filters.get("severity"),
                product_item_id=filters.get("product_item_id"),
                bom_line_item_id=filters.get("bom_line_item_id"),
                batch_code=filters.get("batch_code"),
                responsibility=filters.get("responsibility"),
                page=current_page,
                page_size=current_page_size,
                export_format=normalized_format,
            )
            content_bytes = bytes(exported.get("content") or b"")
            encoded_content = base64.b64encode(content_bytes).decode("ascii")
            updated_payload = dict(payload)
            updated_payload["export"] = {
                "sync_status": "completed",
                "updated_at": now.isoformat(),
                "updated_by_id": user_id,
            }
            updated_payload["export_result"] = {
                "filename": exported.get("filename"),
                "media_type": exported.get("media_type"),
                "size_bytes": len(content_bytes),
                "content_sha1": hashlib.sha1(content_bytes).hexdigest(),
                "content_b64": encoded_content,
            }
            updated_payload["result"] = {
                "filename": exported.get("filename"),
                "media_type": exported.get("media_type"),
                "size_bytes": len(content_bytes),
                "updated_at": now.isoformat(),
            }
            job.payload = updated_payload
            job.status = JobStatus.COMPLETED.value
            job.completed_at = now
            job.last_error = None
            self.session.add(job)
            self.session.flush()
            return self._build_incidents_export_job_view(job)
        except Exception as exc:
            updated_payload = dict(payload)
            updated_payload["export"] = {
                "sync_status": "failed",
                "error_message": str(exc),
                "updated_at": now.isoformat(),
                "updated_by_id": user_id,
            }
            job.payload = updated_payload
            job.status = JobStatus.FAILED.value
            job.completed_at = now
            job.last_error = str(exc)
            self.session.add(job)
            self.session.flush()
            raise ValueError(str(exc))

    def get_incidents_export_job(self, job_id: str) -> Dict[str, Any]:
        job = self._get_incidents_export_job(job_id)
        return self._build_incidents_export_job_view(job)

    def run_incidents_export_job(
        self,
        job_id: str,
        *,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self.execute_incidents_export_job(job_id, user_id=user_id)

    def download_incidents_export_job(self, job_id: str) -> Dict[str, Any]:
        job = self._get_incidents_export_job(job_id)
        if str(job.status or "") != JobStatus.COMPLETED.value:
            raise ValueError(f"Export job is not completed yet: {job_id}")
        payload = job.payload if isinstance(job.payload, dict) else {}
        result = payload.get("export_result")
        if not isinstance(result, dict):
            raise ValueError(f"Export result payload missing for job: {job_id}")
        encoded = result.get("content_b64")
        if not encoded:
            raise ValueError(f"Export content missing for job: {job_id}")
        try:
            decoded = base64.b64decode(str(encoded).encode("ascii"))
        except Exception as exc:
            raise ValueError(f"Export content decode failed for job: {job_id}") from exc
        return {
            "content": decoded,
            "media_type": result.get("media_type") or "application/octet-stream",
            "filename": result.get("filename") or "breakage-incidents.bin",
        }

    def cleanup_expired_incidents_export_results(
        self,
        *,
        ttl_hours: int = 24,
        limit: int = 200,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            normalized_ttl_hours = int(ttl_hours)
        except Exception as exc:
            raise ValueError("ttl_hours must be an integer between 1 and 720") from exc
        if normalized_ttl_hours < 1 or normalized_ttl_hours > 720:
            raise ValueError("ttl_hours must be between 1 and 720")
        try:
            normalized_limit = int(limit)
        except Exception as exc:
            raise ValueError("limit must be an integer between 1 and 1000") from exc
        if normalized_limit < 1 or normalized_limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        now = _utcnow()
        cutoff = now - timedelta(hours=normalized_ttl_hours)
        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type == self._incidents_export_task_type)
            .filter(ConversionJob.status == JobStatus.COMPLETED.value)
            .filter(ConversionJob.completed_at.isnot(None))
            .filter(ConversionJob.completed_at <= cutoff)
            .order_by(ConversionJob.completed_at.asc())
            .limit(normalized_limit)
            .all()
        )
        expired_jobs = 0
        skipped_jobs = 0
        touched_job_ids: List[str] = []
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            result = (
                payload.get("export_result")
                if isinstance(payload.get("export_result"), dict)
                else {}
            )
            if not result.get("content_b64"):
                skipped_jobs += 1
                continue

            updated_payload = dict(payload)
            updated_result = dict(result)
            updated_result.pop("content_b64", None)
            updated_result["content_expired_at"] = now.isoformat()
            updated_result["content_ttl_hours"] = normalized_ttl_hours
            updated_payload["export_result"] = updated_result

            export_info = (
                updated_payload.get("export")
                if isinstance(updated_payload.get("export"), dict)
                else {}
            )
            export_info = dict(export_info)
            export_info["sync_status"] = "expired"
            export_info["content_expired_at"] = now.isoformat()
            export_info["content_ttl_hours"] = normalized_ttl_hours
            export_info["updated_by_id"] = user_id
            updated_payload["export"] = export_info

            result_summary = (
                updated_payload.get("result")
                if isinstance(updated_payload.get("result"), dict)
                else {}
            )
            result_summary = dict(result_summary)
            result_summary["download_ready"] = False
            result_summary["content_expired_at"] = now.isoformat()
            updated_payload["result"] = result_summary

            job.payload = updated_payload
            self.session.add(job)
            expired_jobs += 1
            touched_job_ids.append(str(job.id))

        self.session.flush()
        return {
            "ttl_hours": normalized_ttl_hours,
            "limit": normalized_limit,
            "cutoff_at": cutoff.isoformat(),
            "scanned_jobs": len(jobs),
            "expired_jobs": expired_jobs,
            "skipped_jobs": skipped_jobs,
            "job_ids": touched_job_ids,
            "updated_at": now.isoformat(),
        }

    def _build_helpdesk_sync_summary(
        self,
        *,
        incident_ids: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type == self._helpdesk_task_type)
            .order_by(ConversionJob.created_at.desc())
            .all()
        )
        by_job_status = Counter()
        by_sync_status = Counter()
        by_provider = Counter()
        by_provider_ticket_status = Counter()
        with_external_ticket = 0
        touched_incidents: set[str] = set()
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            incident_id = str(payload.get("incident_id") or "")
            if not incident_id:
                continue
            if incident_ids is not None and incident_id not in incident_ids:
                continue
            touched_incidents.add(incident_id)
            job_status = str(job.status or "unknown").strip().lower() or "unknown"
            by_job_status[job_status] += 1
            sync_info = (
                payload.get("helpdesk_sync")
                if isinstance(payload.get("helpdesk_sync"), dict)
                else {}
            )
            integration = (
                payload.get("integration")
                if isinstance(payload.get("integration"), dict)
                else {}
            )
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            triage = payload.get("triage") if isinstance(payload.get("triage"), dict) else {}
            sync_status = (
                str(sync_info.get("sync_status") or result.get("sync_status") or "")
                .strip()
                .lower()
                or job_status
            )
            by_sync_status[sync_status] += 1
            provider = (
                str(
                    sync_info.get("provider")
                    or integration.get("provider")
                    or result.get("provider")
                    or payload.get("provider")
                    or "stub"
                )
                .strip()
                .lower()
                or "stub"
            )
            by_provider[provider] += 1
            provider_ticket_status = (
                str(
                    sync_info.get("provider_ticket_status")
                    or result.get("provider_ticket_status")
                    or payload.get("provider_ticket_status")
                    or ""
                )
                .strip()
                .lower()
            )
            if provider_ticket_status:
                by_provider_ticket_status[provider_ticket_status] += 1
            external_ticket_id = (
                sync_info.get("external_ticket_id")
                or result.get("external_ticket_id")
                or payload.get("external_ticket_id")
            )
            if external_ticket_id:
                with_external_ticket += 1

        return {
            "total_jobs": sum(by_job_status.values()),
            "incidents_with_jobs": len(touched_incidents),
            "by_job_status": dict(by_job_status),
            "by_sync_status": dict(by_sync_status),
            "by_provider": dict(by_provider),
            "by_provider_ticket_status": dict(by_provider_ticket_status),
            "providers_total": len(by_provider),
            "with_external_ticket": with_external_ticket,
            "with_provider_ticket_status": int(sum(by_provider_ticket_status.values())),
            "failed_jobs": int(by_job_status.get(JobStatus.FAILED.value, 0)),
        }

    def cockpit(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        product_item_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        responsibility: Optional[str] = None,
        trend_window_days: int = 14,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        current_page = self._normalize_page(page)
        current_page_size = self._normalize_page_size(page_size)
        incidents_all = self.list_incidents(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
        )
        total = len(incidents_all)
        total_pages = max(1, (total + current_page_size - 1) // current_page_size)
        if current_page > total_pages:
            current_page = total_pages
        offset = (current_page - 1) * current_page_size
        incidents_page = incidents_all[offset : offset + current_page_size]
        metrics = self.metrics(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            trend_window_days=trend_window_days,
            page=current_page,
            page_size=current_page_size,
        )
        latest_helpdesk_summary_by_incident = self.build_latest_helpdesk_summary_map(
            incident.id for incident in incidents_all
        )
        helpdesk_sync_summary = self._build_helpdesk_sync_summary(
            incident_ids={str(incident.id) for incident in incidents_all}
        )
        open_incidents = sum(
            1
            for incident in incidents_all
            if str(incident.status or "").strip().lower() == "open"
        )
        return {
            "total": total,
            "filters": {
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
            },
            "pagination": {
                "page": current_page,
                "page_size": current_page_size,
                "pages": total_pages,
                "total": total,
            },
            "kpis": {
                "incidents_total": total,
                "open_incidents": open_incidents,
                "closed_incidents": max(total - open_incidents, 0),
                "repeated_failure_rate": metrics.get("repeated_failure_rate") or 0.0,
                "helpdesk_failed_jobs": helpdesk_sync_summary.get("failed_jobs") or 0,
            },
            "incidents": [
                self.serialize_incident_view(
                    incident,
                    helpdesk_summary=latest_helpdesk_summary_by_incident.get(
                        str(incident.id)
                    ),
                )
                for incident in incidents_page
            ],
            "metrics": {
                "total": metrics.get("total") or 0,
                "repeated_event_count": metrics.get("repeated_event_count") or 0,
                "repeated_failure_rate": metrics.get("repeated_failure_rate") or 0.0,
                "by_status": metrics.get("by_status") or {},
                "by_severity": metrics.get("by_severity") or {},
                "by_responsibility": metrics.get("by_responsibility") or {},
                "by_product_item": metrics.get("by_product_item") or {},
                "by_batch_code": metrics.get("by_batch_code") or {},
                "by_bom_line_item": metrics.get("by_bom_line_item") or {},
                "by_bom_id": metrics.get("by_bom_id") or {},
                "by_mbom_id": metrics.get("by_mbom_id") or {},
                "by_routing_id": metrics.get("by_routing_id") or {},
                "top_product_items": metrics.get("top_product_items") or [],
                "top_batch_codes": metrics.get("top_batch_codes") or [],
                "top_bom_line_items": metrics.get("top_bom_line_items") or [],
                "top_bom_ids": metrics.get("top_bom_ids") or [],
                "top_mbom_ids": metrics.get("top_mbom_ids") or [],
                "top_routing_ids": metrics.get("top_routing_ids") or [],
                "hotspot_components": metrics.get("hotspot_components") or [],
                "trend_window_days": metrics.get("trend_window_days") or trend_window_days,
                "trend": metrics.get("trend") or [],
            },
            "helpdesk_sync_summary": helpdesk_sync_summary,
        }

    def export_cockpit(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        product_item_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        responsibility: Optional[str] = None,
        trend_window_days: int = 14,
        page: int = 1,
        page_size: int = 20,
        export_format: str = "json",
    ) -> Dict[str, Any]:
        cockpit = self.cockpit(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            trend_window_days=trend_window_days,
            page=page,
            page_size=page_size,
        )
        normalized = self._normalize_export_format(export_format)
        if normalized == "json":
            return {
                "content": json.dumps(cockpit, ensure_ascii=False, indent=2).encode("utf-8"),
                "media_type": "application/json",
                "filename": "breakage-cockpit.json",
            }

        incidents = cockpit.get("incidents") if isinstance(cockpit.get("incidents"), list) else []
        kpis = cockpit.get("kpis") if isinstance(cockpit.get("kpis"), dict) else {}
        helpdesk = (
            cockpit.get("helpdesk_sync_summary")
            if isinstance(cockpit.get("helpdesk_sync_summary"), dict)
            else {}
        )

        if normalized == "csv":
            rows = incidents or [{}]
            csv_io = io.StringIO()
            writer = csv.DictWriter(
                csv_io,
                fieldnames=[
                    "id",
                    "incident_code",
                    "description",
                    "status",
                    "severity",
                    "product_item_id",
                    "bom_id",
                    "mbom_id",
                    "routing_id",
                    "bom_line_item_id",
                    "batch_code",
                    "responsibility",
                    "created_at",
                    "updated_at",
                    "incidents_total",
                    "open_incidents",
                    "repeated_failure_rate",
                    "helpdesk_total_jobs",
                    "helpdesk_failed_jobs",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "id": row.get("id"),
                        "incident_code": row.get("incident_code"),
                        "description": row.get("description"),
                        "status": row.get("status"),
                        "severity": row.get("severity"),
                        "product_item_id": row.get("product_item_id"),
                        "bom_id": row.get("bom_id"),
                        "mbom_id": row.get("mbom_id"),
                        "routing_id": row.get("routing_id"),
                        "bom_line_item_id": row.get("bom_line_item_id"),
                        "batch_code": row.get("batch_code"),
                        "responsibility": row.get("responsibility"),
                        "created_at": row.get("created_at"),
                        "updated_at": row.get("updated_at"),
                        "incidents_total": kpis.get("incidents_total"),
                        "open_incidents": kpis.get("open_incidents"),
                        "repeated_failure_rate": kpis.get("repeated_failure_rate"),
                        "helpdesk_total_jobs": helpdesk.get("total_jobs"),
                        "helpdesk_failed_jobs": helpdesk.get("failed_jobs"),
                    }
                )
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": "breakage-cockpit.csv",
            }

        if normalized == "md":
            lines = [
                "# Breakage Cockpit",
                "",
                f"- filters: {json.dumps(cockpit.get('filters') or {}, ensure_ascii=False)}",
                f"- pagination: {json.dumps(cockpit.get('pagination') or {}, ensure_ascii=False)}",
                f"- kpis: {json.dumps(kpis, ensure_ascii=False)}",
                f"- helpdesk_sync_summary: {json.dumps(helpdesk, ensure_ascii=False)}",
                "",
                "| Code | ID | Status | Severity | Product | BOM | MBOM | Routing | BOM Line | Batch | Responsibility |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
            for row in incidents:
                lines.append(
                    f"| {row.get('incident_code') or ''} | {row.get('id') or ''} | {row.get('status') or ''} | "
                    f"{row.get('severity') or ''} | {row.get('product_item_id') or ''} | {row.get('bom_id') or ''} | "
                    f"{row.get('mbom_id') or ''} | {row.get('routing_id') or ''} | {row.get('bom_line_item_id') or ''} | "
                    f"{row.get('batch_code') or ''} | {row.get('responsibility') or ''} |"
                )
            return {
                "content": ("\n".join(lines) + "\n").encode("utf-8"),
                "media_type": "text/markdown",
                "filename": "breakage-cockpit.md",
            }

        raise ValueError("export_format must be json, csv or md")

    def update_status(
        self,
        incident_id: str,
        *,
        status: str,
        auto_loopback: bool = False,
        loopback_user_id: Optional[int] = None,
    ) -> BreakageIncident:
        incident = self.session.get(BreakageIncident, incident_id)
        if not incident:
            raise ValueError(f"Breakage incident not found: {incident_id}")
        target_status = status.strip().lower()
        incident.status = target_status
        incident.updated_at = _utcnow()
        self.session.flush()

        if not auto_loopback:
            # Default-OFF: byte-identical pre-§3.3 behavior — no
            # eligibility check, no create call, no extra query, no
            # eco_id write. Pinned by
            # test_update_status_default_off_is_byte_identical.
            return incident

        # Tier-B #3 §3.3/§3.4 shared auto-trigger. The caller has
        # already flushed the target status; the post-flush
        # eligibility gate + create + §3.C self-heal / unrecoverable
        # logic lives in ONE shared helper (Tier-B #3 §3.4 §3.E,
        # taskbook `cab1162`) — §3.4 reuses it, adding NO second
        # race handler.
        return self._auto_trigger_design_loopback(
            incident_id,
            target_status=target_status,
            loopback_user_id=loopback_user_id,
            trigger_source="update_status",  # §3.6 §3.F (no sync ctx)
        )

    def _auto_trigger_design_loopback(
        self,
        incident_id: str,
        *,
        target_status: str,
        loopback_user_id: Optional[int],
        trigger_source: str,
        sync_status: Optional[str] = None,
        provider_ticket_status: Optional[str] = None,
    ) -> BreakageIncident:
        """Shared §3.3/§3.4 auto-loopback (Tier-B #3 §3.4 §3.E,
        taskbook `cab1162`). The caller MUST have already flushed
        the ``target_status`` onto the incident; this helper then:

        - §3.A / §3.D gate #2: build the post-update descriptor and
          fire iff the new status is loopback-eligible (product
          policy A1, no delta gating). An ineligible status is a
          valid no-op — return the incident; do NOT call
          create_…eco and swallow its ValueError.
        - require ``loopback_user_id`` (the routes always supply
          ``int(user.id)``; this guards a direct-caller misuse).
        - §3.C ratified single behavior: on ``created`` return the
          incident (CAS winner — the caller's status flush is
          intact); on ``created=False`` re-read and self-heal by
          re-applying ``target_status`` (idempotent in the
          no-rollback dedupe sub-case; the real repair in the §3.2
          CAS-loser ``self.session.rollback()`` sub-case) and
          return an ordinary success — no client-visible retry;
          raise ``BreakageDesignLoopbackLinkRace`` ONLY when the
          re-read finds no incident or no winner ECO.

        Behavior-preserving extraction of §3.3's inline block;
        ``test_breakage_update_status_auto_trigger.py`` stays green
        UNCHANGED as the regression proof.
        """

        incident = self.session.get(BreakageIncident, incident_id)
        descriptor = resolve_breakage_eco_closure_descriptor(
            self._breakage_design_loopback_row(incident)
        )
        if not is_breakage_eligible_for_design_loopback(descriptor):
            return incident

        if loopback_user_id is None:
            raise ValueError(
                "loopback_user_id is required when auto_loopback=True and "
                f"the new status is loopback-eligible: {incident_id}"
            )

        # §3.C: same session/transaction — the route owns the single
        # db.commit(). §3.D: §3.2's durable CAS makes a repeat /
        # concurrent fire idempotent (created=False), not duplicate-
        # producing.
        creation = self.create_breakage_design_loopback_eco(
            incident_id,
            user_id=loopback_user_id,
            allow_duplicate=False,
            trigger_source=trigger_source,
            sync_status=sync_status,
            provider_ticket_status=provider_ticket_status,
        )
        if creation.created:
            # CAS winner — no rollback occurred; the caller's status
            # flush is intact.
            return incident

        # created=False — either §3.2's durable-dedupe early return
        # (no rollback; status flush intact) OR the CAS-loser path,
        # which did a full self.session.rollback() that ALSO unwound
        # the caller's status flush (shared transaction). §3.C
        # ratified behavior: re-read and self-heal by re-applying
        # the target status (idempotent in the no-rollback sub-case;
        # the real repair in the CAS-loser sub-case). Raise the
        # dedicated unrecoverable-state exception ONLY when the
        # re-read finds no incident or no winner ECO.
        incident = self.session.get(BreakageIncident, incident_id)
        if incident is None or creation.eco is None:
            raise BreakageDesignLoopbackLinkRace(
                "breakage design loopback link race — cannot determine the "
                f"linked ECO for incident {incident_id}"
            )
        incident.status = target_status
        incident.updated_at = _utcnow()
        self.session.flush()
        return incident

    def _breakage_design_loopback_row(
        self,
        incident: BreakageIncident,
    ) -> BreakageIncidentRow:
        return BreakageIncidentRow(
            description=incident.description,
            status=incident.status,
            severity=incident.severity,
            incident_code=incident.incident_code,
            product_item_id=incident.product_item_id,
            bom_id=incident.bom_id,
            version_id=incident.version_id,
        )

    def resolve_breakage_design_loopback_descriptor(
        self,
        incident_id: str,
    ) -> BreakageEcoClosureDescriptor:
        incident = self.session.get(BreakageIncident, incident_id)
        if not incident:
            raise ValueError(f"Breakage incident not found: {incident_id}")
        return resolve_breakage_eco_closure_descriptor(
            self._breakage_design_loopback_row(incident)
        )

    def prepare_breakage_design_loopback_intake(
        self,
        incident_id: str,
    ) -> BreakageDesignLoopbackPreparation:
        descriptor = self.resolve_breakage_design_loopback_descriptor(incident_id)
        eligible = is_breakage_eligible_for_design_loopback(descriptor)
        if not eligible:
            return BreakageDesignLoopbackPreparation(
                incident_id=incident_id,
                descriptor=descriptor,
                eligible=False,
                ineligible_reason=(
                    f"breakage status {descriptor.status!r} is not eligible "
                    "for design loopback"
                ),
            )

        intake = map_breakage_to_change_request_intake(descriptor)
        eco_draft_inputs = map_change_request_to_eco_draft_inputs(intake)
        return BreakageDesignLoopbackPreparation(
            incident_id=incident_id,
            descriptor=descriptor,
            eligible=True,
            intake=intake,
            eco_draft_inputs=eco_draft_inputs,
        )

    def _find_breakage_design_loopback_eco_by_reference(
        self,
        reference: str,
        *,
        incident_id: Optional[str] = None,
    ) -> Optional[ECO]:
        # Primary: durable eco_id-column lookup if we know the
        # incident. `eco_id` is a bare String soft-link (no FK —
        # see taskbook §3.2 `3e5104f` §4.1); session.get returns
        # None for a since-deleted ECO, so a dangling id degrades
        # gracefully to "no link".
        if incident_id:
            incident = self.session.get(BreakageIncident, incident_id)
            if incident is not None and incident.eco_id:
                return self.session.get(ECO, incident.eco_id)

        # Fallback: substring scan for pre-migration data (incidents
        # whose `eco_id` is NULL but whose ECO was created before
        # the migration). NOT race-safe — only the CAS link path in
        # create_breakage_design_loopback_eco is.
        normalized = str(reference or "").strip()
        if not normalized:
            return None

        marker = f"reference={normalized}"
        candidates = (
            self.session.query(ECO)
            .filter(ECO.description.contains(marker))
            .order_by(ECO.created_at.asc(), ECO.id.asc())
            .all()
        )
        for eco in candidates:
            description = str(getattr(eco, "description", None) or "")
            if "breakage-eco-closeout" in description and marker in description:
                return eco
        return None

    def _enqueue_breakage_design_loopback_event(
        self,
        *,
        incident_id: str,
        eco_id: str,
        created: bool,
        trigger_source: str,
        incident_status: str,
        sync_status: Optional[str] = None,
        provider_ticket_status: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> None:
        """Tier-B #3 §3.6 (taskbook ``61ce226``). No-op unless the
        S2 settings flag is enabled (default OFF →
        byte-identical). Mirrors ``ECOService._enqueue_eco_created``:
        one transactional ``enqueue_event`` — published only after
        the route's single commit, dropped by ANY rollback (so the
        §3.2 CAS-loser de-dups automatically and is never an
        enqueue site; see §3.B/§3.C).
        """

        if not get_settings().BREAKAGE_DESIGN_LOOPBACK_EVENTS_ENABLED:
            return
        enqueue_event(
            self.session,
            BreakageDesignLoopbackEcoEvent(
                incident_id=incident_id,
                eco_id=eco_id,
                created=created,
                trigger_source=trigger_source,
                incident_status=incident_status,
                sync_status=sync_status,
                provider_ticket_status=provider_ticket_status,
                actor_id=actor_id,
            ),
        )

    def create_breakage_design_loopback_eco(
        self,
        incident_id: str,
        *,
        user_id: int,
        allow_duplicate: bool = False,
        trigger_source: str = "route",
        sync_status: Optional[str] = None,
        provider_ticket_status: Optional[str] = None,
    ) -> BreakageDesignLoopbackEcoCreation:
        """Explicitly create an ECO for an eligible breakage design loopback.

        The caller owns the transaction boundary. Permission/event
        behavior is delegated to ECOService.create_eco. Durable
        idempotency (Tier-B #3 §3.2, taskbook `3e5104f`): the link
        is wired by a compare-and-swap UPDATE
        (``WHERE id=:i AND eco_id IS NULL``). The CAS — NOT the
        UNIQUE index — is the concurrency sync point: the row write
        lock serializes concurrent callers so exactly one gets
        rowcount==1; the rowcount==0 loser rolls back its own
        ECOService.create_eco INSERT (undone because create_eco has
        no internal commit) and returns the winner with
        created=False. `allow_duplicate=True` is an explicit
        detached duplicate — it skips the CAS entirely and leaves
        `eco_id` untouched.
        """

        preparation = self.prepare_breakage_design_loopback_intake(incident_id)
        if (
            not preparation.eligible
            or preparation.intake is None
            or preparation.eco_draft_inputs is None
        ):
            raise ValueError(
                preparation.ineligible_reason
                or "breakage is not eligible for design loopback"
            )

        reference = derive_breakage_change_reference(preparation.descriptor)
        if not allow_duplicate:
            existing = self._find_breakage_design_loopback_eco_by_reference(
                reference, incident_id=incident_id
            )
            if existing is not None:
                # §3.6 §3.B: durable-dedupe reuse — a legitimate
                # "reuse" signal (no create_eco, no CAS, no
                # rollback). incident_status = the post-flush
                # eligible status from preparation.descriptor.
                self._enqueue_breakage_design_loopback_event(
                    incident_id=incident_id,
                    eco_id=existing.id,
                    created=False,
                    trigger_source=trigger_source,
                    incident_status=preparation.descriptor.status,
                    sync_status=sync_status,
                    provider_ticket_status=provider_ticket_status,
                    actor_id=user_id,
                )
                return BreakageDesignLoopbackEcoCreation(
                    incident_id=incident_id,
                    preparation=preparation,
                    reference=reference,
                    eco=existing,
                    created=False,
                )

        kwargs = preparation.eco_draft_inputs.as_kwargs()
        kwargs["user_id"] = user_id
        eco = ECOService(self.session).create_eco(**kwargs)

        if allow_duplicate:
            # Explicit detached duplicate — do NOT attempt the CAS
            # link. `eco_id` stays whatever it was (NULL or the
            # canonical first ECO). Author-ratified semantics.
            return BreakageDesignLoopbackEcoCreation(
                incident_id=incident_id,
                preparation=preparation,
                reference=reference,
                eco=eco,
                created=True,
            )

        # Compare-and-swap link. The single atomic conditional
        # UPDATE is the concurrency sync point — the row write lock
        # blocks a concurrent caller's UPDATE behind this one until
        # this transaction's caller commits, after which the loser
        # re-evaluates the predicate and matches zero rows.
        #
        # Predicate is `eco_id IS NULL OR eco_id points at a
        # missing ECO`. The taskbook §4.4 illustrative snippet
        # showed only `eco_id IS NULL`, but §4.1/§5-test-6 ratified
        # the behavioral contract that a *dangling* link (the ECO
        # was hard-deleted; no FK so no SET NULL cascade) must
        # degrade so a fresh create proceeds. A bare `IS NULL`
        # predicate would permanently wedge such an incident (its
        # eco_id is non-NULL but useless). The `NOT IN (SELECT id
        # FROM meta_ecos)` arm reconciles the snippet with the
        # ratified behavioral contract while staying a single
        # atomic UPDATE (row write lock still serializes the
        # genuine race — a committed valid winner.eco_id IS in
        # meta_ecos, so the loser's predicate is false → rowcount
        # 0). The just-flushed loser ECO is in meta_ecos too, so a
        # genuine winner is never mistaken for dangling.
        result = self.session.execute(
            sa_update(BreakageIncident)
            .where(
                BreakageIncident.id == incident_id,
                sa_or(
                    BreakageIncident.eco_id.is_(None),
                    BreakageIncident.eco_id.not_in(sa_select(ECO.id)),
                ),
            )
            .values(eco_id=eco.id)
        )
        if result.rowcount == 1:
            self.session.flush()
            # §3.6 §3.B: CAS winner — enqueue AFTER the post-CAS
            # flush, before returning, mirroring where
            # ECOService.create_eco enqueues EcoCreatedEvent. On
            # the §3.2 CAS-loser this point is never reached
            # (rowcount==0 → rollback path), so the event de-dups
            # to exactly the winner. incident_status = the
            # post-flush eligible status from preparation.descriptor.
            self._enqueue_breakage_design_loopback_event(
                incident_id=incident_id,
                eco_id=eco.id,
                created=True,
                trigger_source=trigger_source,
                incident_status=preparation.descriptor.status,
                sync_status=sync_status,
                provider_ticket_status=provider_ticket_status,
                actor_id=user_id,
            )
            return BreakageDesignLoopbackEcoCreation(
                incident_id=incident_id,
                preparation=preparation,
                reference=reference,
                eco=eco,
                created=True,
            )

        # rowcount == 0 → another transaction already linked an ECO
        # to this incident. We lost the race. Roll back our own
        # create_eco (undone because ECOService.create_eco has no
        # internal commit), re-read the winner's link, return it.
        self.session.rollback()
        incident = self.session.get(BreakageIncident, incident_id)
        winner = (
            self.session.get(ECO, incident.eco_id)
            if incident is not None and incident.eco_id
            else None
        )
        return BreakageDesignLoopbackEcoCreation(
            incident_id=incident_id,
            preparation=preparation,
            reference=reference,
            eco=winner,
            created=False,
        )

    def metrics(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        product_item_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        responsibility: Optional[str] = None,
        trend_window_days: int = 14,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        window_days = self._normalize_trend_window_days(trend_window_days)
        current_page = self._normalize_page(page)
        current_page_size = self._normalize_page_size(page_size)
        incidents_all = (
            self.session.query(BreakageIncident)
            .order_by(BreakageIncident.created_at.desc())
            .all()
        )
        incidents = self._apply_incident_filters(
            incidents_all,
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
        )
        by_signature = defaultdict(int)
        for incident in incidents:
            signature = (
                str(incident.product_item_id or ""),
                (incident.description or "").strip().lower(),
            )
            by_signature[signature] += 1

        repeated_events = sum(count for count in by_signature.values() if count > 1)
        total = len(incidents)
        repeated_rate = (repeated_events / total) if total else 0.0

        hotspot_counter = Counter(
            str(incident.bom_line_item_id)
            for incident in incidents
            if incident.bom_line_item_id
        )
        hotspot_components = [
            {"bom_line_item_id": item_id, "count": count}
            for item_id, count in hotspot_counter.most_common(10)
        ]

        by_status = Counter(str(incident.status or "unknown") for incident in incidents)
        by_severity = Counter(
            str(incident.severity or "unknown") for incident in incidents
        )
        by_responsibility = Counter(
            str(incident.responsibility or "unknown") for incident in incidents
        )
        by_product_item = Counter(
            str(incident.product_item_id)
            for incident in incidents
            if incident.product_item_id
        )
        by_batch_code = Counter(
            str(incident.batch_code)
            for incident in incidents
            if incident.batch_code
        )
        by_bom_line_item = Counter(
            str(incident.bom_line_item_id)
            for incident in incidents
            if incident.bom_line_item_id
        )
        by_bom_id = Counter(
            str(self._incident_bom_id(incident))
            for incident in incidents
            if self._incident_bom_id(incident)
        )
        by_mbom_id = Counter(
            str(self._incident_mbom_id(incident))
            for incident in incidents
            if self._incident_mbom_id(incident)
        )
        by_routing_id = Counter(
            str(self._incident_routing_id(incident))
            for incident in incidents
            if self._incident_routing_id(incident)
        )
        top_product_items = [
            {"product_item_id": item_id, "count": count}
            for item_id, count in by_product_item.most_common(10)
        ]
        top_batch_codes = [
            {"batch_code": batch_code, "count": count}
            for batch_code, count in by_batch_code.most_common(10)
        ]
        top_bom_line_items = [
            {"bom_line_item_id": item_id, "count": count}
            for item_id, count in by_bom_line_item.most_common(10)
        ]
        top_bom_ids = [
            {"bom_id": bom_id, "count": count}
            for bom_id, count in by_bom_id.most_common(10)
        ]
        top_mbom_ids = [
            {"mbom_id": mbom_id, "count": count}
            for mbom_id, count in by_mbom_id.most_common(10)
        ]
        top_routing_ids = [
            {"routing_id": routing_id, "count": count}
            for routing_id, count in by_routing_id.most_common(10)
        ]

        now = _utcnow()
        start_day = (now - timedelta(days=window_days - 1)).date()
        day_keys = [
            (start_day + timedelta(days=offset)).isoformat()
            for offset in range(window_days)
        ]
        trend_counter = {day: 0 for day in day_keys}
        for incident in incidents:
            created_at = incident.created_at or incident.updated_at
            if not created_at:
                continue
            day = created_at.date().isoformat()
            if day in trend_counter:
                trend_counter[day] += 1
        trend = [{"date": day, "count": trend_counter[day]} for day in day_keys]

        total = len(incidents)
        total_pages = max(1, (total + current_page_size - 1) // current_page_size)
        if current_page > total_pages:
            current_page = total_pages
        offset = (current_page - 1) * current_page_size
        page_rows = incidents[offset : offset + current_page_size]

        incidents_page = [self._serialize_incident(incident) for incident in page_rows]

        return {
            "total": total,
            "repeated_failure_rate": repeated_rate,
            "repeated_event_count": repeated_events,
            "by_status": dict(by_status),
            "by_severity": dict(by_severity),
            "by_responsibility": dict(by_responsibility),
            "by_product_item": dict(by_product_item),
            "by_batch_code": dict(by_batch_code),
            "by_bom_line_item": dict(by_bom_line_item),
            "by_bom_id": dict(by_bom_id),
            "by_mbom_id": dict(by_mbom_id),
            "by_routing_id": dict(by_routing_id),
            "top_product_items": top_product_items,
            "top_batch_codes": top_batch_codes,
            "top_bom_line_items": top_bom_line_items,
            "top_bom_ids": top_bom_ids,
            "top_mbom_ids": top_mbom_ids,
            "top_routing_ids": top_routing_ids,
            "hotspot_components": hotspot_components,
            "trend_window_days": window_days,
            "trend": trend,
            "filters": {
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
            },
            "pagination": {
                "page": current_page,
                "page_size": current_page_size,
                "pages": total_pages,
                "total": total,
            },
            "incidents": incidents_page,
        }

    def metrics_groups(
        self,
        *,
        group_by: str = "responsibility",
        status: Optional[str] = None,
        severity: Optional[str] = None,
        product_item_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        responsibility: Optional[str] = None,
        trend_window_days: int = 14,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_group_by = self._normalize_group_by(group_by)
        window_days = self._normalize_trend_window_days(trend_window_days)
        current_page = self._normalize_page(page)
        current_page_size = self._normalize_page_size(page_size)
        incidents_all = (
            self.session.query(BreakageIncident)
            .order_by(BreakageIncident.created_at.desc())
            .all()
        )
        incidents = self._apply_incident_filters(
            incidents_all,
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
        )

        attr_name = self._group_by_fields[normalized_group_by]
        counter = Counter()
        for incident in incidents:
            raw = getattr(incident, attr_name, None)
            value = str(raw or "").strip()
            if not value:
                continue
            counter[value] += 1

        groups_all = [
            {
                "group_by": normalized_group_by,
                "group_value": group_value,
                "count": count,
            }
            for group_value, count in counter.most_common()
        ]
        total_groups = len(groups_all)
        total_pages = max(1, (total_groups + current_page_size - 1) // current_page_size)
        if current_page > total_pages:
            current_page = total_pages
        offset = (current_page - 1) * current_page_size
        groups_page = groups_all[offset : offset + current_page_size]

        return {
            "group_by": normalized_group_by,
            "total_groups": total_groups,
            "groups": groups_page,
            "trend_window_days": window_days,
            "filters": {
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
            },
            "pagination": {
                "page": current_page,
                "page_size": current_page_size,
                "pages": total_pages,
                "total": total_groups,
            },
        }

    def _metrics_export_rows(self, metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        filters = metrics.get("filters") if isinstance(metrics.get("filters"), dict) else {}
        trend = metrics.get("trend") if isinstance(metrics.get("trend"), list) else []
        rows: List[Dict[str, Any]] = []
        for point in trend:
            if not isinstance(point, dict):
                continue
            rows.append(
                {
                    "date": point.get("date"),
                    "count": point.get("count"),
                    "total": metrics.get("total"),
                    "repeated_event_count": metrics.get("repeated_event_count"),
                    "repeated_failure_rate": metrics.get("repeated_failure_rate"),
                    "trend_window_days": metrics.get("trend_window_days"),
                    "status_filter": filters.get("status"),
                    "severity_filter": filters.get("severity"),
                    "product_item_id_filter": filters.get("product_item_id"),
                    "bom_line_item_id_filter": filters.get("bom_line_item_id"),
                    "batch_code_filter": filters.get("batch_code"),
                    "responsibility_filter": filters.get("responsibility"),
                }
            )
        if rows:
            return rows
        return [
            {
                "date": None,
                "count": 0,
                "total": metrics.get("total"),
                "repeated_event_count": metrics.get("repeated_event_count"),
                "repeated_failure_rate": metrics.get("repeated_failure_rate"),
                "trend_window_days": metrics.get("trend_window_days"),
                "status_filter": filters.get("status"),
                "severity_filter": filters.get("severity"),
                "product_item_id_filter": filters.get("product_item_id"),
                "bom_line_item_id_filter": filters.get("bom_line_item_id"),
                "batch_code_filter": filters.get("batch_code"),
                "responsibility_filter": filters.get("responsibility"),
            }
        ]

    def _metrics_groups_export_rows(
        self, metrics_groups: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        filters = (
            metrics_groups.get("filters")
            if isinstance(metrics_groups.get("filters"), dict)
            else {}
        )
        groups = (
            metrics_groups.get("groups")
            if isinstance(metrics_groups.get("groups"), list)
            else []
        )
        rows: List[Dict[str, Any]] = []
        for row in groups:
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    "group_by": metrics_groups.get("group_by"),
                    "group_value": row.get("group_value"),
                    "count": row.get("count"),
                    "total_groups": metrics_groups.get("total_groups"),
                    "trend_window_days": metrics_groups.get("trend_window_days"),
                    "status_filter": filters.get("status"),
                    "severity_filter": filters.get("severity"),
                    "product_item_id_filter": filters.get("product_item_id"),
                    "bom_line_item_id_filter": filters.get("bom_line_item_id"),
                    "batch_code_filter": filters.get("batch_code"),
                    "responsibility_filter": filters.get("responsibility"),
                }
            )
        if rows:
            return rows
        return [
            {
                "group_by": metrics_groups.get("group_by"),
                "group_value": None,
                "count": 0,
                "total_groups": metrics_groups.get("total_groups"),
                "trend_window_days": metrics_groups.get("trend_window_days"),
                "status_filter": filters.get("status"),
                "severity_filter": filters.get("severity"),
                "product_item_id_filter": filters.get("product_item_id"),
                "bom_line_item_id_filter": filters.get("bom_line_item_id"),
                "batch_code_filter": filters.get("batch_code"),
                "responsibility_filter": filters.get("responsibility"),
            }
        ]

    def export_metrics(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        product_item_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        responsibility: Optional[str] = None,
        trend_window_days: int = 14,
        page: int = 1,
        page_size: int = 20,
        export_format: str = "json",
        report_lang: Optional[str] = None,
        report_type: Optional[str] = None,
        locale_profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        metrics = self.metrics(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            trend_window_days=trend_window_days,
            page=page,
            page_size=page_size,
        )
        locale_context = _resolve_report_locale_context(
            self.session,
            locale_profile_id=locale_profile_id,
            report_lang=report_lang,
            report_type=report_type or "breakage_metrics",
        )
        normalized = str(export_format or "json").strip().lower()
        if normalized == "json":
            payload = dict(metrics)
            if locale_context:
                payload["locale"] = locale_context
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            return {
                "content": content,
                "media_type": "application/json",
                "filename": "breakage-metrics.json",
            }

        rows = self._metrics_export_rows(metrics)
        if normalized == "csv":
            csv_io = io.StringIO()
            writer = csv.DictWriter(
                csv_io,
                fieldnames=[
                    "date",
                    "count",
                    "total",
                    "repeated_event_count",
                    "repeated_failure_rate",
                    "trend_window_days",
                    "status_filter",
                    "severity_filter",
                    "product_item_id_filter",
                    "bom_line_item_id_filter",
                    "batch_code_filter",
                    "responsibility_filter",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": "breakage-metrics.csv",
            }

        if normalized == "md":
            by_status = (
                metrics.get("by_status")
                if isinstance(metrics.get("by_status"), dict)
                else {}
            )
            by_severity = (
                metrics.get("by_severity")
                if isinstance(metrics.get("by_severity"), dict)
                else {}
            )
            by_responsibility = (
                metrics.get("by_responsibility")
                if isinstance(metrics.get("by_responsibility"), dict)
                else {}
            )
            by_product_item = (
                metrics.get("by_product_item")
                if isinstance(metrics.get("by_product_item"), dict)
                else {}
            )
            by_batch_code = (
                metrics.get("by_batch_code")
                if isinstance(metrics.get("by_batch_code"), dict)
                else {}
            )
            by_bom_line_item = (
                metrics.get("by_bom_line_item")
                if isinstance(metrics.get("by_bom_line_item"), dict)
                else {}
            )
            by_bom_id = (
                metrics.get("by_bom_id")
                if isinstance(metrics.get("by_bom_id"), dict)
                else {}
            )
            by_mbom_id = (
                metrics.get("by_mbom_id")
                if isinstance(metrics.get("by_mbom_id"), dict)
                else {}
            )
            by_routing_id = (
                metrics.get("by_routing_id")
                if isinstance(metrics.get("by_routing_id"), dict)
                else {}
            )
            top_product_items = (
                metrics.get("top_product_items")
                if isinstance(metrics.get("top_product_items"), list)
                else []
            )
            top_batch_codes = (
                metrics.get("top_batch_codes")
                if isinstance(metrics.get("top_batch_codes"), list)
                else []
            )
            top_bom_line_items = (
                metrics.get("top_bom_line_items")
                if isinstance(metrics.get("top_bom_line_items"), list)
                else []
            )
            top_bom_ids = (
                metrics.get("top_bom_ids")
                if isinstance(metrics.get("top_bom_ids"), list)
                else []
            )
            top_mbom_ids = (
                metrics.get("top_mbom_ids")
                if isinstance(metrics.get("top_mbom_ids"), list)
                else []
            )
            top_routing_ids = (
                metrics.get("top_routing_ids")
                if isinstance(metrics.get("top_routing_ids"), list)
                else []
            )
            hotspots = (
                metrics.get("hotspot_components")
                if isinstance(metrics.get("hotspot_components"), list)
                else []
            )
            lines = [
                "# Breakage Metrics",
                "",
                f"- total: {metrics.get('total') or 0}",
                (
                    f"- repeated_event_count: "
                    f"{metrics.get('repeated_event_count') or 0}"
                ),
                (
                    f"- repeated_failure_rate: "
                    f"{metrics.get('repeated_failure_rate') or 0}"
                ),
                f"- trend_window_days: {metrics.get('trend_window_days') or ''}",
                "",
                f"- by_status: {json.dumps(by_status, ensure_ascii=False)}",
                f"- by_severity: {json.dumps(by_severity, ensure_ascii=False)}",
                (
                    f"- by_responsibility: "
                    f"{json.dumps(by_responsibility, ensure_ascii=False)}"
                ),
                f"- by_product_item: {json.dumps(by_product_item, ensure_ascii=False)}",
                f"- by_batch_code: {json.dumps(by_batch_code, ensure_ascii=False)}",
                (
                    f"- by_bom_line_item: "
                    f"{json.dumps(by_bom_line_item, ensure_ascii=False)}"
                ),
                f"- by_bom_id: {json.dumps(by_bom_id, ensure_ascii=False)}",
                f"- by_mbom_id: {json.dumps(by_mbom_id, ensure_ascii=False)}",
                f"- by_routing_id: {json.dumps(by_routing_id, ensure_ascii=False)}",
                (
                    f"- top_product_items: "
                    f"{json.dumps(top_product_items, ensure_ascii=False)}"
                ),
                (
                    f"- top_batch_codes: "
                    f"{json.dumps(top_batch_codes, ensure_ascii=False)}"
                ),
                (
                    f"- top_bom_line_items: "
                    f"{json.dumps(top_bom_line_items, ensure_ascii=False)}"
                ),
                f"- top_bom_ids: {json.dumps(top_bom_ids, ensure_ascii=False)}",
                f"- top_mbom_ids: {json.dumps(top_mbom_ids, ensure_ascii=False)}",
                (
                    f"- top_routing_ids: "
                    f"{json.dumps(top_routing_ids, ensure_ascii=False)}"
                ),
                (
                    f"- hotspot_components: "
                    f"{json.dumps(hotspots, ensure_ascii=False)}"
                ),
                "",
            ]
            if locale_context:
                lines.extend(
                    [
                        "## Locale",
                        "",
                        f"- lang: {locale_context.get('lang') or ''}",
                        f"- profile_id: {locale_context.get('id') or ''}",
                        f"- report_type: {locale_context.get('report_type') or locale_context.get('requested_report_type') or ''}",
                        f"- timezone: {locale_context.get('timezone') or ''}",
                        "",
                    ]
                )
            lines.extend(
                [
                    "| Date | Count |",
                    "| --- | --- |",
                ]
            )
            for row in rows:
                lines.append(f"| {row['date'] or ''} | {row['count'] or 0} |")
            return {
                "content": ("\n".join(lines) + "\n").encode("utf-8"),
                "media_type": "text/markdown",
                "filename": "breakage-metrics.md",
            }

        raise ValueError("export_format must be json, csv or md")

    def export_metrics_groups(
        self,
        *,
        group_by: str = "responsibility",
        status: Optional[str] = None,
        severity: Optional[str] = None,
        product_item_id: Optional[str] = None,
        bom_line_item_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        responsibility: Optional[str] = None,
        trend_window_days: int = 14,
        page: int = 1,
        page_size: int = 20,
        export_format: str = "json",
        report_lang: Optional[str] = None,
        report_type: Optional[str] = None,
        locale_profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        metrics_groups = self.metrics_groups(
            group_by=group_by,
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            trend_window_days=trend_window_days,
            page=page,
            page_size=page_size,
        )
        locale_context = _resolve_report_locale_context(
            self.session,
            locale_profile_id=locale_profile_id,
            report_lang=report_lang,
            report_type=report_type or "breakage_metrics_groups",
        )
        normalized = str(export_format or "json").strip().lower()
        if normalized == "json":
            payload = dict(metrics_groups)
            if locale_context:
                payload["locale"] = locale_context
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            return {
                "content": content,
                "media_type": "application/json",
                "filename": "breakage-metrics-groups.json",
            }

        rows = self._metrics_groups_export_rows(metrics_groups)
        if normalized == "csv":
            csv_io = io.StringIO()
            writer = csv.DictWriter(
                csv_io,
                fieldnames=[
                    "group_by",
                    "group_value",
                    "count",
                    "total_groups",
                    "trend_window_days",
                    "status_filter",
                    "severity_filter",
                    "product_item_id_filter",
                    "bom_line_item_id_filter",
                    "batch_code_filter",
                    "responsibility_filter",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": "breakage-metrics-groups.csv",
            }

        if normalized == "md":
            lines = [
                "# Breakage Metrics Groups",
                "",
                f"- group_by: {metrics_groups.get('group_by') or ''}",
                f"- total_groups: {metrics_groups.get('total_groups') or 0}",
                (
                    f"- trend_window_days: "
                    f"{metrics_groups.get('trend_window_days') or ''}"
                ),
                f"- filters: {json.dumps(metrics_groups.get('filters') or {}, ensure_ascii=False)}",
                "",
            ]
            if locale_context:
                lines.extend(
                    [
                        "## Locale",
                        "",
                        f"- lang: {locale_context.get('lang') or ''}",
                        f"- profile_id: {locale_context.get('id') or ''}",
                        f"- report_type: {locale_context.get('report_type') or locale_context.get('requested_report_type') or ''}",
                        f"- timezone: {locale_context.get('timezone') or ''}",
                        "",
                    ]
                )
            lines.extend(
                [
                    "| Group By | Group Value | Count |",
                    "| --- | --- | --- |",
                ]
            )
            for row in rows:
                lines.append(
                    f"| {row['group_by'] or ''} | {row['group_value'] or ''} | {row['count'] or 0} |"
                )
            return {
                "content": ("\n".join(lines) + "\n").encode("utf-8"),
                "media_type": "text/markdown",
                "filename": "breakage-metrics-groups.md",
            }

        raise ValueError("export_format must be json, csv or md")

    def _simulate_helpdesk_provider_dispatch(
        self,
        *,
        provider: str,
        incident: BreakageIncident,
        attempt_count: int,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metadata = metadata_json if isinstance(metadata_json, dict) else {}
        forced_error = str(metadata.get("force_error_code") or "").strip()
        forced_message = str(metadata.get("force_error_message") or "").strip()
        if forced_error:
            raise RuntimeError(forced_message or forced_error)
        normalized_provider = str(provider or "stub").strip().lower() or "stub"
        normalized_attempt = max(int(attempt_count or 1), 1)
        ticket_base = str(incident.id or "").replace("-", "").upper()[:8]
        if normalized_provider == "stub":
            return {"external_ticket_id": f"HD-{ticket_base}-{normalized_attempt}"}
        if normalized_provider == "jira":
            return {"external_ticket_id": f"JIRA-{ticket_base}-{normalized_attempt}"}
        if normalized_provider == "zendesk":
            return {"external_ticket_id": f"ZD-{ticket_base}-{normalized_attempt}"}
        raise ValueError(f"unsupported helpdesk provider: {normalized_provider}")

    def _normalize_helpdesk_provider(self, provider: str) -> str:
        normalized = str(provider or "stub").strip().lower() or "stub"
        if normalized not in {"stub", "jira", "zendesk"}:
            raise ValueError(f"unsupported helpdesk provider: {normalized}")
        return normalized

    def _normalize_helpdesk_integration(
        self,
        *,
        provider: str,
        integration_json: Optional[Dict[str, Any]],
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        raw = integration_json if isinstance(integration_json, dict) else {}
        base_url = str(raw.get("base_url") or "").strip().rstrip("/")
        mode = str(raw.get("mode") or "").strip().lower()
        if not mode:
            mode = "http" if base_url else "stub"
        if mode not in {"stub", "http"}:
            raise ValueError("integration.mode must be one of: stub, http")

        timeout_s_raw = raw.get("timeout_s", 8.0)
        try:
            timeout_s = float(timeout_s_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("integration.timeout_s must be between 1 and 60") from exc
        if timeout_s < 1 or timeout_s > 60:
            raise ValueError("integration.timeout_s must be between 1 and 60")

        verify_tls = bool(raw.get("verify_tls", True))
        auth_type = str(raw.get("auth_type") or "").strip().lower()
        token = str(raw.get("token") or "").strip() or None
        username = str(raw.get("username") or "").strip() or None
        api_key = str(raw.get("api_key") or "").strip() or None
        if not auth_type:
            if token:
                auth_type = "bearer"
            elif username and api_key:
                auth_type = "basic"
            else:
                auth_type = "none"
        if auth_type not in {"none", "bearer", "basic"}:
            raise ValueError("integration.auth_type must be one of: none, bearer, basic")
        if auth_type == "bearer" and not token:
            raise ValueError("integration.token is required when auth_type=bearer")
        if auth_type == "basic" and (not username or not api_key):
            raise ValueError("integration.username/api_key are required when auth_type=basic")

        extra_headers_raw = raw.get("extra_headers")
        extra_headers: Dict[str, str] = {}
        if extra_headers_raw is not None:
            if not isinstance(extra_headers_raw, dict):
                raise ValueError("integration.extra_headers must be an object")
            for key, value in extra_headers_raw.items():
                header_key = str(key or "").strip()
                if not header_key:
                    continue
                extra_headers[header_key] = str(value or "").strip()

        jira_project_key = str(raw.get("jira_project_key") or "").strip() or None
        jira_issue_type = str(raw.get("jira_issue_type") or "").strip() or None
        zendesk_requester_email = (
            str(raw.get("zendesk_requester_email") or "").strip() or None
        )
        zendesk_priority = str(raw.get("zendesk_priority") or "").strip().lower() or None

        normalized_provider = self._normalize_helpdesk_provider(provider)
        if mode == "http":
            if normalized_provider == "stub":
                raise ValueError("stub provider does not support integration.mode=http")
            if not base_url:
                raise ValueError("integration.base_url is required when mode=http")

        return {
            "mode": mode,
            "base_url": base_url or None,
            "timeout_s": timeout_s,
            "verify_tls": verify_tls,
            "auth_type": auth_type,
            "token": token,
            "username": username,
            "api_key": api_key,
            "extra_headers": extra_headers,
            "jira_project_key": jira_project_key,
            "jira_issue_type": jira_issue_type,
            "zendesk_requester_email": zendesk_requester_email,
            "zendesk_priority": zendesk_priority,
            "provider": normalized_provider,
            "idempotency_key": idempotency_key,
        }

    def _build_helpdesk_http_headers(
        self,
        *,
        integration: Dict[str, Any],
        idempotency_key: Optional[str],
    ) -> Dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        extra_headers = (
            integration.get("extra_headers")
            if isinstance(integration.get("extra_headers"), dict)
            else {}
        )
        for key, value in extra_headers.items():
            header_key = str(key or "").strip()
            if not header_key:
                continue
            headers[header_key] = str(value or "").strip()
        auth_type = str(integration.get("auth_type") or "none").strip().lower()
        if auth_type == "bearer":
            token = str(integration.get("token") or "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        if idempotency_key:
            headers["X-Idempotency-Key"] = str(idempotency_key)
        return headers

    def _dispatch_helpdesk_provider_http(
        self,
        *,
        provider: str,
        incident: BreakageIncident,
        attempt_count: int,
        metadata_json: Optional[Dict[str, Any]] = None,
        integration_json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        metadata = metadata_json if isinstance(metadata_json, dict) else {}
        integration = (
            integration_json if isinstance(integration_json, dict) else {}
        )
        base_url = str(integration.get("base_url") or "").strip().rstrip("/")
        timeout_s = float(integration.get("timeout_s") or 8.0)
        verify_tls = bool(integration.get("verify_tls", True))
        normalized_provider = self._normalize_helpdesk_provider(provider)
        if not base_url:
            raise ValueError("integration.base_url is required when mode=http")

        headers = self._build_helpdesk_http_headers(
            integration=integration,
            idempotency_key=idempotency_key,
        )
        auth = None
        if str(integration.get("auth_type") or "").strip().lower() == "basic":
            auth = (
                str(integration.get("username") or "").strip(),
                str(integration.get("api_key") or ""),
            )

        incident_desc = str(incident.description or "").strip()
        summary = str(metadata.get("summary") or f"[Breakage] {incident_desc[:120]}").strip()
        detail = str(
            metadata.get("description")
            or (
                f"breakage_incident_id={incident.id}\n"
                f"severity={incident.severity}\n"
                f"status={incident.status}\n"
                f"product_item_id={incident.product_item_id}\n"
                f"bom_line_item_id={incident.bom_line_item_id}\n"
                f"batch_code={incident.batch_code}\n"
                f"description={incident_desc}"
            )
        )

        if normalized_provider == "jira":
            project_key = str(
                metadata.get("jira_project_key")
                or integration.get("jira_project_key")
                or "OPS"
            ).strip()
            issue_type = str(
                metadata.get("jira_issue_type")
                or integration.get("jira_issue_type")
                or "Task"
            ).strip()
            request_url = f"{base_url}/rest/api/2/issue"
            request_body = {
                "fields": {
                    "project": {"key": project_key},
                    "summary": summary,
                    "description": detail,
                    "issuetype": {"name": issue_type},
                }
            }
        elif normalized_provider == "zendesk":
            requester_email = str(
                metadata.get("zendesk_requester_email")
                or integration.get("zendesk_requester_email")
                or "noreply@example.com"
            ).strip()
            priority = str(
                metadata.get("zendesk_priority")
                or integration.get("zendesk_priority")
                or "normal"
            ).strip().lower()
            request_url = f"{base_url}/api/v2/tickets.json"
            request_body = {
                "ticket": {
                    "subject": summary,
                    "comment": {"body": detail},
                    "priority": priority,
                    "requester": {"email": requester_email},
                }
            }
        else:
            raise ValueError(f"unsupported helpdesk provider: {normalized_provider}")

        try:
            with httpx.Client(timeout=timeout_s, verify=verify_tls) as client:
                response = client.post(
                    request_url,
                    headers=headers,
                    json=request_body,
                    auth=auth,
                )
        except httpx.TimeoutException as exc:
            raise RuntimeError("provider timeout") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"provider transport error: {exc}") from exc

        response_text = str(getattr(response, "text", "") or "").strip()
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code >= 400:
            preview = response_text[:200]
            raise RuntimeError(f"http_{status_code}: {preview}")

        body: Dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                body = parsed
        except Exception:
            body = {}

        external_ticket_id = None
        if normalized_provider == "jira":
            external_ticket_id = (
                str(body.get("key") or "").strip()
                or str(body.get("id") or "").strip()
                or None
            )
        elif normalized_provider == "zendesk":
            ticket = body.get("ticket") if isinstance(body.get("ticket"), dict) else {}
            external_ticket_id = (
                str(ticket.get("id") or "").strip()
                or str(body.get("id") or "").strip()
                or None
            )
        if not external_ticket_id:
            ticket_base = str(incident.id or "").replace("-", "").upper()[:8]
            if normalized_provider == "jira":
                external_ticket_id = f"JIRA-{ticket_base}-{max(int(attempt_count or 1), 1)}"
            else:
                external_ticket_id = f"ZD-{ticket_base}-{max(int(attempt_count or 1), 1)}"

        return {
            "external_ticket_id": external_ticket_id,
            "dispatch_mode": "http",
            "http_status": status_code,
            "request_url": request_url,
        }

    def _dispatch_helpdesk_provider(
        self,
        *,
        provider: str,
        incident: BreakageIncident,
        attempt_count: int,
        metadata_json: Optional[Dict[str, Any]] = None,
        integration_json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_provider = self._normalize_helpdesk_provider(provider)
        integration = (
            integration_json if isinstance(integration_json, dict) else {}
        )
        mode = str(integration.get("mode") or "stub").strip().lower() or "stub"
        if mode == "http" and normalized_provider in {"jira", "zendesk"}:
            return self._dispatch_helpdesk_provider_http(
                provider=normalized_provider,
                incident=incident,
                attempt_count=attempt_count,
                metadata_json=metadata_json,
                integration_json=integration,
                idempotency_key=idempotency_key,
            )
        return self._simulate_helpdesk_provider_dispatch(
            provider=normalized_provider,
            incident=incident,
            attempt_count=attempt_count,
            metadata_json=metadata_json,
        )

    def _map_helpdesk_provider_error(
        self,
        exc: Exception,
    ) -> Dict[str, Any]:
        text = str(exc or "").strip()
        lowered = text.lower()
        if isinstance(exc, httpx.TimeoutException):
            return {
                "error_code": "provider_timeout",
                "error_message": text or "provider timeout",
            }
        if isinstance(exc, httpx.HTTPError):
            return {
                "error_code": "provider_transport_error",
                "error_message": text or "provider transport error",
            }
        if "http_429" in lowered:
            return {
                "error_code": "provider_rate_limited",
                "error_message": text or "provider rate limited",
            }
        if "http_401" in lowered or "http_403" in lowered:
            return {
                "error_code": "provider_auth_error",
                "error_message": text or "provider auth error",
            }
        if "http_400" in lowered or "http_404" in lowered or "http_422" in lowered:
            return {
                "error_code": "provider_invalid_request",
                "error_message": text or "provider invalid request",
            }
        if "http_5" in lowered:
            return {
                "error_code": "provider_http_server_error",
                "error_message": text or "provider server error",
            }
        if "timeout" in lowered or "timed out" in lowered:
            return {
                "error_code": "provider_timeout",
                "error_message": text or "provider timeout",
            }
        if "rate" in lowered or "429" in lowered:
            return {
                "error_code": "provider_rate_limited",
                "error_message": text or "provider rate limited",
            }
        if "unauthorized" in lowered or "forbidden" in lowered:
            return {
                "error_code": "provider_auth_error",
                "error_message": text or "provider auth error",
            }
        if "unsupported helpdesk provider" in lowered:
            return {
                "error_code": "provider_unsupported",
                "error_message": text or "provider unsupported",
            }
        if isinstance(exc, ValueError):
            return {
                "error_code": "provider_invalid_request",
                "error_message": text or "provider invalid request",
            }
        return {
            "error_code": "provider_sync_failed",
            "error_message": text or "provider sync failed",
        }

    def run_helpdesk_sync_job(
        self,
        job_id: str,
        *,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        job = self.session.get(ConversionJob, job_id)
        if not job or str(job.task_type or "") != self._helpdesk_task_type:
            raise ValueError(f"Helpdesk sync job not found: {job_id}")
        payload = job.payload if isinstance(job.payload, dict) else {}
        incident_id = str(payload.get("incident_id") or "")
        if not incident_id:
            raise ValueError(f"Helpdesk sync job missing incident_id: {job_id}")
        incident = self.session.get(BreakageIncident, incident_id)
        if not incident:
            raise ValueError(f"Breakage incident not found: {incident_id}")

        sync_info = (
            payload.get("helpdesk_sync")
            if isinstance(payload.get("helpdesk_sync"), dict)
            else {}
        )
        integration = (
            payload.get("integration")
            if isinstance(payload.get("integration"), dict)
            else {}
        )
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        idempotency_key = (
            str(
                sync_info.get("idempotency_key")
                or integration.get("idempotency_key")
                or payload.get("idempotency_key")
                or ""
            ).strip()
            or None
        )
        provider = (
            str(
                sync_info.get("provider")
                or integration.get("provider")
                or payload.get("provider")
                or "stub"
            )
            .strip()
            .lower()
            or "stub"
        )
        simulate_status = str(metadata.get("simulate_status") or "").strip().lower()
        if simulate_status == "failed":
            error_code = str(metadata.get("error_code") or "provider_simulated_failed")
            error_message = str(
                metadata.get("error_message") or f"{provider} simulated failed"
            )
            return self.execute_helpdesk_sync(
                incident_id,
                simulate_status="failed",
                job_id=job_id,
                error_code=error_code,
                error_message=error_message,
                metadata_json=metadata,
                user_id=user_id,
            )

        try:
            normalized_integration = self._normalize_helpdesk_integration(
                provider=provider,
                integration_json=integration,
                idempotency_key=idempotency_key,
            )
            dispatch_result = self._dispatch_helpdesk_provider(
                provider=self._normalize_helpdesk_provider(provider),
                incident=incident,
                attempt_count=int(job.attempt_count or 0) + 1,
                metadata_json=metadata,
                integration_json=normalized_integration,
                idempotency_key=idempotency_key,
            )
            external_ticket_id = (
                str(metadata.get("external_ticket_id") or "").strip()
                or str(dispatch_result.get("external_ticket_id") or "").strip()
                or None
            )
            dispatch_meta = dict(metadata)
            dispatch_meta["provider_dispatch"] = {
                "mode": str(dispatch_result.get("dispatch_mode") or normalized_integration.get("mode") or "stub"),
                "http_status": dispatch_result.get("http_status"),
                "request_url": dispatch_result.get("request_url"),
            }
            return self.execute_helpdesk_sync(
                incident_id,
                simulate_status="completed",
                job_id=job_id,
                external_ticket_id=external_ticket_id,
                metadata_json=dispatch_meta,
                user_id=user_id,
            )
        except Exception as exc:
            mapped = self._map_helpdesk_provider_error(exc)
            return self.execute_helpdesk_sync(
                incident_id,
                simulate_status="failed",
                job_id=job_id,
                error_code=str(mapped.get("error_code") or "provider_sync_failed"),
                error_message=str(mapped.get("error_message") or str(exc)),
                metadata_json={
                    **metadata,
                    "provider_dispatch": {
                        "mode": str(integration.get("mode") or "stub"),
                        "http_status": None,
                        "request_url": None,
                    },
                },
                user_id=user_id,
            )

    def enqueue_helpdesk_stub_sync(
        self,
        incident_id: str,
        *,
        user_id: Optional[int] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
        provider: str = "stub",
        idempotency_key: Optional[str] = None,
        retry_max_attempts: Optional[int] = None,
        integration_json: Optional[Dict[str, Any]] = None,
    ) -> ConversionJob:
        incident = self.session.get(BreakageIncident, incident_id)
        if not incident:
            raise ValueError(f"Breakage incident not found: {incident_id}")
        normalized_provider = self._normalize_helpdesk_provider(provider)
        metadata = metadata_json if isinstance(metadata_json, dict) else {}
        resolved_idempotency_key = (
            str(idempotency_key or "").strip()
            or str(metadata.get("idempotency_key") or "").strip()
            or None
        )
        configured_retry_max_attempts = retry_max_attempts
        if configured_retry_max_attempts is None and "retry_max_attempts" in metadata:
            configured_retry_max_attempts = metadata.get("retry_max_attempts")
        if configured_retry_max_attempts is None:
            max_attempts = max(1, int(get_settings().JOB_MAX_ATTEMPTS_DEFAULT or 1))
        else:
            try:
                max_attempts = int(configured_retry_max_attempts)
            except Exception as exc:
                raise ValueError("retry_max_attempts must be between 1 and 10") from exc
            if max_attempts < 1 or max_attempts > 10:
                raise ValueError("retry_max_attempts must be between 1 and 10")
        normalized_integration = self._normalize_helpdesk_integration(
            provider=normalized_provider,
            integration_json=integration_json,
            idempotency_key=resolved_idempotency_key,
        )
        payload = {
            "incident_id": incident.id,
            "description": incident.description,
            "severity": incident.severity,
            "status": incident.status,
            "product_item_id": incident.product_item_id,
            "bom_line_item_id": incident.bom_line_item_id,
            "batch_code": incident.batch_code,
            "customer_name": incident.customer_name,
            "metadata": metadata,
            "mode": (
                "helpdesk_http"
                if str(normalized_integration.get("mode") or "stub") == "http"
                else "helpdesk_stub"
            ),
            "integration": {
                **normalized_integration,
            },
            "helpdesk_sync": {
                "sync_status": "queued",
                "provider": normalized_provider,
                "idempotency_key": resolved_idempotency_key,
                "updated_at": _utcnow().isoformat(),
                "updated_by_id": user_id,
            },
        }
        if resolved_idempotency_key:
            dedupe_key = (
                f"breakage-helpdesk:{incident.id}:"
                f"idemp:{resolved_idempotency_key[:40]}"
            )
        else:
            dedupe_key = (
                f"breakage-helpdesk:{incident.id}:"
                f"{incident.updated_at.isoformat() if incident.updated_at else ''}"
            )
        return self._job_service.create_job(
            task_type=self._helpdesk_task_type,
            payload=payload,
            user_id=user_id,
            max_attempts=max_attempts,
            dedupe=True,
            dedupe_key=dedupe_key,
        )

    def _list_helpdesk_sync_jobs_for_incident(self, incident_id: str) -> List[ConversionJob]:
        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type == self._helpdesk_task_type)
            .order_by(ConversionJob.created_at.desc())
            .all()
        )
        matched: List[ConversionJob] = []
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            if str(payload.get("incident_id") or "") == str(incident_id):
                matched.append(job)
        return matched

    def _resolve_helpdesk_sync_job(
        self,
        incident_id: str,
        *,
        job_id: Optional[str] = None,
    ) -> ConversionJob:
        target_job: Optional[ConversionJob] = None
        if job_id:
            target_job = self.session.get(ConversionJob, job_id)
            if (
                not target_job
                or str(target_job.task_type or "") != self._helpdesk_task_type
            ):
                raise ValueError(f"Helpdesk sync job not found: {job_id}")
            payload = target_job.payload if isinstance(target_job.payload, dict) else {}
            if str(payload.get("incident_id") or "") != str(incident_id):
                raise ValueError(f"Helpdesk sync job does not belong to incident: {job_id}")
            return target_job

        jobs = self._list_helpdesk_sync_jobs_for_incident(incident_id)
        if not jobs:
            raise ValueError(f"Helpdesk sync job not found for incident: {incident_id}")
        return jobs[0]

    def _classify_helpdesk_failure(
        self,
        *,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> str:
        text = f"{error_code or ''} {error_message or ''}".strip().lower()
        if not text:
            return "unknown"
        transient_tokens = {
            "timeout",
            "tempor",
            "rate_limit",
            "429",
            "network",
            "unavailable",
            "503",
            "busy",
            "retry",
            "provider_timeout",
            "provider_rate_limited",
            "provider_transport_error",
            "provider_http_server_error",
        }
        permanent_tokens = {
            "invalid",
            "validation",
            "bad_request",
            "400",
            "forbidden",
            "403",
            "not_found",
            "404",
            "unauthorized",
            "401",
            "conflict",
            "409",
            "schema",
            "provider_auth_error",
            "provider_unsupported",
            "provider_invalid_request",
        }
        if any(token in text for token in transient_tokens):
            return "transient"
        if any(token in text for token in permanent_tokens):
            return "permanent"
        return "unknown"

    def _normalize_helpdesk_provider_ticket_status(self, status: str) -> str:
        normalized = str(status or "").strip().lower()
        if not normalized:
            raise ValueError("provider_ticket_status must not be empty")
        return self._HELPDESK_PROVIDER_STATUS_ALIASES.get(normalized, normalized)

    def _map_helpdesk_provider_ticket_to_incident_status(self, status: str) -> str:
        return self._HELPDESK_PROVIDER_TO_INCIDENT_STATUS.get(status, "open")

    def _map_helpdesk_provider_ticket_to_sync_status(
        self,
        status: str,
        *,
        fallback: Optional[str] = None,
    ) -> str:
        mapped = self._HELPDESK_PROVIDER_TO_SYNC_STATUS.get(status)
        if mapped:
            return mapped
        fallback_normalized = str(fallback or "").strip().lower()
        return fallback_normalized or "queued"

    def _build_helpdesk_sync_job_view(self, job: ConversionJob) -> Dict[str, Any]:
        payload = job.payload if isinstance(job.payload, dict) else {}
        sync_info = (
            payload.get("helpdesk_sync")
            if isinstance(payload.get("helpdesk_sync"), dict)
            else {}
        )
        integration = (
            payload.get("integration")
            if isinstance(payload.get("integration"), dict)
            else {}
        )
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        external_ticket_id = (
            sync_info.get("external_ticket_id")
            or result.get("external_ticket_id")
            or payload.get("external_ticket_id")
        )
        provider = (
            str(
                sync_info.get("provider")
                or integration.get("provider")
                or payload.get("provider")
                or "stub"
            )
            .strip()
            .lower()
            or "stub"
        )
        idempotency_key = (
            sync_info.get("idempotency_key")
            or integration.get("idempotency_key")
            or payload.get("idempotency_key")
        )
        integration_mode = str(integration.get("mode") or "stub").strip().lower() or "stub"
        integration_base_url = integration.get("base_url")
        failure_category = (
            sync_info.get("failure_category")
            or result.get("failure_category")
            or payload.get("failure_category")
        )
        error_code = sync_info.get("error_code") or result.get("error_code")
        error_message = (
            sync_info.get("error_message")
            or result.get("error_message")
            or payload.get("error_message")
            or job.last_error
        )
        provider_ticket_status = (
            sync_info.get("provider_ticket_status")
            or result.get("provider_ticket_status")
            or payload.get("provider_ticket_status")
        )
        provider_ticket_updated_at = (
            sync_info.get("provider_ticket_updated_at")
            or result.get("provider_ticket_updated_at")
            or payload.get("provider_ticket_updated_at")
        )
        provider_assignee = (
            sync_info.get("provider_assignee")
            or result.get("provider_assignee")
            or payload.get("provider_assignee")
        )
        provider_payload = (
            sync_info.get("provider_payload")
            or result.get("provider_payload")
            or payload.get("provider_payload")
        )
        provider_last_event_id = (
            sync_info.get("provider_last_event_id")
            or result.get("provider_last_event_id")
            or payload.get("provider_last_event_id")
        )
        provider_event_ids_raw = (
            sync_info.get("provider_event_ids")
            or result.get("provider_event_ids")
            or payload.get("provider_event_ids")
        )
        provider_event_ids_count = 0
        if isinstance(provider_event_ids_raw, list):
            provider_event_ids_count = len(
                [value for value in provider_event_ids_raw if str(value or "").strip()]
            )
        sync_status = (
            str(sync_info.get("sync_status") or result.get("sync_status") or "")
            .strip()
            .lower()
            or str(job.status or "").strip().lower()
        )
        attempt_count = int(job.attempt_count or 0)
        max_attempts = int(job.max_attempts or 0)
        return {
            "id": job.id,
            "task_type": job.task_type,
            "status": job.status,
            "sync_status": sync_status,
            "provider": provider,
            "integration_mode": integration_mode,
            "integration_base_url": integration_base_url,
            "idempotency_key": idempotency_key,
            "failure_category": failure_category,
            "error_code": error_code,
            "error_message": error_message,
            "provider_ticket_status": provider_ticket_status,
            "provider_ticket_updated_at": provider_ticket_updated_at,
            "provider_assignee": provider_assignee,
            "provider_payload": provider_payload,
            "provider_last_event_id": provider_last_event_id,
            "provider_event_ids_count": provider_event_ids_count,
            "external_ticket_id": external_ticket_id,
            "last_error": job.last_error,
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "retry_budget": {
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
                "remaining_attempts": max(max_attempts - attempt_count, 0),
            },
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    def get_helpdesk_sync_status(self, incident_id: str) -> Dict[str, Any]:
        incident = self.session.get(BreakageIncident, incident_id)
        if not incident:
            raise ValueError(f"Breakage incident not found: {incident_id}")
        jobs = self._list_helpdesk_sync_jobs_for_incident(incident_id)
        job_views = [self._build_helpdesk_sync_job_view(job) for job in jobs]
        latest = job_views[0] if job_views else None
        return {
            "incident_id": incident_id,
            "incident_status": str(incident.status or "").strip().lower() or "open",
            "incident_responsibility": incident.responsibility,
            "sync_status": latest.get("sync_status") if latest else "not_started",
            "external_ticket_id": latest.get("external_ticket_id") if latest else None,
            "last_job": latest,
            "jobs": job_views,
        }

    def execute_helpdesk_sync(
        self,
        incident_id: str,
        *,
        simulate_status: str = "completed",
        job_id: Optional[str] = None,
        external_ticket_id: Optional[str] = None,
        error_message: Optional[str] = None,
        error_code: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_status = str(simulate_status or "").strip().lower()
        if normalized_status not in {"completed", "failed"}:
            raise ValueError("simulate_status must be one of: completed, failed")
        incident = self.session.get(BreakageIncident, incident_id)
        if not incident:
            raise ValueError(f"Breakage incident not found: {incident_id}")
        target_job = self._resolve_helpdesk_sync_job(incident_id, job_id=job_id)
        payload = target_job.payload if isinstance(target_job.payload, dict) else {}
        sync_info = (
            payload.get("helpdesk_sync")
            if isinstance(payload.get("helpdesk_sync"), dict)
            else {}
        )
        integration = (
            payload.get("integration")
            if isinstance(payload.get("integration"), dict)
            else {}
        )
        if (
            str(target_job.status or "") == JobStatus.COMPLETED.value
            and normalized_status == "completed"
        ):
            return self.get_helpdesk_sync_status(incident_id)

        now = _utcnow()
        provider = (
            str(
                sync_info.get("provider")
                or payload.get("provider")
                or integration.get("provider")
                or "stub"
            )
            .strip()
            .lower()
            or "stub"
        )
        idempotency_key = (
            sync_info.get("idempotency_key")
            or payload.get("idempotency_key")
            or integration.get("idempotency_key")
        )
        target_job.status = JobStatus.PROCESSING.value
        target_job.started_at = now
        target_job.attempt_count = int(target_job.attempt_count or 0) + 1

        generated_ticket_id = external_ticket_id
        if normalized_status == "completed" and not generated_ticket_id:
            generated_ticket_id = (
                sync_info.get("external_ticket_id")
                or payload.get("external_ticket_id")
                or f"HD-{incident.id[:8]}-{target_job.attempt_count}"
            )
        failure_category = None
        if normalized_status == "failed":
            failure_category = self._classify_helpdesk_failure(
                error_code=error_code,
                error_message=error_message,
            )

        updated_payload = dict(payload)
        integration_payload = (
            dict(integration) if isinstance(integration, dict) else {}
        )
        integration_payload["provider"] = provider
        integration_payload["idempotency_key"] = idempotency_key
        updated_payload["integration"] = integration_payload
        updated_payload["helpdesk_sync"] = {
            "sync_status": normalized_status,
            "provider": provider,
            "idempotency_key": idempotency_key,
            "external_ticket_id": generated_ticket_id,
            "failure_category": failure_category,
            "error_code": error_code,
            "error_message": error_message,
            "updated_at": now.isoformat(),
            "updated_by_id": user_id,
            "metadata": metadata_json or {},
        }
        if generated_ticket_id:
            updated_payload["external_ticket_id"] = generated_ticket_id
        result = updated_payload.get("result")
        if not isinstance(result, dict):
            result = {}
        result.update(
            {
                "sync_status": normalized_status,
                "provider": provider,
                "idempotency_key": idempotency_key,
                "external_ticket_id": generated_ticket_id,
                "failure_category": failure_category,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": now.isoformat(),
            }
        )
        updated_payload["result"] = result

        target_job.payload = updated_payload
        target_job.completed_at = now
        if normalized_status == "completed":
            target_job.status = JobStatus.COMPLETED.value
            target_job.last_error = None
        else:
            target_job.status = JobStatus.FAILED.value
            target_job.last_error = str(error_message or error_code or "helpdesk_sync_failed")
        self.session.add(target_job)
        self.session.flush()
        return self.get_helpdesk_sync_status(incident_id)

    def record_helpdesk_sync_result(
        self,
        incident_id: str,
        *,
        sync_status: str,
        job_id: Optional[str] = None,
        external_ticket_id: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
        error_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_sync_status = str(sync_status or "").strip().lower()
        if normalized_sync_status not in {"completed", "failed"}:
            raise ValueError("sync_status must be one of: completed, failed")

        incident = self.session.get(BreakageIncident, incident_id)
        if not incident:
            raise ValueError(f"Breakage incident not found: {incident_id}")

        target_job = self._resolve_helpdesk_sync_job(incident_id, job_id=job_id)
        now = _utcnow()
        payload = target_job.payload if isinstance(target_job.payload, dict) else {}
        sync_info = (
            payload.get("helpdesk_sync")
            if isinstance(payload.get("helpdesk_sync"), dict)
            else {}
        )
        integration = (
            payload.get("integration")
            if isinstance(payload.get("integration"), dict)
            else {}
        )
        provider = (
            str(
                sync_info.get("provider")
                or integration.get("provider")
                or payload.get("provider")
                or "stub"
            )
            .strip()
            .lower()
            or "stub"
        )
        idempotency_key = (
            sync_info.get("idempotency_key")
            or integration.get("idempotency_key")
            or payload.get("idempotency_key")
        )
        failure_category = None
        if normalized_sync_status == "failed":
            failure_category = self._classify_helpdesk_failure(
                error_code=error_code,
                error_message=error_message,
            )
        updated_payload = dict(payload)
        integration_payload = (
            dict(integration) if isinstance(integration, dict) else {}
        )
        integration_payload["provider"] = provider
        integration_payload["idempotency_key"] = idempotency_key
        updated_payload["integration"] = integration_payload
        updated_payload["helpdesk_sync"] = {
            "sync_status": normalized_sync_status,
            "provider": provider,
            "idempotency_key": idempotency_key,
            "external_ticket_id": external_ticket_id,
            "failure_category": failure_category,
            "error_code": error_code,
            "error_message": error_message,
            "updated_at": now.isoformat(),
            "updated_by_id": user_id,
            "metadata": metadata_json or {},
        }
        if external_ticket_id:
            updated_payload["external_ticket_id"] = external_ticket_id
        result = updated_payload.get("result")
        if not isinstance(result, dict):
            result = {}
        result.update(
            {
                "sync_status": normalized_sync_status,
                "provider": provider,
                "idempotency_key": idempotency_key,
                "external_ticket_id": external_ticket_id,
                "failure_category": failure_category,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": now.isoformat(),
            }
        )
        updated_payload["result"] = result

        target_job.payload = updated_payload
        target_job.completed_at = now
        if normalized_sync_status == "completed":
            target_job.status = JobStatus.COMPLETED.value
            target_job.last_error = None
        else:
            target_job.status = JobStatus.FAILED.value
            target_job.last_error = str(error_message or "helpdesk_sync_failed")
        self.session.add(target_job)
        self.session.flush()
        return self.get_helpdesk_sync_status(incident_id)

    def apply_helpdesk_ticket_update(
        self,
        incident_id: str,
        *,
        provider_ticket_status: str,
        job_id: Optional[str] = None,
        external_ticket_id: Optional[str] = None,
        provider: Optional[str] = None,
        provider_updated_at: Optional[datetime] = None,
        provider_assignee: Optional[str] = None,
        provider_payload: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
        incident_status: Optional[str] = None,
        incident_responsibility: Optional[str] = None,
        user_id: Optional[int] = None,
        auto_loopback: bool = False,
        loopback_user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        incident = self.session.get(BreakageIncident, incident_id)
        if not incident:
            raise ValueError(f"Breakage incident not found: {incident_id}")
        target_job = self._resolve_helpdesk_sync_job(incident_id, job_id=job_id)

        normalized_provider_status = self._normalize_helpdesk_provider_ticket_status(
            provider_ticket_status
        )
        normalized_incident_status = (
            str(incident_status or "").strip().lower()
            or self._map_helpdesk_provider_ticket_to_incident_status(normalized_provider_status)
        )
        now = _utcnow()
        provider_updated = provider_updated_at or now
        if provider_updated.tzinfo is not None:
            provider_updated = provider_updated.astimezone(timezone.utc).replace(
                tzinfo=None
            )

        payload = target_job.payload if isinstance(target_job.payload, dict) else {}
        sync_info = (
            payload.get("helpdesk_sync")
            if isinstance(payload.get("helpdesk_sync"), dict)
            else {}
        )
        result_payload = (
            payload.get("result") if isinstance(payload.get("result"), dict) else {}
        )
        integration = (
            payload.get("integration")
            if isinstance(payload.get("integration"), dict)
            else {}
        )
        idempotency_key = (
            sync_info.get("idempotency_key")
            or integration.get("idempotency_key")
            or payload.get("idempotency_key")
        )
        normalized_provider = self._normalize_helpdesk_provider(
            str(
                provider
                or sync_info.get("provider")
                or integration.get("provider")
                or payload.get("provider")
                or "stub"
            )
            .strip()
            .lower()
            or "stub"
        )
        normalized_event_id = str(event_id or "").strip() or None
        existing_last_event_id = (
            str(
                sync_info.get("provider_last_event_id")
                or result_payload.get("provider_last_event_id")
                or payload.get("provider_last_event_id")
                or ""
            ).strip()
            or None
        )
        existing_event_ids_raw = (
            sync_info.get("provider_event_ids")
            or result_payload.get("provider_event_ids")
            or payload.get("provider_event_ids")
        )
        existing_event_ids: List[str] = []
        seen_event_ids: set[str] = set()
        if isinstance(existing_event_ids_raw, list):
            for value in existing_event_ids_raw:
                normalized = str(value or "").strip()
                if not normalized or normalized in seen_event_ids:
                    continue
                seen_event_ids.add(normalized)
                existing_event_ids.append(normalized)
        if normalized_event_id and normalized_event_id in seen_event_ids:
            replay = self.get_helpdesk_sync_status(incident_id)
            replay["event_id"] = normalized_event_id
            replay["idempotent_replay"] = True
            return replay
        if normalized_event_id:
            existing_event_ids.append(normalized_event_id)
        if len(existing_event_ids) > 50:
            existing_event_ids = existing_event_ids[-50:]
        provider_last_event_id = normalized_event_id or existing_last_event_id

        resolved_external_ticket_id = (
            str(external_ticket_id or "").strip()
            or str(sync_info.get("external_ticket_id") or "").strip()
            or str(payload.get("external_ticket_id") or "").strip()
            or None
        )
        derived_sync_status = self._map_helpdesk_provider_ticket_to_sync_status(
            normalized_provider_status,
            fallback=sync_info.get("sync_status"),
        )

        # Tier-B #3 §3.4 helpdesk-sync auto-trigger (taskbook
        # `cab1162`). The "heavy" helpdesk mutations
        # (responsibility + the job payload/status envelope) are
        # factored into one closure so the default-OFF single-flush
        # path and the §3.C-β path share one implementation.
        def _apply_helpdesk_mutations(target_incident, job):
            if incident_responsibility is not None:
                target_incident.responsibility = (
                    str(incident_responsibility or "").strip() or None
                )
            elif provider_assignee:
                target_incident.responsibility = str(provider_assignee).strip()

            updated_payload = dict(payload)
            integration_payload = (
                dict(integration) if isinstance(integration, dict) else {}
            )
            integration_payload["provider"] = normalized_provider
            integration_payload["idempotency_key"] = idempotency_key
            updated_payload["integration"] = integration_payload
            updated_payload["helpdesk_sync"] = {
                "sync_status": derived_sync_status,
                "provider": normalized_provider,
                "idempotency_key": idempotency_key,
                "external_ticket_id": resolved_external_ticket_id,
                "provider_ticket_status": normalized_provider_status,
                "provider_ticket_updated_at": provider_updated.isoformat(),
                "provider_assignee": provider_assignee,
                "provider_payload": provider_payload or {},
                "provider_last_event_id": provider_last_event_id,
                "provider_event_ids": existing_event_ids,
                "updated_at": now.isoformat(),
                "updated_by_id": user_id,
            }
            if resolved_external_ticket_id:
                updated_payload["external_ticket_id"] = resolved_external_ticket_id

            result = updated_payload.get("result")
            if not isinstance(result, dict):
                result = {}
            result.update(
                {
                    "sync_status": derived_sync_status,
                    "provider": normalized_provider,
                    "idempotency_key": idempotency_key,
                    "external_ticket_id": resolved_external_ticket_id,
                    "provider_ticket_status": normalized_provider_status,
                    "provider_ticket_updated_at": provider_updated.isoformat(),
                    "provider_assignee": provider_assignee,
                    "provider_last_event_id": provider_last_event_id,
                    "provider_event_ids": existing_event_ids,
                    "updated_at": now.isoformat(),
                }
            )
            updated_payload["result"] = result
            updated_payload["provider_last_event_id"] = provider_last_event_id
            updated_payload["provider_event_ids"] = existing_event_ids

            job.payload = updated_payload
            if derived_sync_status == "completed":
                job.status = JobStatus.COMPLETED.value
                job.completed_at = now
                job.last_error = None
            elif derived_sync_status == "failed":
                job.status = JobStatus.FAILED.value
                job.completed_at = now
                job.last_error = (
                    f"provider_ticket_status={normalized_provider_status}"
                )
            else:
                if str(job.status or "") == JobStatus.PENDING.value:
                    job.status = JobStatus.PROCESSING.value
                if job.started_at is None:
                    job.started_at = now
                job.completed_at = None
            self.session.add(job)
            self.session.add(target_incident)
            self.session.flush()

        if not auto_loopback:
            # Default-OFF: byte-identical pre-§3.4 single-flush
            # flow on the originally-resolved job — no
            # eligibility/sync gate, no create_…eco. (updated_at is
            # set before responsibility instead of after — a
            # behaviorally-inert intra-transaction reorder for the
            # shared closure; final committed state is identical.)
            incident.status = normalized_incident_status
            incident.updated_at = now
            _apply_helpdesk_mutations(incident, target_job)
        else:
            # §3.C-β (RATIFIED): flush ONLY status + updated_at, run
            # the shared §3.3 helper (double-gated below), THEN
            # apply the heavy helpdesk mutations on the helper's
            # returned incident + a PK-refetched job. A §3.2
            # CAS-loser self.session.rollback() can therefore only
            # ever unwind the status-only flush (the helper
            # self-heals it); the responsibility / job-payload /
            # event-id-accumulator mutations land AFTER the trigger
            # converges and are never inside a rolled-back window.
            resolved_job_id = target_job.id
            incident.status = normalized_incident_status
            incident.updated_at = now
            self.session.add(incident)
            self.session.flush()
            # §3.D double gate: gate #1 here (sync outcome must be
            # "completed" — this closes the canceled→closed/failed
            # vector and the incident_status-override-with-failed-
            # sync vector); gate #2 (status eligibility) + the
            # loopback_user_id guard + the §3.C self-heal /
            # unrecoverable behavior are all inside the shared
            # helper (no second race handler — §3.4 §3.E).
            if derived_sync_status == "completed":
                incident = self._auto_trigger_design_loopback(
                    incident_id,
                    target_status=normalized_incident_status,
                    loopback_user_id=loopback_user_id,
                    # §3.6 §3.F: thread the helpdesk sync context
                    # the emit point (inside create_…eco) cannot see.
                    trigger_source="helpdesk_sync",
                    sync_status=derived_sync_status,
                    provider_ticket_status=normalized_provider_status,
                )
            # PK re-fetch: a §3.2 CAS-loser rollback expires the
            # job; a PK get is deterministic and invariant under
            # that ECO-only rollback. updated_at is left as set in
            # the status-only flush (the helper re-applies it on
            # the CAS-loser self-heal); responsibility does NOT
            # re-bump it, matching pre-§3.4's single bump.
            job_after = self.session.get(ConversionJob, resolved_job_id)
            _apply_helpdesk_mutations(incident, job_after)

        response = self.get_helpdesk_sync_status(incident_id)
        if normalized_event_id:
            response["event_id"] = normalized_event_id
            response["idempotent_replay"] = False
        return response


_WORKORDER_VERSION_UNSET = object()


class WorkorderDocumentPackService:
    _VERSION_LOCK_SOURCES = frozenset({"manual", "eco_apply", "backfill"})

    # Re-exported so callers in the same module can opt out of clearing the
    # lock columns without passing magic strings around.
    VERSION_UNSET = _WORKORDER_VERSION_UNSET

    def __init__(self, session: Session):
        self.session = session

    def _load_owned_version(
        self,
        *,
        document_item_id: str,
        document_version_id: str,
    ) -> ItemVersion:
        version = self.session.get(ItemVersion, document_version_id)
        if version is None:
            raise ValueError(
                f"document_version_id {document_version_id!r} not found"
            )
        if str(version.item_id) != str(document_item_id):
            raise ValueError(
                "document_version_id does not belong to document_item_id"
            )
        return version

    def _resolve_lock_source(self, value: Optional[str]) -> str:
        normalized = (value or "manual").strip().lower()
        if normalized not in self._VERSION_LOCK_SOURCES:
            raise ValueError(
                f"version_lock_source must be one of {sorted(self._VERSION_LOCK_SOURCES)}"
            )
        return normalized

    def serialize_link(self, link: WorkorderDocumentLink) -> Dict[str, Any]:
        version_id = getattr(link, "document_version_id", None)
        version_locked_at = getattr(link, "version_locked_at", None)
        version_lock_source = getattr(link, "version_lock_source", None)
        version_is_current: Optional[bool] = None
        version_is_released: Optional[bool] = None
        version_label: Optional[str] = None
        version_belongs_to_item: Optional[bool] = None
        if version_id:
            version = self.session.get(ItemVersion, version_id)
            if version is not None:
                version_is_current = bool(version.is_current)
                version_is_released = bool(version.is_released)
                version_label = getattr(version, "version_label", None)
                version_belongs_to_item = (
                    str(version.item_id) == str(link.document_item_id)
                )
            else:
                version_belongs_to_item = False
        return {
            "id": link.id,
            "routing_id": link.routing_id,
            "operation_id": link.operation_id,
            "document_item_id": link.document_item_id,
            "inherit_to_children": bool(link.inherit_to_children),
            "visible_in_production": bool(link.visible_in_production),
            "document_scope": "routing" if link.operation_id is None else "operation",
            "created_at": link.created_at.isoformat() if link.created_at else None,
            "document_version_id": version_id,
            "version_locked": bool(version_id),
            "version_lock_source": version_lock_source,
            "version_locked_at": (
                version_locked_at.isoformat() if version_locked_at else None
            ),
            "version_label": version_label,
            "version_is_current": version_is_current,
            "version_is_released": version_is_released,
            "version_belongs_to_item": version_belongs_to_item,
        }

    def upsert_link(
        self,
        *,
        routing_id: str,
        document_item_id: str,
        operation_id: Optional[str] = None,
        inherit_to_children: bool = True,
        visible_in_production: bool = True,
        document_version_id: Any = _WORKORDER_VERSION_UNSET,
        version_lock_source: str = "manual",
    ) -> WorkorderDocumentLink:
        """Upsert a workorder document link.

        ``document_version_id`` is tri-state:

        - **Not passed** (``_WORKORDER_VERSION_UNSET`` sentinel): the existing
          lock on the row, if any, is preserved untouched. This is the
          backward-compatible path for callers that predate version-lock.
        - **Explicit ``None``**: the lock is cleared
          (``document_version_id = NULL`` + nulled audit columns).
        - **A string**: validated and applied as a new lock.
        """
        existing = (
            self.session.query(WorkorderDocumentLink)
            .filter(
                WorkorderDocumentLink.routing_id == routing_id,
                WorkorderDocumentLink.operation_id == operation_id,
                WorkorderDocumentLink.document_item_id == document_item_id,
            )
            .first()
        )
        if existing:
            link = existing
        else:
            link = WorkorderDocumentLink(
                id=_uuid(),
                routing_id=routing_id,
                operation_id=operation_id,
                document_item_id=document_item_id,
            )
            self.session.add(link)

        link.inherit_to_children = bool(inherit_to_children)
        link.visible_in_production = bool(visible_in_production)

        if document_version_id is _WORKORDER_VERSION_UNSET:
            # Preserve whatever lock state already exists on the row.
            pass
        elif document_version_id is None:
            link.document_version_id = None
            link.version_locked_at = None
            link.version_lock_source = None
        else:
            self._load_owned_version(
                document_item_id=document_item_id,
                document_version_id=document_version_id,
            )
            source = self._resolve_lock_source(version_lock_source)
            link.document_version_id = document_version_id
            link.version_locked_at = _utcnow()
            link.version_lock_source = source

        self.session.flush()
        return link

    def refresh_document_version_locks_for_item(
        self,
        *,
        document_item_id: str,
        document_version_id: str,
        source: str = "eco_apply",
    ) -> int:
        """Re-point every existing workorder-doc link for the given item.

        Strict scope: only rows whose ``document_item_id`` exactly matches the
        target item are touched. No inference from BOM/routing trees. No new
        links are created. Returns the number of rows updated.
        """
        version = self._load_owned_version(
            document_item_id=document_item_id,
            document_version_id=document_version_id,
        )
        resolved_source = self._resolve_lock_source(source)
        rows = (
            self.session.query(WorkorderDocumentLink)
            .filter(WorkorderDocumentLink.document_item_id == document_item_id)
            .all()
        )
        now = _utcnow()
        updated = 0
        for row in rows:
            if row.document_version_id == version.id:
                continue
            row.document_version_id = version.id
            row.version_locked_at = now
            row.version_lock_source = resolved_source
            self.session.add(row)
            updated += 1
        if updated:
            self.session.flush()
        return updated

    def list_links(
        self,
        *,
        routing_id: str,
        operation_id: Optional[str] = None,
        include_inherited: bool = True,
    ) -> List[WorkorderDocumentLink]:
        query = (
            self.session.query(WorkorderDocumentLink)
            .filter(
                WorkorderDocumentLink.routing_id == routing_id,
                WorkorderDocumentLink.visible_in_production.is_(True),
            )
            .order_by(
                WorkorderDocumentLink.operation_id.asc(),
                WorkorderDocumentLink.created_at.asc(),
            )
        )
        links = query.all()
        if operation_id is None:
            return links

        filtered: List[WorkorderDocumentLink] = []
        for link in links:
            if link.operation_id == operation_id:
                filtered.append(link)
                continue
            if include_inherited and link.operation_id is None and link.inherit_to_children:
                filtered.append(link)
        return filtered

    def export_pack(
        self,
        *,
        routing_id: str,
        operation_id: Optional[str] = None,
        include_inherited: bool = True,
        export_meta: Optional[Dict[str, Any]] = None,
        require_locked_versions: bool = False,
    ) -> Dict[str, Any]:
        links = self.list_links(
            routing_id=routing_id,
            operation_id=operation_id,
            include_inherited=include_inherited,
        )
        serialized = [self.serialize_link(link) for link in links]

        unlocked: List[Dict[str, Any]] = []
        mismatched: List[Dict[str, Any]] = []
        stale: List[Dict[str, Any]] = []
        for row in serialized:
            if not row["visible_in_production"]:
                continue
            if not row["version_locked"]:
                unlocked.append(row)
                continue
            if row["version_belongs_to_item"] is False:
                mismatched.append(row)
                continue
            if row["version_is_current"] is False:
                stale.append(row)

        if require_locked_versions and (unlocked or mismatched):
            offending_ids = [row["id"] for row in unlocked + mismatched]
            raise ValueError(
                "require_locked_versions=true: "
                f"{len(unlocked)} unlocked + {len(mismatched)} mismatched links "
                f"(ids={offending_ids})"
            )

        generated_at = _utcnow().isoformat()
        normalized_meta = export_meta if isinstance(export_meta, dict) else {}
        operator_id = normalized_meta.get("operator_id")
        try:
            operator_id = int(operator_id) if operator_id is not None else None
        except Exception:
            operator_id = None
        export_context = {
            "generated_at": generated_at,
            "job_no": str(normalized_meta.get("job_no") or "").strip() or None,
            "operator_id": operator_id,
            "operator_name": str(normalized_meta.get("operator_name") or "").strip() or None,
            "exported_by": str(normalized_meta.get("exported_by") or "").strip() or None,
            "format_version": "workorder-doc-pack.v3",
        }
        locale_context = _resolve_report_locale_context(
            self.session,
            locale_profile_id=normalized_meta.get("locale_profile_id"),
            report_lang=normalized_meta.get("report_lang"),
            report_type=normalized_meta.get("report_type") or "workorder_doc_pack",
        )
        docs = [
            {
                "link_id": row["id"],
                "routing_id": row["routing_id"],
                "operation_id": row["operation_id"],
                "document_item_id": row["document_item_id"],
                "inherit_to_children": row["inherit_to_children"],
                "visible_in_production": row["visible_in_production"],
                "document_scope": row["document_scope"],
                "created_at": row["created_at"],
                "document_version_id": row["document_version_id"],
                "version_locked": row["version_locked"],
                "version_lock_source": row["version_lock_source"],
                "version_locked_at": row["version_locked_at"],
                "version_label": row["version_label"],
                "version_is_current": row["version_is_current"],
                "version_is_released": row["version_is_released"],
            }
            for row in serialized
        ]
        scope_counter = Counter(
            str(row.get("document_scope") or "unknown")
            for row in docs
        )
        visible_count = sum(1 for row in docs if row["visible_in_production"])
        version_lock_summary = {
            "locked": visible_count - len(unlocked) - len(mismatched),
            "unlocked": len(unlocked),
            "mismatched": len(mismatched),
            "stale": len(stale),
            "requires_lock": bool(require_locked_versions),
        }
        manifest = {
            "generated_at": generated_at,
            "routing_id": routing_id,
            "operation_id": operation_id,
            "count": len(docs),
            "scope_summary": dict(scope_counter),
            "version_lock_summary": version_lock_summary,
            "export_meta": export_context,
            "documents": docs,
        }
        if locale_context:
            manifest["locale"] = locale_context

        csv_io = io.StringIO()
        writer = csv.DictWriter(
            csv_io,
            fieldnames=[
                "link_id",
                "routing_id",
                "operation_id",
                "document_item_id",
                "document_scope",
                "inherit_to_children",
                "visible_in_production",
                "created_at",
                "document_version_id",
                "version_locked",
                "version_lock_source",
                "version_locked_at",
                "version_label",
                "version_is_current",
                "version_is_released",
            ],
        )
        writer.writeheader()
        for row in docs:
            writer.writerow(row)

        zip_io = io.BytesIO()
        with ZipFile(zip_io, mode="w", compression=ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr("documents.csv", csv_io.getvalue())
            if locale_context:
                zf.writestr("locale.json", json.dumps(locale_context, ensure_ascii=False, indent=2))

        return {"manifest": manifest, "zip_bytes": zip_io.getvalue()}


class ThreeDOverlayService:
    _CACHE_TTL_SECONDS = 60
    _CACHE_MAX_ENTRIES = 500
    _CACHE_LOCK = Lock()
    _CACHE: Dict[str, Dict[str, Any]] = {}
    _CACHE_HITS = 0
    _CACHE_MISSES = 0
    _CACHE_EVICTIONS = 0

    def __init__(self, session: Session):
        self.session = session

    @classmethod
    def _cache_now(cls) -> float:
        return time.monotonic()

    @classmethod
    def _cache_key(cls, document_item_id: str) -> str:
        return str(document_item_id or "").strip()

    @classmethod
    def _prune_cache_locked(cls, *, now: Optional[float] = None) -> None:
        ts = cls._cache_now() if now is None else float(now)
        expired = [
            key
            for key, row in cls._CACHE.items()
            if float((row or {}).get("expires_at") or 0.0) <= ts
        ]
        for key in expired:
            cls._CACHE.pop(key, None)

        overflow = len(cls._CACHE) - int(cls._CACHE_MAX_ENTRIES)
        if overflow <= 0:
            return

        rows = sorted(
            cls._CACHE.items(),
            key=lambda pair: float((pair[1] or {}).get("stored_at") or 0.0),
        )
        for key, _ in rows[:overflow]:
            cls._CACHE.pop(key, None)
            cls._CACHE_EVICTIONS += 1

    @classmethod
    def reset_cache_for_tests(cls) -> None:
        with cls._CACHE_LOCK:
            cls._CACHE = {}
            cls._CACHE_HITS = 0
            cls._CACHE_MISSES = 0
            cls._CACHE_EVICTIONS = 0

    @classmethod
    def cache_stats(cls) -> Dict[str, Any]:
        with cls._CACHE_LOCK:
            cls._prune_cache_locked()
            return {
                "entries": len(cls._CACHE),
                "hits": int(cls._CACHE_HITS),
                "misses": int(cls._CACHE_MISSES),
                "evictions": int(cls._CACHE_EVICTIONS),
                "ttl_seconds": int(cls._CACHE_TTL_SECONDS),
                "max_entries": int(cls._CACHE_MAX_ENTRIES),
            }

    def _cache_get(self, document_item_id: str) -> Optional[Dict[str, Any]]:
        cls = type(self)
        key = self._cache_key(document_item_id)
        if not key:
            return None
        now = self._cache_now()
        with cls._CACHE_LOCK:
            cls._prune_cache_locked(now=now)
            row = cls._CACHE.get(key)
            if not row:
                cls._CACHE_MISSES += 1
                return None
            cls._CACHE_HITS += 1
            payload = row.get("payload")
            return dict(payload) if isinstance(payload, dict) else None

    def _cache_set(self, document_item_id: str, payload: Dict[str, Any]) -> None:
        cls = type(self)
        key = self._cache_key(document_item_id)
        if not key:
            return
        now = self._cache_now()
        row = {
            "payload": dict(payload),
            "stored_at": now,
            "expires_at": now + float(cls._CACHE_TTL_SECONDS),
        }
        with cls._CACHE_LOCK:
            cls._CACHE[key] = row
            cls._prune_cache_locked(now=now)

    def _cache_invalidate(self, document_item_id: str) -> None:
        cls = type(self)
        key = self._cache_key(document_item_id)
        if not key:
            return
        with cls._CACHE_LOCK:
            cls._CACHE.pop(key, None)

    def _serialize_overlay(self, overlay: ThreeDOverlay) -> Dict[str, Any]:
        part_refs = overlay.part_refs if isinstance(overlay.part_refs, list) else []
        properties = overlay.properties if isinstance(overlay.properties, dict) else {}
        return {
            "id": overlay.id,
            "document_item_id": overlay.document_item_id,
            "version_label": overlay.version_label,
            "status": overlay.status,
            "visibility_role": overlay.visibility_role,
            "part_refs": list(part_refs),
            "properties": dict(properties),
            "created_at": overlay.created_at,
            "updated_at": overlay.updated_at,
        }

    def _overlay_from_payload(self, payload: Dict[str, Any]) -> SimpleNamespace:
        part_refs = payload.get("part_refs")
        properties = payload.get("properties")
        return SimpleNamespace(
            id=payload.get("id"),
            document_item_id=payload.get("document_item_id"),
            version_label=payload.get("version_label"),
            status=payload.get("status"),
            visibility_role=payload.get("visibility_role"),
            part_refs=list(part_refs) if isinstance(part_refs, list) else [],
            properties=dict(properties) if isinstance(properties, dict) else {},
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
        )

    def upsert_overlay(
        self,
        *,
        document_item_id: str,
        version_label: Optional[str] = None,
        status: Optional[str] = None,
        visibility_role: Optional[str] = None,
        part_refs: Optional[List[Dict[str, Any]]] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> ThreeDOverlay:
        overlay = (
            self.session.query(ThreeDOverlay)
            .filter(ThreeDOverlay.document_item_id == document_item_id)
            .first()
        )
        if not overlay:
            overlay = ThreeDOverlay(id=_uuid(), document_item_id=document_item_id)
            self.session.add(overlay)

        overlay.version_label = version_label
        overlay.status = status
        overlay.visibility_role = visibility_role
        overlay.part_refs = part_refs or []
        overlay.properties = properties or {}
        overlay.updated_at = _utcnow()
        self.session.flush()
        self._cache_invalidate(document_item_id)
        self._cache_set(document_item_id, self._serialize_overlay(overlay))
        return overlay

    def get_overlay(
        self, *, document_item_id: str, user_roles: Optional[List[str]] = None
    ) -> Optional[SimpleNamespace]:
        payload = self._cache_get(document_item_id)
        if payload is None:
            overlay = (
                self.session.query(ThreeDOverlay)
                .filter(ThreeDOverlay.document_item_id == document_item_id)
                .first()
            )
            if not overlay:
                return None
            payload = self._serialize_overlay(overlay)
            self._cache_set(document_item_id, payload)

        required_role = str(payload.get("visibility_role") or "").strip().lower()
        if required_role:
            actual_roles = {str(role).strip().lower() for role in (user_roles or [])}
            if required_role not in actual_roles:
                raise PermissionError("Overlay is not visible for current roles")

        return self._overlay_from_payload(payload)

    def upsert_explode(
        self, *, document_item_id: str, explode_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Persist a single validated explode config for a document under the
        existing 3D-overlay row's ``properties["explode"]`` (G3 — no migration).

        Creates the overlay row if absent (empty ``part_refs``). The config
        structure is validated at the router (Pydantic); the server stores it
        verbatim and never interprets geometry. Reuses the overlay
        cache-invalidation seam.
        """
        overlay = (
            self.session.query(ThreeDOverlay)
            .filter(ThreeDOverlay.document_item_id == document_item_id)
            .first()
        )
        if not overlay:
            overlay = ThreeDOverlay(
                id=_uuid(),
                document_item_id=document_item_id,
                part_refs=[],
                properties={},
            )
            self.session.add(overlay)
        props = dict(overlay.properties or {})
        props["explode"] = explode_config
        overlay.properties = props
        overlay.updated_at = _utcnow()
        self.session.flush()
        self._cache_invalidate(document_item_id)
        self._cache_set(document_item_id, self._serialize_overlay(overlay))
        return explode_config

    def get_explode(
        self, *, document_item_id: str, user_roles: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Return the document's explode config (or None).

        Inherits the overlay's role-visibility gate via ``get_overlay`` (raises
        PermissionError if hidden for the current roles).
        """
        overlay = self.get_overlay(
            document_item_id=document_item_id, user_roles=user_roles
        )
        if not overlay:
            return None
        props = overlay.properties if isinstance(overlay.properties, dict) else {}
        return props.get("explode")

    @staticmethod
    def _flatten_bom_nodes(tree: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Flatten a ``BOMService.get_bom_structure`` tree into child nodes
        carrying ``(item_id, relationship_id, depth, order)``. The ROOT is
        EXCLUDED — R1 emits offsets for child components only (taskbook §4).
        """
        nodes: List[Dict[str, Any]] = []

        def _walk(node: Dict[str, Any], depth: int) -> None:
            for order, entry in enumerate(node.get("children") or []):
                if not isinstance(entry, dict):
                    continue
                rel = entry.get("relationship")
                child = entry.get("child")
                child_id = child.get("id") if isinstance(child, dict) else None
                rel_id = rel.get("id") if isinstance(rel, dict) else None
                if child_id:
                    nodes.append(
                        {
                            "item_id": child_id,
                            "relationship_id": rel_id,
                            "depth": depth + 1,
                            "order": order,
                        }
                    )
                if isinstance(child, dict):
                    _walk(child, depth + 1)

        _walk(tree, 0)
        return nodes

    @staticmethod
    def _auto_layout_offset(
        node: Dict[str, Any],
        *,
        axis: str,
        depth_spacing: float,
        sibling_spacing: float,
        factor: float,
    ) -> List[float]:
        """Deterministic, geometry-free offset: the primary ``axis`` by BOM
        depth, an orthogonal axis by sibling order. NOT a spatial-correctness
        guarantee — a client viewer may later replace it with mesh-aware layout.
        """
        axis_index = {"x": 0, "y": 1, "z": 2}[axis]
        ortho_index = (axis_index + 1) % 3
        vec = [0.0, 0.0, 0.0]
        vec[axis_index] = float(node["depth"]) * depth_spacing * factor
        vec[ortho_index] = float(node["order"]) * sibling_spacing * factor
        if not all(math.isfinite(v) for v in vec):
            raise ValueError("auto-layout produced a non-finite offset")
        return vec

    def build_auto_layout(
        self,
        *,
        document_item_id: str,
        root_item_id: str,
        levels: int,
        factor: float,
        depth_spacing: float,
        sibling_spacing: float,
        axis: str,
        user_roles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """G3 BOM auto-layout R1 (taskbook #684): compute a default explode config
        by binding the overlay's ``part_refs`` to BOM nodes and assigning
        deterministic depth-based offsets. READ-ONLY — does not persist.

        The overlay is REQUIRED (no auto-create) and its role-visibility gate is
        applied via ``get_overlay``. Binding LOCK (§4 — never assume
        ``component_ref == item_id``): ``relationship_id``/``bom_relationship_id``
        -> BOM relationship id; else ``item_id`` -> BOM child item id, but ONLY
        when unique — a duplicate ``item_id`` is SKIPPED with an
        ``ambiguous_item_id_fallback`` warning, never guessed; unbound rows are
        skipped with ``unmapped_component_ref``.
        """
        overlay = self.get_overlay(
            document_item_id=document_item_id, user_roles=user_roles
        )
        if not overlay:
            raise ValueError(f"Overlay not found: {document_item_id}")
        part_refs = overlay.part_refs if isinstance(overlay.part_refs, list) else []
        if not part_refs:
            raise ValueError(
                f"Overlay part_refs required for auto-layout: {document_item_id}"
            )

        from yuantus.meta_engine.services.bom_service import BOMService

        tree = BOMService(self.session).get_bom_structure(
            root_item_id, levels=levels, relationship_types=["Part BOM"]
        )
        nodes = self._flatten_bom_nodes(tree)

        by_rel_id: Dict[str, Dict[str, Any]] = {}
        item_id_counts: Dict[str, int] = {}
        by_item_id: Dict[str, Dict[str, Any]] = {}
        for node in nodes:
            rid = node.get("relationship_id")
            if rid and rid not in by_rel_id:
                by_rel_id[rid] = node
            iid = node.get("item_id")
            item_id_counts[iid] = item_id_counts.get(iid, 0) + 1
            if iid not in by_item_id:
                by_item_id[iid] = node

        offsets: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []
        matched = 0
        skipped = 0

        def _warn(ref: str, code: str, message: str) -> None:
            nonlocal skipped
            skipped += 1
            warnings.append({"component_ref": ref, "code": code, "message": message})

        for row in part_refs:
            if not isinstance(row, dict):
                continue
            component_ref = str(row.get("component_ref") or "").strip()
            if not component_ref:
                continue  # cannot key an offset or a warning without a ref
            rel_id = row.get("relationship_id") or row.get("bom_relationship_id")
            node = None
            if rel_id:
                node = by_rel_id.get(rel_id)
                if node is None:
                    _warn(
                        component_ref,
                        "unmapped_component_ref",
                        "relationship_id did not match any BOM relationship",
                    )
                    continue
            elif row.get("item_id"):
                iid = row.get("item_id")
                count = item_id_counts.get(iid, 0)
                if count == 1:
                    node = by_item_id[iid]
                elif count > 1:
                    _warn(
                        component_ref,
                        "ambiguous_item_id_fallback",
                        "item_id matched multiple BOM nodes; skipped",
                    )
                    continue
                else:
                    _warn(
                        component_ref,
                        "unmapped_component_ref",
                        "item_id did not match any BOM node",
                    )
                    continue
            else:
                _warn(
                    component_ref,
                    "unmapped_component_ref",
                    "no relationship_id or item_id binding key",
                )
                continue

            offsets.append(
                {
                    "component_ref": component_ref,
                    "offset": self._auto_layout_offset(
                        node,
                        axis=axis,
                        depth_spacing=depth_spacing,
                        sibling_spacing=sibling_spacing,
                        factor=factor,
                    ),
                }
            )
            matched += 1

        return {
            "explode": {"factor": factor, "mode": "bom-depth", "offsets": offsets},
            "binding": {"matched": matched, "skipped": skipped, "warnings": warnings},
        }

    def resolve_component(
        self,
        *,
        document_item_id: str,
        component_ref: str,
        user_roles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        overlay = self.get_overlay(document_item_id=document_item_id, user_roles=user_roles)
        if not overlay:
            raise ValueError(f"Overlay not found: {document_item_id}")

        refs = overlay.part_refs or []
        if not isinstance(refs, list):
            refs = []
        normalized_ref = str(component_ref).strip().lower()
        for row in refs:
            if not isinstance(row, dict):
                continue
            candidate = str(row.get("component_ref") or "").strip().lower()
            if candidate == normalized_ref:
                return {
                    "document_item_id": document_item_id,
                    "component_ref": component_ref,
                    "hit": row,
                }
        raise ValueError(f"Component not found: {component_ref}")

    def resolve_components(
        self,
        *,
        document_item_id: str,
        component_refs: List[str],
        user_roles: Optional[List[str]] = None,
        include_missing: bool = True,
    ) -> Dict[str, Any]:
        overlay = self.get_overlay(document_item_id=document_item_id, user_roles=user_roles)
        if not overlay:
            raise ValueError(f"Overlay not found: {document_item_id}")

        refs = overlay.part_refs or []
        if not isinstance(refs, list):
            refs = []
        index: Dict[str, Dict[str, Any]] = {}
        for row in refs:
            if not isinstance(row, dict):
                continue
            key = str(row.get("component_ref") or "").strip().lower()
            if key and key not in index:
                index[key] = row

        normalized_refs = [str(ref).strip() for ref in (component_refs or []) if str(ref).strip()]
        rows: List[Dict[str, Any]] = []
        hit_count = 0
        for ref in normalized_refs:
            hit = index.get(ref.lower())
            if hit is not None:
                hit_count += 1
                rows.append(
                    {
                        "component_ref": ref,
                        "found": True,
                        "hit": hit,
                    }
                )
                continue
            if include_missing:
                rows.append(
                    {
                        "component_ref": ref,
                        "found": False,
                        "hit": None,
                    }
                )

        return {
            "document_item_id": document_item_id,
            "requested": len(normalized_refs),
            "returned": len(rows),
            "hits": hit_count,
            "misses": len(normalized_refs) - hit_count,
            "include_missing": bool(include_missing),
            "results": rows,
            "cache": self.cache_stats(),
        }


class ParallelOpsOverviewService:
    _ALLOWED_WINDOW_DAYS = {1, 7, 14, 30, 90}
    _ALLOWED_BUCKET_DAYS = {1, 7, 14, 30}
    _ALLOWED_DOC_SYNC_DIRECTIONS = {"push", "pull"}
    _BREAKAGE_HELPDESK_TASK_TYPE = "breakage_helpdesk_sync_stub"
    _BREAKAGE_HELPDESK_FAILURE_EXPORT_TASK_TYPE = (
        "parallel_ops_breakage_helpdesk_failures_export"
    )
    _DEFAULT_SLO_THRESHOLDS = {
        "overlay_cache_hit_rate_warn": 0.8,
        "overlay_cache_min_requests_warn": 10,
        "doc_sync_dead_letter_rate_warn": 0.05,
        "workflow_failed_rate_warn": 0.02,
        "breakage_open_rate_warn": 0.5,
        "breakage_helpdesk_failed_rate_warn": 0.5,
        "breakage_helpdesk_failed_total_warn": 5,
        "breakage_helpdesk_triage_coverage_warn": 0.8,
        "breakage_helpdesk_export_failed_total_warn": 0,
        "breakage_helpdesk_provider_failed_rate_warn": 0.9,
        "breakage_helpdesk_provider_failed_min_jobs_warn": 5,
        "breakage_helpdesk_provider_failed_rate_critical": 0.98,
        "breakage_helpdesk_provider_failed_min_jobs_critical": 10,
        "breakage_helpdesk_replay_failed_rate_warn": 0.5,
        "breakage_helpdesk_replay_failed_total_warn": 3,
        "breakage_helpdesk_replay_pending_total_warn": 10,
    }
    _ALLOWED_TRIAGE_STATUS = {"open", "in_progress", "resolved", "ignored"}

    def __init__(self, session: Session):
        self.session = session
        self._job_service = JobService(session)

    def _normalize_window_days(self, window_days: int) -> int:
        try:
            value = int(window_days)
        except Exception as exc:
            raise ValueError("window_days must be one of: 1, 7, 14, 30, 90") from exc
        if value not in self._ALLOWED_WINDOW_DAYS:
            raise ValueError("window_days must be one of: 1, 7, 14, 30, 90")
        return value

    def _window_since(self, window_days: int) -> datetime:
        return _utcnow() - timedelta(days=window_days)

    def _safe_ratio(self, numerator: int, denominator: int) -> Optional[float]:
        return (float(numerator) / float(denominator)) if denominator > 0 else None

    def _normalize_rate_threshold(self, value: Optional[float], *, field: str) -> Optional[float]:
        if value is None:
            return None
        try:
            normalized = float(value)
        except Exception as exc:
            raise ValueError(f"{field} must be between 0 and 1") from exc
        if normalized < 0.0 or normalized > 1.0:
            raise ValueError(f"{field} must be between 0 and 1")
        return normalized

    def _normalize_non_negative_int(self, value: Optional[int], *, field: str) -> Optional[int]:
        if value is None:
            return None
        try:
            normalized = int(value)
        except Exception as exc:
            raise ValueError(f"{field} must be >= 0") from exc
        if normalized < 0:
            raise ValueError(f"{field} must be >= 0")
        return normalized

    def _normalize_optional_bool(self, value: Optional[bool], *, field: str) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        raise ValueError(f"{field} must be a boolean")

    def _normalize_page(self, page: int) -> int:
        try:
            value = int(page or 1)
        except Exception as exc:
            raise ValueError("page must be >= 1") from exc
        if value < 1:
            raise ValueError("page must be >= 1")
        return value

    def _normalize_page_size(self, page_size: int) -> int:
        try:
            value = int(page_size or 20)
        except Exception as exc:
            raise ValueError("page_size must be between 1 and 200") from exc
        if value < 1 or value > 200:
            raise ValueError("page_size must be between 1 and 200")
        return value

    def _normalize_top_n(self, value: int, *, field: str = "top_n") -> int:
        try:
            normalized = int(5 if value is None else value)
        except Exception as exc:
            raise ValueError(f"{field} must be between 1 and 50") from exc
        if normalized < 1 or normalized > 50:
            raise ValueError(f"{field} must be between 1 and 50")
        return normalized

    def _normalize_cleanup_ttl_hours(self, ttl_hours: int) -> int:
        try:
            normalized = int(ttl_hours)
        except Exception as exc:
            raise ValueError("ttl_hours must be an integer between 1 and 720") from exc
        if normalized < 1 or normalized > 720:
            raise ValueError("ttl_hours must be between 1 and 720")
        return normalized

    def _normalize_cleanup_limit(self, limit: int) -> int:
        try:
            normalized = int(limit)
        except Exception as exc:
            raise ValueError("limit must be an integer between 1 and 1000") from exc
        if normalized < 1 or normalized > 1000:
            raise ValueError("limit must be between 1 and 1000")
        return normalized

    def _normalize_bulk_limit(self, limit: int) -> int:
        try:
            normalized = int(100 if limit is None else limit)
        except Exception as exc:
            raise ValueError("limit must be an integer between 1 and 500") from exc
        if normalized < 1 or normalized > 500:
            raise ValueError("limit must be between 1 and 500")
        return normalized

    def _normalize_breakage_helpdesk_export_format(self, export_format: str) -> str:
        normalized = str(export_format or "json").strip().lower()
        if normalized not in {"json", "csv", "md", "zip"}:
            raise ValueError("export_format must be json, csv, md or zip")
        return normalized

    def _normalize_triage_status(self, status: str) -> str:
        normalized = str(status or "").strip().lower()
        if not normalized:
            raise ValueError("triage_status must not be empty")
        if normalized not in self._ALLOWED_TRIAGE_STATUS:
            allowed = ", ".join(sorted(self._ALLOWED_TRIAGE_STATUS))
            raise ValueError(f"triage_status must be one of: {allowed}")
        return normalized

    def _normalize_bucket_days(self, bucket_days: int) -> int:
        try:
            value = int(bucket_days or 1)
        except Exception as exc:
            raise ValueError("bucket_days must be one of: 1, 7, 14, 30") from exc
        if value not in self._ALLOWED_BUCKET_DAYS:
            raise ValueError("bucket_days must be one of: 1, 7, 14, 30")
        return value

    def _percentile(self, values: List[float], ratio: float) -> Optional[float]:
        if not values:
            return None
        if len(values) == 1:
            return float(values[0])
        clamped = min(max(float(ratio), 0.0), 1.0)
        ordered = sorted(float(v) for v in values)
        position = (len(ordered) - 1) * clamped
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return float(ordered[lower])
        weight = position - lower
        return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)

    def _duration_stats(self, values: List[float]) -> Dict[str, Any]:
        normalized = sorted(float(v) for v in values if v is not None and float(v) >= 0.0)
        if not normalized:
            return {
                "count": 0,
                "min_seconds": None,
                "max_seconds": None,
                "avg_seconds": None,
                "p50_seconds": None,
                "p95_seconds": None,
            }
        count = len(normalized)
        avg = float(sum(normalized) / float(count))
        return {
            "count": int(count),
            "min_seconds": round(float(normalized[0]), 4),
            "max_seconds": round(float(normalized[-1]), 4),
            "avg_seconds": round(avg, 4),
            "p50_seconds": round(float(self._percentile(normalized, 0.5) or 0.0), 4),
            "p95_seconds": round(float(self._percentile(normalized, 0.95) or 0.0), 4),
        }

    def _parse_iso_datetime(self, value: Any) -> Optional[datetime]:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except Exception:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    def _normalize_doc_sync_direction(self, value: Any) -> Optional[str]:
        normalized = str(value or "").strip().lower()
        if normalized in self._ALLOWED_DOC_SYNC_DIRECTIONS:
            return normalized
        return None

    def _doc_sync_direction_for_job(
        self, job: ConversionJob, payload: Optional[Dict[str, Any]] = None
    ) -> str:
        data = payload if isinstance(payload, dict) else {}
        direction = self._normalize_doc_sync_direction(data.get("direction"))
        if direction:
            return direction

        task_type = str(job.task_type or "").strip().lower()
        if task_type.startswith("document_sync_"):
            suffix = task_type[len("document_sync_") :]
            normalized_suffix = self._normalize_doc_sync_direction(suffix)
            if normalized_suffix:
                return normalized_suffix
        return "unknown"

    def _paginate(
        self, rows: List[Dict[str, Any]], *, page: int, page_size: int
    ) -> Dict[str, Any]:
        total = len(rows)
        start = (page - 1) * page_size
        end = start + page_size
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": ((total + page_size - 1) // page_size) if page_size else 0,
            "rows": rows[start:end],
        }

    def _doc_sync_summary(
        self,
        *,
        since: datetime,
        site_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type.like("document_sync_%"))
            .filter(ConversionJob.created_at >= since)
            .order_by(ConversionJob.created_at.desc())
            .all()
        )
        filtered: List[ConversionJob] = []
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            if site_id and str(payload.get("site_id") or "").strip() != str(site_id).strip():
                continue
            filtered.append(job)

        by_status = Counter(str(job.status or "unknown").lower() for job in filtered)
        by_direction = Counter()
        for job in filtered:
            payload = job.payload if isinstance(job.payload, dict) else {}
            by_direction[self._doc_sync_direction_for_job(job, payload)] += 1
        dead_letter = [
            job
            for job in filtered
            if str(job.status or "").lower() == JobStatus.FAILED.value
            and int(job.max_attempts or 0) > 0
            and int(job.attempt_count or 0) >= int(job.max_attempts or 0)
        ]
        success_count = int(by_status.get(JobStatus.COMPLETED.value, 0))
        total = len(filtered)
        avg_attempts = (
            sum(int(job.attempt_count or 0) for job in filtered) / float(total)
            if total > 0
            else 0.0
        )
        return {
            "total": total,
            "by_status": dict(by_status),
            "by_direction": dict(by_direction),
            "success_rate": self._safe_ratio(success_count, total),
            "dead_letter_total": len(dead_letter),
            "dead_letter_rate": self._safe_ratio(len(dead_letter), total),
            "avg_attempt_count": round(avg_attempts, 4),
            "site_filter": site_id,
        }

    def _doc_sync_checkout_gate_snapshot(
        self,
        *,
        doc_sync_summary: Dict[str, Any],
        block_on_dead_letter_only: Optional[bool],
        max_pending_warn: Optional[int],
        max_processing_warn: Optional[int],
        max_failed_warn: Optional[int],
        max_dead_letter_warn: Optional[int],
    ) -> Dict[str, Any]:
        policy_input = self._normalize_optional_bool(
            block_on_dead_letter_only,
            field="doc_sync_checkout_gate_block_on_dead_letter_only",
        )
        threshold_inputs = {
            "pending": self._normalize_non_negative_int(
                max_pending_warn,
                field="doc_sync_checkout_gate_max_pending_warn",
            ),
            "processing": self._normalize_non_negative_int(
                max_processing_warn,
                field="doc_sync_checkout_gate_max_processing_warn",
            ),
            "failed": self._normalize_non_negative_int(
                max_failed_warn,
                field="doc_sync_checkout_gate_max_failed_warn",
            ),
            "dead_letter": self._normalize_non_negative_int(
                max_dead_letter_warn,
                field="doc_sync_checkout_gate_max_dead_letter_warn",
            ),
        }
        enabled = policy_input is not None or any(
            value is not None for value in threshold_inputs.values()
        )
        policy = {"block_on_dead_letter_only": bool(policy_input or False)}

        thresholds: Dict[str, Optional[int]] = {}
        for status, value in threshold_inputs.items():
            if not enabled:
                thresholds[status] = None
            else:
                thresholds[status] = int(value if value is not None else 0)

        by_status = (
            doc_sync_summary.get("by_status")
            if isinstance(doc_sync_summary.get("by_status"), dict)
            else {}
        )
        counts = {
            "pending": int(by_status.get(JobStatus.PENDING.value, 0) or 0),
            "processing": int(by_status.get(JobStatus.PROCESSING.value, 0) or 0),
            "failed": int(by_status.get(JobStatus.FAILED.value, 0) or 0),
            "dead_letter": int(doc_sync_summary.get("dead_letter_total") or 0),
        }
        considered = (
            ("dead_letter",)
            if policy["block_on_dead_letter_only"]
            else ("pending", "processing", "failed", "dead_letter")
        )
        threshold_hits: List[Dict[str, Any]] = []
        if enabled:
            for status in considered:
                threshold = thresholds.get(status)
                if threshold is None:
                    continue
                observed = int(counts.get(status) or 0)
                if observed > int(threshold):
                    threshold_hits.append(
                        {
                            "status": status,
                            "count": observed,
                            "threshold": int(threshold),
                            "exceeded_by": int(observed - int(threshold)),
                        }
                    )
        return {
            "enabled": bool(enabled),
            "policy": policy,
            "thresholds": thresholds,
            "counts": counts,
            "threshold_hits": threshold_hits,
            "threshold_hits_total": len(threshold_hits),
            "is_blocking": bool(threshold_hits),
        }

    def _doc_sync_dead_letter_trend_snapshot(
        self,
        *,
        since: datetime,
        site_id: Optional[str] = None,
        bucket_days: int = 1,
    ) -> Dict[str, Any]:
        normalized_bucket = self._normalize_bucket_days(bucket_days)
        now = _utcnow()
        bucket_span = timedelta(days=normalized_bucket)
        bucket_seconds = float(bucket_span.total_seconds())

        points: List[Dict[str, Any]] = []
        cursor = since
        while cursor < now:
            bucket_end = min(cursor + bucket_span, now)
            points.append(
                {
                    "bucket_start": cursor.isoformat(),
                    "bucket_end": bucket_end.isoformat(),
                    "total": 0,
                    "dead_letter_total": 0,
                    "directions": {"push": 0, "pull": 0, "unknown": 0},
                    "dead_letter_directions": {"push": 0, "pull": 0, "unknown": 0},
                }
            )
            cursor = bucket_end
        if not points:
            points.append(
                {
                    "bucket_start": since.isoformat(),
                    "bucket_end": now.isoformat(),
                    "total": 0,
                    "dead_letter_total": 0,
                    "directions": {"push": 0, "pull": 0, "unknown": 0},
                    "dead_letter_directions": {"push": 0, "pull": 0, "unknown": 0},
                }
            )

        def bucket_index(created_at: Optional[datetime]) -> Optional[int]:
            if created_at is None:
                return None
            delta = (created_at - since).total_seconds()
            if delta < 0:
                return None
            idx = int(delta // bucket_seconds) if bucket_seconds > 0 else 0
            if idx < 0:
                return None
            if idx >= len(points):
                return len(points) - 1
            return idx

        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type.like("document_sync_%"))
            .filter(ConversionJob.created_at >= since)
            .all()
        )
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            current_site_id = str(payload.get("site_id") or "").strip() or None
            if site_id and current_site_id != str(site_id).strip():
                continue
            idx = bucket_index(job.created_at)
            if idx is None:
                continue
            point = points[idx]
            point["total"] = int(point.get("total") or 0) + 1
            direction_key = self._doc_sync_direction_for_job(job, payload)
            direction_counts = (
                point.get("directions") if isinstance(point.get("directions"), dict) else {}
            )
            direction_counts[direction_key] = int(direction_counts.get(direction_key) or 0) + 1
            point["directions"] = direction_counts
            status = str(job.status or "").strip().lower()
            max_attempts = int(job.max_attempts or 0)
            attempt_count = int(job.attempt_count or 0)
            is_dead_letter = (
                status == JobStatus.FAILED.value
                and max_attempts > 0
                and attempt_count >= max_attempts
            )
            if is_dead_letter:
                point["dead_letter_total"] = int(point.get("dead_letter_total") or 0) + 1
                dead_direction_counts = (
                    point.get("dead_letter_directions")
                    if isinstance(point.get("dead_letter_directions"), dict)
                    else {}
                )
                dead_direction_counts[direction_key] = (
                    int(dead_direction_counts.get(direction_key) or 0) + 1
                )
                point["dead_letter_directions"] = dead_direction_counts

        values: List[int] = []
        direction_totals = Counter()
        dead_letter_direction_totals = Counter()
        for point in points:
            total = int(point.get("total") or 0)
            dead_letter_total = int(point.get("dead_letter_total") or 0)
            point["dead_letter_rate"] = self._safe_ratio(dead_letter_total, total)
            values.append(dead_letter_total)
            for key, count in (point.get("directions") or {}).items():
                direction_totals[str(key)] += int(count or 0)
            for key, count in (point.get("dead_letter_directions") or {}).items():
                dead_letter_direction_totals[str(key)] += int(count or 0)
        first = int(values[0]) if values else 0
        latest = int(values[-1]) if values else 0
        max_value = int(max(values)) if values else 0
        min_value = int(min(values)) if values else 0
        delta = int(max_value - min_value)
        direction = "flat"
        if latest > first:
            direction = "up"
        elif latest < first:
            direction = "down"

        return {
            "bucket_days": normalized_bucket,
            "points": points,
            "aggregates": {
                "first_dead_letter_total": first,
                "latest_dead_letter_total": latest,
                "min_dead_letter_total": min_value,
                "max_dead_letter_total": max_value,
                "delta_dead_letter_total": delta,
                "nonzero_buckets": int(sum(1 for value in values if int(value) > 0)),
                "by_direction": dict(direction_totals),
                "dead_letter_by_direction": dict(dead_letter_direction_totals),
            },
            "direction": direction,
        }

    def _workflow_summary(
        self,
        *,
        since: datetime,
        target_object: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = self.session.query(WorkflowCustomActionRun).filter(
            WorkflowCustomActionRun.created_at >= since
        )
        if target_object:
            query = query.filter(WorkflowCustomActionRun.target_object == target_object)
        runs = query.order_by(WorkflowCustomActionRun.created_at.desc()).all()

        by_status = Counter(str(run.status or "unknown").lower() for run in runs)
        by_result_code = Counter(
            str((run.result or {}).get("result_code") or "UNKNOWN")
            for run in runs
            if isinstance(run.result, dict)
        )
        failed_count = int(by_status.get("failed", 0))
        warning_count = int(by_status.get("warning", 0))
        total = len(runs)
        return {
            "total": total,
            "by_status": dict(by_status),
            "by_result_code": dict(by_result_code),
            "failed_rate": self._safe_ratio(failed_count, total),
            "warning_rate": self._safe_ratio(warning_count, total),
            "target_object_filter": target_object,
        }

    def _breakage_summary(self, *, since: datetime) -> Dict[str, Any]:
        incidents = (
            self.session.query(BreakageIncident)
            .filter(BreakageIncident.created_at >= since)
            .order_by(BreakageIncident.created_at.desc())
            .all()
        )
        total = len(incidents)
        by_status = Counter(str(row.status or "unknown").lower() for row in incidents)
        by_severity = Counter(str(row.severity or "unknown").lower() for row in incidents)
        by_responsibility = Counter(
            str(row.responsibility or "unassigned") for row in incidents
        )
        open_total = int(by_status.get("open", 0))
        descriptions = Counter(str(row.description or "").strip().lower() for row in incidents)
        repeated_total = sum(
            count
            for key, count in descriptions.items()
            if key and count > 1
        )
        return {
            "total": total,
            "by_status": dict(by_status),
            "by_severity": dict(by_severity),
            "by_responsibility": dict(by_responsibility),
            "open_total": open_total,
            "open_rate": self._safe_ratio(open_total, total),
            "repeated_total": repeated_total,
            "repeated_rate": self._safe_ratio(repeated_total, total),
        }

    def _breakage_helpdesk_summary(self, *, since: datetime) -> Dict[str, Any]:
        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type == self._BREAKAGE_HELPDESK_TASK_TYPE)
            .filter(ConversionJob.created_at >= since)
            .order_by(ConversionJob.created_at.desc())
            .all()
        )
        by_job_status = Counter()
        by_sync_status = Counter()
        by_provider = Counter()
        by_provider_failed = Counter()
        by_provider_ticket_status = Counter()
        by_triage_status = Counter()
        replay_batches: set[str] = set()
        replay_by_job_status = Counter()
        replay_by_sync_status = Counter()
        replay_by_provider = Counter()
        replay_jobs_total = 0
        replay_failed_jobs = 0
        replay_pending_jobs = 0
        with_external_ticket = 0
        triaged_jobs = 0
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            sync_info = (
                payload.get("helpdesk_sync")
                if isinstance(payload.get("helpdesk_sync"), dict)
                else {}
            )
            integration = (
                payload.get("integration")
                if isinstance(payload.get("integration"), dict)
                else {}
            )
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            triage = payload.get("triage") if isinstance(payload.get("triage"), dict) else {}
            job_status = str(job.status or "unknown").strip().lower() or "unknown"
            by_job_status[job_status] += 1

            sync_status = (
                str(sync_info.get("sync_status") or result.get("sync_status") or "")
                .strip()
                .lower()
                or job_status
            )
            by_sync_status[sync_status] += 1

            provider = (
                str(
                    sync_info.get("provider")
                    or integration.get("provider")
                    or result.get("provider")
                    or payload.get("provider")
                    or "stub"
                )
                .strip()
                .lower()
                or "stub"
            )
            by_provider[provider] += 1
            is_failed_job = (
                sync_status == JobStatus.FAILED.value
                or job_status == JobStatus.FAILED.value
            )
            if is_failed_job:
                by_provider_failed[provider] += 1

            provider_ticket_status = (
                str(
                    sync_info.get("provider_ticket_status")
                    or result.get("provider_ticket_status")
                    or payload.get("provider_ticket_status")
                    or ""
                )
                .strip()
                .lower()
            )
            if provider_ticket_status:
                by_provider_ticket_status[provider_ticket_status] += 1

            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            replay = metadata.get("replay") if isinstance(metadata.get("replay"), dict) else {}
            replay_batch_id = str(replay.get("batch_id") or "").strip()
            if replay_batch_id:
                replay_archived = bool(replay.get("archived"))
                if replay_archived:
                    continue
                replay_jobs_total += 1
                replay_batches.add(replay_batch_id)
                replay_by_job_status[job_status] += 1
                replay_by_sync_status[sync_status] += 1
                replay_by_provider[provider] += 1
                if is_failed_job:
                    replay_failed_jobs += 1
                if sync_status in {
                    JobStatus.PENDING.value,
                    JobStatus.PROCESSING.value,
                    "queued",
                }:
                    replay_pending_jobs += 1

            triage_status = (
                str(triage.get("status") or payload.get("triage_status") or "")
                .strip()
                .lower()
                or "open"
            )
            if is_failed_job:
                by_triage_status[triage_status] += 1
                is_triaged = (
                    triage_status != "open"
                    or bool(str(triage.get("owner") or "").strip())
                    or bool(str(triage.get("root_cause") or "").strip())
                    or bool(str(triage.get("resolution") or "").strip())
                    or bool(str(triage.get("note") or "").strip())
                )
                if is_triaged:
                    triaged_jobs += 1

            external_ticket_id = (
                sync_info.get("external_ticket_id")
                or result.get("external_ticket_id")
                or payload.get("external_ticket_id")
            )
            if external_ticket_id:
                with_external_ticket += 1

        failed_jobs = int(by_job_status.get(JobStatus.FAILED.value, 0))
        total_jobs = len(jobs)
        provider_failed_rates: Dict[str, Dict[str, Any]] = {}
        for provider, total in sorted(by_provider.items()):
            provider_failed_jobs = int(by_provider_failed.get(provider, 0))
            provider_failed_rates[provider] = {
                "total_jobs": int(total),
                "failed_jobs": provider_failed_jobs,
                "failed_rate": self._safe_ratio(provider_failed_jobs, int(total)),
            }
        return {
            "total_jobs": len(jobs),
            "by_job_status": dict(by_job_status),
            "by_sync_status": dict(by_sync_status),
            "by_provider": dict(by_provider),
            "by_provider_failed": dict(by_provider_failed),
            "provider_failed_rates": provider_failed_rates,
            "by_provider_ticket_status": dict(by_provider_ticket_status),
            "by_triage_status": dict(by_triage_status),
            "providers_total": len(by_provider),
            "with_external_ticket": with_external_ticket,
            "with_provider_ticket_status": int(sum(by_provider_ticket_status.values())),
            "failed_jobs": failed_jobs,
            "failed_rate": self._safe_ratio(failed_jobs, total_jobs),
            "triaged_jobs": int(triaged_jobs),
            "triage_rate": self._safe_ratio(int(triaged_jobs), failed_jobs),
            "replay_jobs_total": int(replay_jobs_total),
            "replay_batches_total": int(len(replay_batches)),
            "replay_failed_jobs": int(replay_failed_jobs),
            "replay_failed_rate": self._safe_ratio(int(replay_failed_jobs), int(replay_jobs_total)),
            "replay_by_job_status": dict(replay_by_job_status),
            "replay_by_sync_status": dict(replay_by_sync_status),
            "replay_by_provider": dict(replay_by_provider),
            "replay_pending_jobs": int(replay_pending_jobs),
        }

    def _breakage_helpdesk_export_job_summary(self, *, since: datetime) -> Dict[str, Any]:
        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type == self._BREAKAGE_HELPDESK_FAILURE_EXPORT_TASK_TYPE)
            .filter(ConversionJob.created_at >= since)
            .order_by(ConversionJob.created_at.desc())
            .all()
        )
        by_job_status = Counter(str(job.status or "unknown").strip().lower() for job in jobs)
        expired_jobs = 0
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            export_info = payload.get("export") if isinstance(payload.get("export"), dict) else {}
            if str(export_info.get("sync_status") or "").strip().lower() == "expired":
                expired_jobs += 1
        total_jobs = len(jobs)
        failed_jobs = int(by_job_status.get(JobStatus.FAILED.value, 0))
        return {
            "total_jobs": total_jobs,
            "by_job_status": dict(by_job_status),
            "pending_jobs": int(by_job_status.get(JobStatus.PENDING.value, 0)),
            "processing_jobs": int(by_job_status.get(JobStatus.PROCESSING.value, 0)),
            "completed_jobs": int(by_job_status.get(JobStatus.COMPLETED.value, 0)),
            "failed_jobs": failed_jobs,
            "expired_jobs": int(expired_jobs),
            "failed_rate": self._safe_ratio(failed_jobs, total_jobs),
        }

    def _consumption_template_summary(
        self, *, template_key: Optional[str] = None
    ) -> Dict[str, Any]:
        plans = (
            self.session.query(ConsumptionPlan)
            .order_by(ConsumptionPlan.created_at.desc())
            .all()
        )
        template_rows: List[ConsumptionPlan] = []
        for plan in plans:
            props = plan.properties if isinstance(plan.properties, dict) else {}
            template = props.get("template")
            if not isinstance(template, dict):
                continue
            if not bool(template.get("is_template_version")):
                continue
            key = str(template.get("key") or "").strip()
            if not key:
                continue
            if template_key and key != str(template_key).strip():
                continue
            template_rows.append(plan)

        active_by_key: Counter = Counter()
        all_keys = set()
        for row in template_rows:
            props = row.properties if isinstance(row.properties, dict) else {}
            template = props.get("template")
            if not isinstance(template, dict):
                continue
            key = str(template.get("key") or "").strip()
            if not key:
                continue
            all_keys.add(key)
            if bool(template.get("is_active")):
                active_by_key[key] += 1

        invalid_templates = [key for key, count in active_by_key.items() if count != 1]
        templates_without_active = [key for key in all_keys if active_by_key.get(key, 0) == 0]

        return {
            "versions_total": len(template_rows),
            "templates_total": len(all_keys),
            "active_versions_total": int(sum(active_by_key.values())),
            "invalid_active_templates": sorted(invalid_templates),
            "templates_without_active": sorted(templates_without_active),
            "template_key_filter": template_key,
        }

    def _overlay_cache_summary(self) -> Dict[str, Any]:
        cache = ThreeDOverlayService.cache_stats()
        requests = int(cache.get("hits") or 0) + int(cache.get("misses") or 0)
        hit_rate = self._safe_ratio(int(cache.get("hits") or 0), requests)
        return {
            **cache,
            "requests": requests,
            "hit_rate": hit_rate,
        }

    def summary(
        self,
        *,
        window_days: int = 7,
        site_id: Optional[str] = None,
        target_object: Optional[str] = None,
        template_key: Optional[str] = None,
        overlay_cache_hit_rate_warn: Optional[float] = None,
        overlay_cache_min_requests_warn: Optional[int] = None,
        doc_sync_dead_letter_rate_warn: Optional[float] = None,
        workflow_failed_rate_warn: Optional[float] = None,
        breakage_open_rate_warn: Optional[float] = None,
        breakage_helpdesk_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_triage_coverage_warn: Optional[float] = None,
        breakage_helpdesk_export_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_provider_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_provider_failed_min_jobs_warn: Optional[int] = None,
        breakage_helpdesk_provider_failed_rate_critical: Optional[float] = None,
        breakage_helpdesk_provider_failed_min_jobs_critical: Optional[int] = None,
        breakage_helpdesk_replay_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_replay_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_replay_pending_total_warn: Optional[int] = None,
        doc_sync_checkout_gate_block_on_dead_letter_only: Optional[bool] = None,
        doc_sync_checkout_gate_max_pending_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_processing_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_failed_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_dead_letter_warn: Optional[int] = None,
        doc_sync_dead_letter_trend_delta_warn: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        since = self._window_since(normalized_window)
        thresholds = {
            "overlay_cache_hit_rate_warn": (
                self._normalize_rate_threshold(
                    overlay_cache_hit_rate_warn,
                    field="overlay_cache_hit_rate_warn",
                )
                if overlay_cache_hit_rate_warn is not None
                else float(self._DEFAULT_SLO_THRESHOLDS["overlay_cache_hit_rate_warn"])
            ),
            "overlay_cache_min_requests_warn": (
                self._normalize_non_negative_int(
                    overlay_cache_min_requests_warn,
                    field="overlay_cache_min_requests_warn",
                )
                if overlay_cache_min_requests_warn is not None
                else int(self._DEFAULT_SLO_THRESHOLDS["overlay_cache_min_requests_warn"])
            ),
            "doc_sync_dead_letter_rate_warn": (
                self._normalize_rate_threshold(
                    doc_sync_dead_letter_rate_warn,
                    field="doc_sync_dead_letter_rate_warn",
                )
                if doc_sync_dead_letter_rate_warn is not None
                else float(self._DEFAULT_SLO_THRESHOLDS["doc_sync_dead_letter_rate_warn"])
            ),
            "workflow_failed_rate_warn": (
                self._normalize_rate_threshold(
                    workflow_failed_rate_warn,
                    field="workflow_failed_rate_warn",
                )
                if workflow_failed_rate_warn is not None
                else float(self._DEFAULT_SLO_THRESHOLDS["workflow_failed_rate_warn"])
            ),
            "breakage_open_rate_warn": (
                self._normalize_rate_threshold(
                    breakage_open_rate_warn,
                    field="breakage_open_rate_warn",
                )
                if breakage_open_rate_warn is not None
                else float(self._DEFAULT_SLO_THRESHOLDS["breakage_open_rate_warn"])
            ),
            "breakage_helpdesk_failed_rate_warn": (
                self._normalize_rate_threshold(
                    breakage_helpdesk_failed_rate_warn,
                    field="breakage_helpdesk_failed_rate_warn",
                )
                if breakage_helpdesk_failed_rate_warn is not None
                else float(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_failed_rate_warn"])
            ),
            "breakage_helpdesk_failed_total_warn": (
                self._normalize_non_negative_int(
                    breakage_helpdesk_failed_total_warn,
                    field="breakage_helpdesk_failed_total_warn",
                )
                if breakage_helpdesk_failed_total_warn is not None
                else int(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_failed_total_warn"])
            ),
            "breakage_helpdesk_triage_coverage_warn": (
                self._normalize_rate_threshold(
                    breakage_helpdesk_triage_coverage_warn,
                    field="breakage_helpdesk_triage_coverage_warn",
                )
                if breakage_helpdesk_triage_coverage_warn is not None
                else float(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_triage_coverage_warn"])
            ),
            "breakage_helpdesk_export_failed_total_warn": (
                self._normalize_non_negative_int(
                    breakage_helpdesk_export_failed_total_warn,
                    field="breakage_helpdesk_export_failed_total_warn",
                )
                if breakage_helpdesk_export_failed_total_warn is not None
                else int(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_export_failed_total_warn"])
            ),
            "breakage_helpdesk_provider_failed_rate_warn": (
                self._normalize_rate_threshold(
                    breakage_helpdesk_provider_failed_rate_warn,
                    field="breakage_helpdesk_provider_failed_rate_warn",
                )
                if breakage_helpdesk_provider_failed_rate_warn is not None
                else float(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_provider_failed_rate_warn"])
            ),
            "breakage_helpdesk_provider_failed_min_jobs_warn": (
                self._normalize_non_negative_int(
                    breakage_helpdesk_provider_failed_min_jobs_warn,
                    field="breakage_helpdesk_provider_failed_min_jobs_warn",
                )
                if breakage_helpdesk_provider_failed_min_jobs_warn is not None
                else int(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_provider_failed_min_jobs_warn"])
            ),
            "breakage_helpdesk_provider_failed_rate_critical": (
                self._normalize_rate_threshold(
                    breakage_helpdesk_provider_failed_rate_critical,
                    field="breakage_helpdesk_provider_failed_rate_critical",
                )
                if breakage_helpdesk_provider_failed_rate_critical is not None
                else float(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_provider_failed_rate_critical"])
            ),
            "breakage_helpdesk_provider_failed_min_jobs_critical": (
                self._normalize_non_negative_int(
                    breakage_helpdesk_provider_failed_min_jobs_critical,
                    field="breakage_helpdesk_provider_failed_min_jobs_critical",
                )
                if breakage_helpdesk_provider_failed_min_jobs_critical is not None
                else int(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_provider_failed_min_jobs_critical"])
            ),
            "breakage_helpdesk_replay_failed_rate_warn": (
                self._normalize_rate_threshold(
                    breakage_helpdesk_replay_failed_rate_warn,
                    field="breakage_helpdesk_replay_failed_rate_warn",
                )
                if breakage_helpdesk_replay_failed_rate_warn is not None
                else float(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_replay_failed_rate_warn"])
            ),
            "breakage_helpdesk_replay_failed_total_warn": (
                self._normalize_non_negative_int(
                    breakage_helpdesk_replay_failed_total_warn,
                    field="breakage_helpdesk_replay_failed_total_warn",
                )
                if breakage_helpdesk_replay_failed_total_warn is not None
                else int(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_replay_failed_total_warn"])
            ),
            "breakage_helpdesk_replay_pending_total_warn": (
                self._normalize_non_negative_int(
                    breakage_helpdesk_replay_pending_total_warn,
                    field="breakage_helpdesk_replay_pending_total_warn",
                )
                if breakage_helpdesk_replay_pending_total_warn is not None
                else int(self._DEFAULT_SLO_THRESHOLDS["breakage_helpdesk_replay_pending_total_warn"])
            ),
            "doc_sync_checkout_gate_block_on_dead_letter_only": self._normalize_optional_bool(
                doc_sync_checkout_gate_block_on_dead_letter_only,
                field="doc_sync_checkout_gate_block_on_dead_letter_only",
            ),
            "doc_sync_checkout_gate_max_pending_warn": self._normalize_non_negative_int(
                doc_sync_checkout_gate_max_pending_warn,
                field="doc_sync_checkout_gate_max_pending_warn",
            ),
            "doc_sync_checkout_gate_max_processing_warn": self._normalize_non_negative_int(
                doc_sync_checkout_gate_max_processing_warn,
                field="doc_sync_checkout_gate_max_processing_warn",
            ),
            "doc_sync_checkout_gate_max_failed_warn": self._normalize_non_negative_int(
                doc_sync_checkout_gate_max_failed_warn,
                field="doc_sync_checkout_gate_max_failed_warn",
            ),
            "doc_sync_checkout_gate_max_dead_letter_warn": self._normalize_non_negative_int(
                doc_sync_checkout_gate_max_dead_letter_warn,
                field="doc_sync_checkout_gate_max_dead_letter_warn",
            ),
            "doc_sync_dead_letter_trend_delta_warn": self._normalize_non_negative_int(
                doc_sync_dead_letter_trend_delta_warn,
                field="doc_sync_dead_letter_trend_delta_warn",
            ),
        }

        doc_sync = self._doc_sync_summary(since=since, site_id=site_id)
        checkout_gate = self._doc_sync_checkout_gate_snapshot(
            doc_sync_summary=doc_sync,
            block_on_dead_letter_only=thresholds.get(
                "doc_sync_checkout_gate_block_on_dead_letter_only"
            ),
            max_pending_warn=thresholds.get("doc_sync_checkout_gate_max_pending_warn"),
            max_processing_warn=thresholds.get("doc_sync_checkout_gate_max_processing_warn"),
            max_failed_warn=thresholds.get("doc_sync_checkout_gate_max_failed_warn"),
            max_dead_letter_warn=thresholds.get("doc_sync_checkout_gate_max_dead_letter_warn"),
        )
        doc_sync["checkout_gate"] = checkout_gate
        doc_sync["dead_letter_trend"] = self._doc_sync_dead_letter_trend_snapshot(
            since=since,
            site_id=site_id,
            bucket_days=1,
        )
        workflow = self._workflow_summary(since=since, target_object=target_object)
        breakages = self._breakage_summary(since=since)
        breakages["helpdesk"] = self._breakage_helpdesk_summary(since=since)
        breakages["helpdesk_export"] = self._breakage_helpdesk_export_job_summary(since=since)
        consumption_templates = self._consumption_template_summary(
            template_key=template_key
        )
        overlay_cache = self._overlay_cache_summary()

        hints: List[Dict[str, Any]] = []
        if (
            overlay_cache.get("requests", 0)
            >= int(thresholds["overlay_cache_min_requests_warn"])
            and (overlay_cache.get("hit_rate") or 0.0)
            < float(thresholds["overlay_cache_hit_rate_warn"])
        ):
            hints.append(
                {
                    "code": "overlay_cache_hit_rate_low",
                    "level": "warn",
                    "message": (
                        "Overlay cache hit rate is below "
                        f"{float(thresholds['overlay_cache_hit_rate_warn']):.4f} "
                        "in current process window"
                    ),
                }
            )
        if (doc_sync.get("dead_letter_rate") or 0.0) > float(
            thresholds["doc_sync_dead_letter_rate_warn"]
        ):
            hints.append(
                {
                    "code": "doc_sync_dead_letter_rate_high",
                    "level": "warn",
                    "message": (
                        "Document sync dead-letter rate is above "
                        f"{float(thresholds['doc_sync_dead_letter_rate_warn']):.4f}"
                    ),
                }
            )
        checkout_gate = (
            doc_sync.get("checkout_gate")
            if isinstance(doc_sync.get("checkout_gate"), dict)
            else {}
        )
        if bool(checkout_gate.get("enabled")) and int(
            checkout_gate.get("threshold_hits_total") or 0
        ) > 0:
            hit_codes = ",".join(
                str(row.get("status") or "")
                for row in (checkout_gate.get("threshold_hits") or [])
                if isinstance(row, dict)
            )
            hints.append(
                {
                    "code": "doc_sync_checkout_gate_threshold_hit",
                    "level": "warn",
                    "message": (
                        "Doc-sync checkout gate thresholds exceeded"
                        + (f" for statuses={hit_codes}" if hit_codes else "")
                    ),
                }
            )
        dead_letter_trend = (
            doc_sync.get("dead_letter_trend")
            if isinstance(doc_sync.get("dead_letter_trend"), dict)
            else {}
        )
        trend_aggregates = (
            dead_letter_trend.get("aggregates")
            if isinstance(dead_letter_trend.get("aggregates"), dict)
            else {}
        )
        trend_delta_warn = thresholds.get("doc_sync_dead_letter_trend_delta_warn")
        if trend_delta_warn is not None and int(
            trend_aggregates.get("delta_dead_letter_total") or 0
        ) > int(trend_delta_warn):
            hints.append(
                {
                    "code": "doc_sync_dead_letter_trend_up",
                    "level": "warn",
                    "message": (
                        "Doc-sync dead-letter trend delta is above "
                        f"{int(trend_delta_warn)}"
                    ),
                }
            )
        if (workflow.get("failed_rate") or 0.0) > float(
            thresholds["workflow_failed_rate_warn"]
        ):
            hints.append(
                {
                    "code": "workflow_action_failed_rate_high",
                    "level": "warn",
                    "message": (
                        "Workflow custom action failed rate is above "
                        f"{float(thresholds['workflow_failed_rate_warn']):.4f}"
                    ),
                }
            )
        if (breakages.get("open_rate") or 0.0) > float(
            thresholds["breakage_open_rate_warn"]
        ):
            hints.append(
                {
                    "code": "breakage_open_rate_high",
                    "level": "warn",
                    "message": (
                        "Open breakage ratio is above "
                        f"{float(thresholds['breakage_open_rate_warn']):.4f}"
                    ),
                }
            )
        helpdesk = (
            breakages.get("helpdesk") if isinstance(breakages.get("helpdesk"), dict) else {}
        )
        if (helpdesk.get("failed_rate") or 0.0) > float(
            thresholds["breakage_helpdesk_failed_rate_warn"]
        ):
            hints.append(
                {
                    "code": "breakage_helpdesk_failed_rate_high",
                    "level": "warn",
                    "message": (
                        "Breakage-helpdesk sync failed rate is above "
                        f"{float(thresholds['breakage_helpdesk_failed_rate_warn']):.4f}"
                    ),
                }
            )
        if int(helpdesk.get("failed_jobs") or 0) > int(
            thresholds["breakage_helpdesk_failed_total_warn"]
        ):
            hints.append(
                {
                    "code": "breakage_helpdesk_failed_total_high",
                    "level": "warn",
                    "message": (
                        "Breakage-helpdesk failed jobs are above "
                        f"{int(thresholds['breakage_helpdesk_failed_total_warn'])}"
                    ),
                }
            )
        provider_failed_rates = (
            helpdesk.get("provider_failed_rates")
            if isinstance(helpdesk.get("provider_failed_rates"), dict)
            else {}
        )
        for provider, provider_row in sorted(provider_failed_rates.items()):
            if not isinstance(provider_row, dict):
                continue
            provider_total_jobs = int(provider_row.get("total_jobs") or 0)
            provider_failed_rate = provider_row.get("failed_rate")
            if provider_failed_rate is None:
                continue
            is_critical = (
                provider_total_jobs
                >= int(thresholds["breakage_helpdesk_provider_failed_min_jobs_critical"])
                and float(provider_failed_rate)
                > float(thresholds["breakage_helpdesk_provider_failed_rate_critical"])
            )
            if is_critical:
                hints.append(
                    {
                        "code": "breakage_helpdesk_provider_failed_rate_critical",
                        "level": "critical",
                        "provider": provider,
                        "message": (
                            "Breakage-helpdesk provider failed rate is above critical "
                            f"{float(thresholds['breakage_helpdesk_provider_failed_rate_critical']):.4f} "
                            f"for provider={provider}"
                        ),
                    }
                )
                continue
            is_warn = (
                provider_total_jobs
                >= int(thresholds["breakage_helpdesk_provider_failed_min_jobs_warn"])
                and float(provider_failed_rate)
                > float(thresholds["breakage_helpdesk_provider_failed_rate_warn"])
            )
            if is_warn:
                hints.append(
                    {
                        "code": "breakage_helpdesk_provider_failed_rate_high",
                        "level": "warn",
                        "provider": provider,
                        "message": (
                            "Breakage-helpdesk provider failed rate is above "
                            f"{float(thresholds['breakage_helpdesk_provider_failed_rate_warn']):.4f} "
                            f"for provider={provider}"
                        ),
                    }
                )
        if (helpdesk.get("replay_failed_rate") or 0.0) > float(
            thresholds["breakage_helpdesk_replay_failed_rate_warn"]
        ):
            hints.append(
                {
                    "code": "breakage_helpdesk_replay_failed_rate_high",
                    "level": "warn",
                    "message": (
                        "Breakage-helpdesk replay failed rate is above "
                        f"{float(thresholds['breakage_helpdesk_replay_failed_rate_warn']):.4f}"
                    ),
                }
            )
        if int(helpdesk.get("replay_failed_jobs") or 0) > int(
            thresholds["breakage_helpdesk_replay_failed_total_warn"]
        ):
            hints.append(
                {
                    "code": "breakage_helpdesk_replay_failed_total_high",
                    "level": "warn",
                    "message": (
                        "Breakage-helpdesk replay failed jobs are above "
                        f"{int(thresholds['breakage_helpdesk_replay_failed_total_warn'])}"
                    ),
                }
            )
        if int(helpdesk.get("replay_pending_jobs") or 0) > int(
            thresholds["breakage_helpdesk_replay_pending_total_warn"]
        ):
            hints.append(
                {
                    "code": "breakage_helpdesk_replay_pending_total_high",
                    "level": "warn",
                    "message": (
                        "Breakage-helpdesk replay pending jobs are above "
                        f"{int(thresholds['breakage_helpdesk_replay_pending_total_warn'])}"
                    ),
                }
            )
        if int(helpdesk.get("failed_jobs") or 0) > 0 and (
            (helpdesk.get("triage_rate") or 0.0)
            < float(thresholds["breakage_helpdesk_triage_coverage_warn"])
        ):
            hints.append(
                {
                    "code": "breakage_helpdesk_triage_coverage_low",
                    "level": "warn",
                    "message": (
                        "Breakage-helpdesk triage coverage is below "
                        f"{float(thresholds['breakage_helpdesk_triage_coverage_warn']):.4f}"
                    ),
                }
            )
        helpdesk_export = (
            breakages.get("helpdesk_export")
            if isinstance(breakages.get("helpdesk_export"), dict)
            else {}
        )
        if int(helpdesk_export.get("failed_jobs") or 0) > int(
            thresholds["breakage_helpdesk_export_failed_total_warn"]
        ):
            hints.append(
                {
                    "code": "breakage_helpdesk_export_failed_total_high",
                    "level": "warn",
                    "message": (
                        "Breakage-helpdesk export failed jobs are above "
                        f"{int(thresholds['breakage_helpdesk_export_failed_total_warn'])}"
                    ),
                }
            )

        return {
            "generated_at": _utcnow().isoformat(),
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "doc_sync": doc_sync,
            "workflow_actions": workflow,
            "breakages": breakages,
            "consumption_templates": consumption_templates,
            "overlay_cache": overlay_cache,
            "slo_hints": hints,
            "slo_thresholds": thresholds,
        }

    def trends(
        self,
        *,
        window_days: int = 7,
        bucket_days: int = 1,
        site_id: Optional[str] = None,
        target_object: Optional[str] = None,
        template_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        normalized_bucket = self._normalize_bucket_days(bucket_days)
        if normalized_bucket > normalized_window:
            raise ValueError("bucket_days must be <= window_days")

        since = self._window_since(normalized_window)
        now = _utcnow()
        bucket_span = timedelta(days=normalized_bucket)

        points: List[Dict[str, Any]] = []
        cursor = since
        while cursor < now:
            bucket_end = min(cursor + bucket_span, now)
            points.append(
                {
                    "bucket_start": cursor.isoformat(),
                    "bucket_end": bucket_end.isoformat(),
                    "doc_sync": {
                        "total": 0,
                        "failed_total": 0,
                        "dead_letter_total": 0,
                        "directions": {"push": 0, "pull": 0, "unknown": 0},
                        "dead_letter_directions": {"push": 0, "pull": 0, "unknown": 0},
                    },
                    "workflow_actions": {
                        "total": 0,
                        "failed_total": 0,
                    },
                    "breakages": {
                        "total": 0,
                        "open_total": 0,
                    },
                    "_doc_sync_success_total": 0,
                }
            )
            cursor = bucket_end

        if not points:
            points.append(
                {
                    "bucket_start": since.isoformat(),
                    "bucket_end": now.isoformat(),
                    "doc_sync": {
                        "total": 0,
                        "failed_total": 0,
                        "dead_letter_total": 0,
                        "directions": {"push": 0, "pull": 0, "unknown": 0},
                        "dead_letter_directions": {"push": 0, "pull": 0, "unknown": 0},
                    },
                    "workflow_actions": {
                        "total": 0,
                        "failed_total": 0,
                    },
                    "breakages": {
                        "total": 0,
                        "open_total": 0,
                    },
                    "_doc_sync_success_total": 0,
                }
            )

        bucket_seconds = float(bucket_span.total_seconds())

        def bucket_index(created_at: Optional[datetime]) -> Optional[int]:
            if created_at is None:
                return None
            delta = (created_at - since).total_seconds()
            if delta < 0:
                return None
            idx = int(delta // bucket_seconds) if bucket_seconds > 0 else 0
            if idx < 0:
                return None
            if idx >= len(points):
                return len(points) - 1
            return idx

        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type.like("document_sync_%"))
            .filter(ConversionJob.created_at >= since)
            .all()
        )
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            row_site_id = str(payload.get("site_id") or "").strip()
            if site_id and row_site_id != str(site_id).strip():
                continue
            idx = bucket_index(job.created_at)
            if idx is None:
                continue
            row = points[idx]
            doc_sync = row["doc_sync"]
            doc_sync["total"] += 1
            direction_key = self._doc_sync_direction_for_job(job, payload)
            direction_counts = (
                doc_sync.get("directions") if isinstance(doc_sync.get("directions"), dict) else {}
            )
            direction_counts[direction_key] = int(direction_counts.get(direction_key) or 0) + 1
            doc_sync["directions"] = direction_counts
            status = str(job.status or "").lower()
            if status == JobStatus.COMPLETED.value:
                row["_doc_sync_success_total"] += 1
            if status == JobStatus.FAILED.value:
                doc_sync["failed_total"] += 1
                max_attempts = int(job.max_attempts or 0)
                attempt_count = int(job.attempt_count or 0)
                if max_attempts > 0 and attempt_count >= max_attempts:
                    doc_sync["dead_letter_total"] += 1
                    dead_direction_counts = (
                        doc_sync.get("dead_letter_directions")
                        if isinstance(doc_sync.get("dead_letter_directions"), dict)
                        else {}
                    )
                    dead_direction_counts[direction_key] = (
                        int(dead_direction_counts.get(direction_key) or 0) + 1
                    )
                    doc_sync["dead_letter_directions"] = dead_direction_counts

        workflow_query = self.session.query(WorkflowCustomActionRun).filter(
            WorkflowCustomActionRun.created_at >= since
        )
        if target_object:
            workflow_query = workflow_query.filter(
                WorkflowCustomActionRun.target_object == target_object
            )
        for run in workflow_query.all():
            idx = bucket_index(run.created_at)
            if idx is None:
                continue
            row = points[idx]
            workflow = row["workflow_actions"]
            workflow["total"] += 1
            if str(run.status or "").lower() == "failed":
                workflow["failed_total"] += 1

        incidents = (
            self.session.query(BreakageIncident)
            .filter(BreakageIncident.created_at >= since)
            .all()
        )
        for incident in incidents:
            idx = bucket_index(incident.created_at)
            if idx is None:
                continue
            row = points[idx]
            breakages = row["breakages"]
            breakages["total"] += 1
            if str(incident.status or "").lower() == "open":
                breakages["open_total"] += 1

        for row in points:
            doc_sync = row["doc_sync"]
            success_total = int(row.pop("_doc_sync_success_total", 0))
            doc_sync["success_rate"] = self._safe_ratio(success_total, doc_sync["total"])
            doc_sync["dead_letter_rate"] = self._safe_ratio(
                doc_sync["dead_letter_total"],
                doc_sync["total"],
            )

            workflow = row["workflow_actions"]
            workflow["failed_rate"] = self._safe_ratio(
                workflow["failed_total"],
                workflow["total"],
            )

            breakages = row["breakages"]
            breakages["open_rate"] = self._safe_ratio(
                breakages["open_total"],
                breakages["total"],
            )

        return {
            "generated_at": now.isoformat(),
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "bucket_days": normalized_bucket,
            "filters": {
                "site_id": site_id,
                "target_object": target_object,
                "template_key": template_key,
            },
            "points": points,
            "aggregates": {
                "doc_sync_total": int(sum((row["doc_sync"]["total"] or 0) for row in points)),
                "doc_sync_failed_total": int(
                    sum((row["doc_sync"]["failed_total"] or 0) for row in points)
                ),
                "doc_sync_dead_letter_total": int(
                    sum((row["doc_sync"]["dead_letter_total"] or 0) for row in points)
                ),
                "doc_sync_push_total": int(
                    sum(
                        ((row["doc_sync"].get("directions") or {}).get("push") or 0)
                        for row in points
                    )
                ),
                "doc_sync_pull_total": int(
                    sum(
                        ((row["doc_sync"].get("directions") or {}).get("pull") or 0)
                        for row in points
                    )
                ),
                "doc_sync_unknown_direction_total": int(
                    sum(
                        ((row["doc_sync"].get("directions") or {}).get("unknown") or 0)
                        for row in points
                    )
                ),
                "doc_sync_dead_letter_push_total": int(
                    sum(
                        ((row["doc_sync"].get("dead_letter_directions") or {}).get("push") or 0)
                        for row in points
                    )
                ),
                "doc_sync_dead_letter_pull_total": int(
                    sum(
                        ((row["doc_sync"].get("dead_letter_directions") or {}).get("pull") or 0)
                        for row in points
                    )
                ),
                "workflow_total": int(
                    sum((row["workflow_actions"]["total"] or 0) for row in points)
                ),
                "workflow_failed_total": int(
                    sum((row["workflow_actions"]["failed_total"] or 0) for row in points)
                ),
                "breakages_total": int(sum((row["breakages"]["total"] or 0) for row in points)),
                "breakages_open_total": int(
                    sum((row["breakages"]["open_total"] or 0) for row in points)
                ),
            },
            "consumption_templates": self._consumption_template_summary(
                template_key=template_key
            ),
            "overlay_cache": self._overlay_cache_summary(),
        }

    def doc_sync_failures(
        self,
        *,
        window_days: int = 7,
        site_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        normalized_page = self._normalize_page(page)
        normalized_page_size = self._normalize_page_size(page_size)
        since = self._window_since(normalized_window)

        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type.like("document_sync_%"))
            .filter(ConversionJob.status == JobStatus.FAILED.value)
            .filter(ConversionJob.created_at >= since)
            .order_by(ConversionJob.created_at.desc())
            .all()
        )
        rows: List[Dict[str, Any]] = []
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            row_site_id = str(payload.get("site_id") or "").strip() or None
            if site_id and row_site_id != str(site_id).strip():
                continue
            direction = self._doc_sync_direction_for_job(job, payload)
            rows.append(
                {
                    "id": job.id,
                    "task_type": job.task_type,
                    "status": job.status,
                    "attempt_count": int(job.attempt_count or 0),
                    "max_attempts": int(job.max_attempts or 0),
                    "last_error": job.last_error,
                    "dedupe_key": job.dedupe_key,
                    "site_id": row_site_id,
                    "direction": direction,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                }
            )

        paged = self._paginate(rows, page=normalized_page, page_size=normalized_page_size)
        by_direction = Counter(str(row.get("direction") or "unknown") for row in rows)
        return {
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "site_filter": site_id,
            "total": paged["total"],
            "by_direction": dict(by_direction),
            "pagination": {
                "page": paged["page"],
                "page_size": paged["page_size"],
                "pages": paged["pages"],
                "total": paged["total"],
            },
            "jobs": paged["rows"],
        }

    def workflow_failures(
        self,
        *,
        window_days: int = 7,
        target_object: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        normalized_page = self._normalize_page(page)
        normalized_page_size = self._normalize_page_size(page_size)
        since = self._window_since(normalized_window)

        query = (
            self.session.query(WorkflowCustomActionRun)
            .filter(WorkflowCustomActionRun.status == "failed")
            .filter(WorkflowCustomActionRun.created_at >= since)
        )
        if target_object:
            query = query.filter(WorkflowCustomActionRun.target_object == target_object)
        runs = query.order_by(WorkflowCustomActionRun.created_at.desc()).all()

        rows: List[Dict[str, Any]] = []
        for run in runs:
            result = run.result if isinstance(run.result, dict) else {}
            rows.append(
                {
                    "id": run.id,
                    "rule_id": run.rule_id,
                    "object_id": run.object_id,
                    "target_object": run.target_object,
                    "from_state": run.from_state,
                    "to_state": run.to_state,
                    "trigger_phase": run.trigger_phase,
                    "status": run.status,
                    "attempts": int(run.attempts or 0),
                    "result_code": result.get("result_code"),
                    "last_error": run.last_error,
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                }
            )

        paged = self._paginate(rows, page=normalized_page, page_size=normalized_page_size)
        return {
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "target_object_filter": target_object,
            "total": paged["total"],
            "pagination": {
                "page": paged["page"],
                "page_size": paged["page_size"],
                "pages": paged["pages"],
                "total": paged["total"],
            },
            "runs": paged["rows"],
        }

    def _collect_breakage_helpdesk_rows(
        self,
        *,
        since: datetime,
        provider_filter: Optional[str] = None,
        provider_ticket_status_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type == self._BREAKAGE_HELPDESK_TASK_TYPE)
            .filter(ConversionJob.created_at >= since)
            .order_by(ConversionJob.created_at.desc())
            .all()
        )

        rows: List[Dict[str, Any]] = []
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            helpdesk_sync = (
                payload.get("helpdesk_sync")
                if isinstance(payload.get("helpdesk_sync"), dict)
                else {}
            )
            integration = (
                payload.get("integration")
                if isinstance(payload.get("integration"), dict)
                else {}
            )
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            triage = payload.get("triage") if isinstance(payload.get("triage"), dict) else {}

            job_status = str(job.status or "").strip().lower()
            sync_status = str(
                helpdesk_sync.get("sync_status")
                or result.get("sync_status")
                or payload.get("sync_status")
                or job_status
            ).strip().lower()
            provider_value = (
                str(
                    helpdesk_sync.get("provider")
                    or integration.get("provider")
                    or result.get("provider")
                    or payload.get("provider")
                    or "unknown"
                )
                .strip()
                .lower()
                or "unknown"
            )
            if provider_filter and provider_value != provider_filter:
                continue
            provider_ticket_status_value = (
                str(
                    helpdesk_sync.get("provider_ticket_status")
                    or result.get("provider_ticket_status")
                    or payload.get("provider_ticket_status")
                    or ""
                )
                .strip()
                .lower()
            )
            if provider_ticket_status_filter and (
                provider_ticket_status_value != provider_ticket_status_filter
            ):
                continue

            failure_category_value = (
                str(
                    helpdesk_sync.get("failure_category")
                    or result.get("failure_category")
                    or payload.get("failure_category")
                    or ""
                )
                .strip()
                .lower()
                or "unknown"
            )
            row = {
                "id": job.id,
                "incident_id": payload.get("incident_id"),
                "provider": provider_value,
                "sync_status": sync_status,
                "failure_category": failure_category_value,
                "error_code": (
                    helpdesk_sync.get("error_code")
                    or result.get("error_code")
                    or payload.get("error_code")
                ),
                "error_message": (
                    helpdesk_sync.get("error_message")
                    or result.get("error_message")
                    or payload.get("error_message")
                    or job.last_error
                ),
                "provider_ticket_status": provider_ticket_status_value or None,
                "triage_status": (
                    str(triage.get("status") or payload.get("triage_status") or "")
                    .strip()
                    .lower()
                    or "open"
                ),
                "triage_owner": str(triage.get("owner") or "").strip() or None,
                "triage_root_cause": str(triage.get("root_cause") or "").strip() or None,
                "triage_resolution": str(triage.get("resolution") or "").strip() or None,
                "triage_note": str(triage.get("note") or "").strip() or None,
                "triage_updated_at": triage.get("updated_at"),
                "triage_updated_by_id": triage.get("updated_by_id"),
                "external_ticket_id": (
                    helpdesk_sync.get("external_ticket_id")
                    or result.get("external_ticket_id")
                    or payload.get("external_ticket_id")
                ),
                "task_type": job.task_type,
                "status": job.status,
                "attempt_count": int(job.attempt_count or 0),
                "max_attempts": int(job.max_attempts or 0),
                "last_error": job.last_error,
                "dedupe_key": job.dedupe_key,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "_is_failed": (
                    sync_status == JobStatus.FAILED.value
                    or job_status == JobStatus.FAILED.value
                ),
                "_created_at_dt": job.created_at,
            }
            rows.append(row)
        return rows

    def _collect_breakage_helpdesk_replay_rows(
        self,
        *,
        since: Optional[datetime] = None,
        batch_id_filter: Optional[str] = None,
        provider_filter: Optional[str] = None,
        job_status_filter: Optional[str] = None,
        sync_status_filter: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        query = self.session.query(ConversionJob).filter(
            ConversionJob.task_type == self._BREAKAGE_HELPDESK_TASK_TYPE
        )
        if since is not None:
            query = query.filter(ConversionJob.created_at >= since)
        jobs = query.order_by(ConversionJob.created_at.desc()).all()

        rows: List[Dict[str, Any]] = []
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            replay = metadata.get("replay") if isinstance(metadata.get("replay"), dict) else {}
            batch_id = str(replay.get("batch_id") or "").strip()
            if not batch_id:
                continue
            replay_archived = bool(replay.get("archived"))
            if replay_archived and not include_archived:
                continue
            if batch_id_filter and batch_id != batch_id_filter:
                continue

            helpdesk_sync = (
                payload.get("helpdesk_sync")
                if isinstance(payload.get("helpdesk_sync"), dict)
                else {}
            )
            integration = (
                payload.get("integration")
                if isinstance(payload.get("integration"), dict)
                else {}
            )
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            job_status = str(job.status or "unknown").strip().lower() or "unknown"
            if job_status_filter and job_status != job_status_filter:
                continue
            sync_status = (
                str(
                    helpdesk_sync.get("sync_status")
                    or result.get("sync_status")
                    or payload.get("sync_status")
                    or job_status
                )
                .strip()
                .lower()
                or job_status
            )
            if sync_status_filter and sync_status != sync_status_filter:
                continue
            provider_value = (
                str(
                    helpdesk_sync.get("provider")
                    or integration.get("provider")
                    or result.get("provider")
                    or payload.get("provider")
                    or "unknown"
                )
                .strip()
                .lower()
                or "unknown"
            )
            if provider_filter and provider_value != provider_filter:
                continue
            failure_category_value = (
                str(
                    helpdesk_sync.get("failure_category")
                    or result.get("failure_category")
                    or payload.get("failure_category")
                    or "unknown"
                )
                .strip()
                .lower()
                or "unknown"
            )
            requested_total_raw = replay.get("requested_total")
            try:
                requested_total = (
                    int(requested_total_raw) if requested_total_raw is not None else None
                )
            except Exception:
                requested_total = None
            requested_by_raw = replay.get("requested_by_id")
            try:
                requested_by_id = (
                    int(requested_by_raw) if requested_by_raw is not None else None
                )
            except Exception:
                requested_by_id = None
            replay_index_raw = replay.get("replay_index")
            try:
                replay_index = int(replay_index_raw) if replay_index_raw is not None else None
            except Exception:
                replay_index = None
            requested_at = replay.get("requested_at")
            requested_at_dt = self._parse_iso_datetime(requested_at)

            row_duration_seconds: Optional[float] = None
            if job.started_at and job.completed_at and job.completed_at >= job.started_at:
                row_duration_seconds = float((job.completed_at - job.started_at).total_seconds())
            rows.append(
                {
                    "batch_id": batch_id,
                    "job_id": str(job.id),
                    "source_job_id": str(replay.get("source_job_id") or "").strip() or None,
                    "replay_index": replay_index,
                    "incident_id": payload.get("incident_id"),
                    "provider": provider_value,
                    "sync_status": sync_status,
                    "status": job_status,
                    "failure_category": failure_category_value,
                    "idempotency_key": (
                        helpdesk_sync.get("idempotency_key")
                        or integration.get("idempotency_key")
                        or payload.get("idempotency_key")
                    ),
                    "requested_at": requested_at,
                    "requested_by_id": requested_by_id,
                    "requested_total": requested_total,
                    "archived": replay_archived,
                    "archived_at": replay.get("archived_at"),
                    "attempt_count": int(job.attempt_count or 0),
                    "max_attempts": int(job.max_attempts or 0),
                    "last_error": job.last_error,
                    "duration_seconds": (
                        round(row_duration_seconds, 4)
                        if row_duration_seconds is not None
                        else None
                    ),
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                    "_created_at_dt": job.created_at,
                    "_requested_at_dt": requested_at_dt,
                    "_duration_seconds": row_duration_seconds,
                }
            )
        return rows

    def breakage_helpdesk_failures(
        self,
        *,
        window_days: int = 7,
        provider: Optional[str] = None,
        failure_category: Optional[str] = None,
        provider_ticket_status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        normalized_page = self._normalize_page(page)
        normalized_page_size = self._normalize_page_size(page_size)
        since = self._window_since(normalized_window)

        provider_filter = str(provider or "").strip().lower() or None
        category_filter = str(failure_category or "").strip().lower() or None
        provider_ticket_status_filter = (
            str(provider_ticket_status or "").strip().lower() or None
        )
        raw_rows = self._collect_breakage_helpdesk_rows(
            since=since,
            provider_filter=provider_filter,
            provider_ticket_status_filter=provider_ticket_status_filter,
        )

        rows: List[Dict[str, Any]] = []
        by_provider: Counter[str] = Counter()
        by_failure_category: Counter[str] = Counter()
        by_provider_ticket_status: Counter[str] = Counter()
        for raw_row in raw_rows:
            if not bool(raw_row.get("_is_failed")):
                continue
            failure_category_value = str(raw_row.get("failure_category") or "unknown")
            if category_filter and failure_category_value != category_filter:
                continue

            by_provider[str(raw_row.get("provider") or "unknown")] += 1
            by_failure_category[failure_category_value] += 1
            provider_ticket_status_value = (
                str(raw_row.get("provider_ticket_status") or "").strip().lower() or "none"
            )
            by_provider_ticket_status[provider_ticket_status_value] += 1
            row = {
                key: value
                for key, value in raw_row.items()
                if key not in {"_is_failed", "_created_at_dt"}
            }
            rows.append(row)

        paged = self._paginate(rows, page=normalized_page, page_size=normalized_page_size)
        return {
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "provider_filter": provider_filter,
            "failure_category_filter": category_filter,
            "provider_ticket_status_filter": provider_ticket_status_filter,
            "total": paged["total"],
            "pagination": {
                "page": paged["page"],
                "page_size": paged["page_size"],
                "pages": paged["pages"],
                "total": paged["total"],
            },
            "by_provider": dict(by_provider),
            "by_failure_category": dict(by_failure_category),
            "by_provider_ticket_status": dict(by_provider_ticket_status),
            "jobs": paged["rows"],
        }

    def breakage_helpdesk_failure_trends(
        self,
        *,
        window_days: int = 7,
        bucket_days: int = 1,
        provider: Optional[str] = None,
        failure_category: Optional[str] = None,
        provider_ticket_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        normalized_bucket = self._normalize_bucket_days(bucket_days)
        if normalized_bucket > normalized_window:
            raise ValueError("bucket_days must be <= window_days")

        since = self._window_since(normalized_window)
        now = _utcnow()
        bucket_span = timedelta(days=normalized_bucket)
        bucket_seconds = float(bucket_span.total_seconds())

        points: List[Dict[str, Any]] = []
        cursor = since
        while cursor < now:
            bucket_end = min(cursor + bucket_span, now)
            points.append(
                {
                    "bucket_start": cursor.isoformat(),
                    "bucket_end": bucket_end.isoformat(),
                    "total_jobs": 0,
                    "failed_jobs": 0,
                    "_failed_by_category": Counter(),
                }
            )
            cursor = bucket_end
        if not points:
            points.append(
                {
                    "bucket_start": since.isoformat(),
                    "bucket_end": now.isoformat(),
                    "total_jobs": 0,
                    "failed_jobs": 0,
                    "_failed_by_category": Counter(),
                }
            )

        def bucket_index(created_at: Optional[datetime]) -> Optional[int]:
            if created_at is None:
                return None
            delta = (created_at - since).total_seconds()
            if delta < 0:
                return None
            idx = int(delta // bucket_seconds) if bucket_seconds > 0 else 0
            if idx < 0:
                return None
            if idx >= len(points):
                return len(points) - 1
            return idx

        provider_filter = str(provider or "").strip().lower() or None
        category_filter = str(failure_category or "").strip().lower() or None
        provider_ticket_status_filter = (
            str(provider_ticket_status or "").strip().lower() or None
        )
        raw_rows = self._collect_breakage_helpdesk_rows(
            since=since,
            provider_filter=provider_filter,
            provider_ticket_status_filter=provider_ticket_status_filter,
        )
        for raw_row in raw_rows:
            idx = bucket_index(raw_row.get("_created_at_dt"))
            if idx is None:
                continue
            bucket_row = points[idx]
            bucket_row["total_jobs"] += 1
            if not bool(raw_row.get("_is_failed")):
                continue
            row_failure_category = str(raw_row.get("failure_category") or "unknown")
            if category_filter and row_failure_category != category_filter:
                continue
            bucket_row["failed_jobs"] += 1
            bucket_row["_failed_by_category"][row_failure_category] += 1

        by_failure_category: Counter[str] = Counter()
        for row in points:
            failed_by_category = row.pop("_failed_by_category", Counter())
            if isinstance(failed_by_category, Counter):
                by_failure_category.update(failed_by_category)
                row["by_failure_category"] = dict(failed_by_category)
            else:
                row["by_failure_category"] = {}
            row["failed_rate"] = self._safe_ratio(
                int(row.get("failed_jobs") or 0),
                int(row.get("total_jobs") or 0),
            )

        total_jobs = int(sum(int(row.get("total_jobs") or 0) for row in points))
        failed_jobs = int(sum(int(row.get("failed_jobs") or 0) for row in points))
        return {
            "generated_at": now.isoformat(),
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "bucket_days": normalized_bucket,
            "filters": {
                "provider": provider_filter,
                "failure_category": category_filter,
                "provider_ticket_status": provider_ticket_status_filter,
            },
            "points": points,
            "aggregates": {
                "total_jobs": total_jobs,
                "failed_jobs": failed_jobs,
                "failed_rate": self._safe_ratio(failed_jobs, total_jobs),
            },
            "by_failure_category": dict(by_failure_category),
        }

    def breakage_helpdesk_failure_triage(
        self,
        *,
        window_days: int = 7,
        provider: Optional[str] = None,
        failure_category: Optional[str] = None,
        provider_ticket_status: Optional[str] = None,
        top_n: int = 5,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        normalized_top_n = self._normalize_top_n(top_n, field="top_n")
        since = self._window_since(normalized_window)
        provider_filter = str(provider or "").strip().lower() or None
        category_filter = str(failure_category or "").strip().lower() or None
        provider_ticket_status_filter = (
            str(provider_ticket_status or "").strip().lower() or None
        )
        raw_rows = self._collect_breakage_helpdesk_rows(
            since=since,
            provider_filter=provider_filter,
            provider_ticket_status_filter=provider_ticket_status_filter,
        )

        by_provider: Counter[str] = Counter()
        by_failure_category: Counter[str] = Counter()
        by_provider_ticket_status: Counter[str] = Counter()
        by_error_code: Counter[str] = Counter()
        by_triage_status: Counter[str] = Counter()
        failed_rows: List[Dict[str, Any]] = []
        replay_candidates: List[Dict[str, Any]] = []
        triaged_jobs = 0

        for raw_row in raw_rows:
            if not bool(raw_row.get("_is_failed")):
                continue
            failure_category_value = str(raw_row.get("failure_category") or "unknown")
            if category_filter and failure_category_value != category_filter:
                continue
            provider_value = str(raw_row.get("provider") or "unknown")
            provider_ticket_status_value = (
                str(raw_row.get("provider_ticket_status") or "").strip().lower() or "none"
            )
            error_code_value = (
                str(raw_row.get("error_code") or "").strip().lower() or "unknown"
            )
            triage_status_value = (
                str(raw_row.get("triage_status") or "").strip().lower() or "open"
            )
            is_triaged = (
                triage_status_value != "open"
                or bool(str(raw_row.get("triage_owner") or "").strip())
                or bool(str(raw_row.get("triage_root_cause") or "").strip())
                or bool(str(raw_row.get("triage_resolution") or "").strip())
                or bool(str(raw_row.get("triage_note") or "").strip())
            )

            by_provider[provider_value] += 1
            by_failure_category[failure_category_value] += 1
            by_provider_ticket_status[provider_ticket_status_value] += 1
            by_error_code[error_code_value] += 1
            by_triage_status[triage_status_value] += 1
            if is_triaged:
                triaged_jobs += 1

            row = {
                key: value
                for key, value in raw_row.items()
                if key not in {"_is_failed", "_created_at_dt"}
            }
            failed_rows.append(row)

            attempt_count = int(raw_row.get("attempt_count") or 0)
            max_attempts = int(raw_row.get("max_attempts") or 0)
            can_replay = max_attempts <= 0 or attempt_count < max_attempts
            if can_replay:
                replay_candidates.append(
                    {
                        "id": row.get("id"),
                        "incident_id": row.get("incident_id"),
                        "provider": provider_value,
                        "failure_category": failure_category_value,
                        "error_code": error_code_value,
                        "provider_ticket_status": row.get("provider_ticket_status"),
                        "attempt_count": attempt_count,
                        "max_attempts": max_attempts,
                        "created_at": row.get("created_at"),
                    }
                )

        total_failed_jobs = len(failed_rows)

        def _counter_rows(counter: Counter[str]) -> List[Dict[str, Any]]:
            rows: List[Dict[str, Any]] = []
            for key, count in counter.most_common(normalized_top_n):
                rows.append(
                    {
                        "key": key,
                        "count": int(count),
                        "ratio": self._safe_ratio(int(count), total_failed_jobs),
                    }
                )
            return rows

        category_actions = {
            "transient": {
                "code": "retry_with_backoff",
                "title": "Retry transient provider failures",
                "playbook": "Apply delayed replay with exponential backoff and provider health checks.",
            },
            "auth": {
                "code": "rotate_provider_credentials",
                "title": "Fix provider authentication",
                "playbook": "Rotate/rebind credentials and validate auth mode before replay.",
            },
            "validation": {
                "code": "fix_payload_mapping",
                "title": "Fix request payload validation",
                "playbook": "Patch payload schema mapping, then replay failed jobs.",
            },
            "conflict": {
                "code": "enforce_idempotency",
                "title": "Resolve provider conflict/idempotency errors",
                "playbook": "Check idempotency key strategy and dedupe policy before retry.",
            },
            "provider": {
                "code": "escalate_provider_incident",
                "title": "Escalate provider-side incident",
                "playbook": "Escalate with provider logs and temporary fallback handling.",
            },
        }

        triage_actions: List[Dict[str, Any]] = []
        emitted_codes: set[str] = set()
        for row in _counter_rows(by_failure_category):
            key = str(row.get("key") or "")
            action = category_actions.get(key)
            if not action:
                continue
            code = str(action.get("code") or "")
            if not code or code in emitted_codes:
                continue
            emitted_codes.add(code)
            triage_actions.append(
                {
                    "code": code,
                    "title": action.get("title"),
                    "playbook": action.get("playbook"),
                    "evidence": {
                        "failure_category": key,
                        "count": row.get("count"),
                        "ratio": row.get("ratio"),
                    },
                }
            )
        if not triage_actions and total_failed_jobs > 0:
            triage_actions.append(
                {
                    "code": "inspect_job_payload_and_logs",
                    "title": "Inspect unknown failure pattern",
                    "playbook": "Inspect payload.error_code/error_message and provider response body before retry.",
                    "evidence": {"total_failed_jobs": total_failed_jobs},
                }
            )

        return {
            "generated_at": _utcnow().isoformat(),
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "filters": {
                "provider": provider_filter,
                "failure_category": category_filter,
                "provider_ticket_status": provider_ticket_status_filter,
            },
            "top_n": normalized_top_n,
            "total_failed_jobs": total_failed_jobs,
            "by_provider": dict(by_provider),
            "by_failure_category": dict(by_failure_category),
            "by_provider_ticket_status": dict(by_provider_ticket_status),
            "by_error_code": dict(by_error_code),
            "by_triage_status": dict(by_triage_status),
            "hotspots": {
                "providers": _counter_rows(by_provider),
                "failure_categories": _counter_rows(by_failure_category),
                "provider_ticket_statuses": _counter_rows(by_provider_ticket_status),
                "error_codes": _counter_rows(by_error_code),
                "triage_statuses": _counter_rows(by_triage_status),
            },
            "triaged_jobs": int(triaged_jobs),
            "triage_rate": self._safe_ratio(int(triaged_jobs), total_failed_jobs),
            "replay_candidates_total": len(replay_candidates),
            "replay_candidates": replay_candidates[:normalized_top_n],
            "triage_actions": triage_actions,
        }

    def apply_breakage_helpdesk_failure_triage(
        self,
        *,
        triage_status: str,
        job_ids: Optional[List[str]] = None,
        window_days: int = 7,
        provider: Optional[str] = None,
        failure_category: Optional[str] = None,
        provider_ticket_status: Optional[str] = None,
        limit: int = 100,
        triage_owner: Optional[str] = None,
        root_cause: Optional[str] = None,
        resolution: Optional[str] = None,
        note: Optional[str] = None,
        tags: Optional[List[str]] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_status = self._normalize_triage_status(triage_status)
        normalized_limit = self._normalize_bulk_limit(limit)
        normalized_window = self._normalize_window_days(window_days)
        provider_filter = str(provider or "").strip().lower() or None
        category_filter = str(failure_category or "").strip().lower() or None
        provider_ticket_status_filter = (
            str(provider_ticket_status or "").strip().lower() or None
        )
        normalized_owner = str(triage_owner or "").strip() or None
        normalized_root_cause = str(root_cause or "").strip() or None
        normalized_resolution = str(resolution or "").strip() or None
        normalized_note = str(note or "").strip() or None
        normalized_tags: Optional[List[str]] = None
        if tags is not None:
            values = []
            seen = set()
            for raw in tags:
                value = str(raw or "").strip().lower()
                if not value or value in seen:
                    continue
                seen.add(value)
                values.append(value)
            normalized_tags = values

        now = _utcnow()
        normalized_job_ids: List[str] = []
        seen_job_ids = set()
        for raw in (job_ids or []):
            value = str(raw or "").strip()
            if not value or value in seen_job_ids:
                continue
            seen_job_ids.add(value)
            normalized_job_ids.append(value)

        target_jobs: List[ConversionJob] = []
        skipped_not_found: List[str] = []
        source = "job_ids"
        if normalized_job_ids:
            for job_id in normalized_job_ids[:normalized_limit]:
                job = self.session.get(ConversionJob, job_id)
                if (
                    not job
                    or str(job.task_type or "") != self._BREAKAGE_HELPDESK_TASK_TYPE
                ):
                    skipped_not_found.append(job_id)
                    continue
                target_jobs.append(job)
        else:
            source = "filters"
            since = self._window_since(normalized_window)
            raw_rows = self._collect_breakage_helpdesk_rows(
                since=since,
                provider_filter=provider_filter,
                provider_ticket_status_filter=provider_ticket_status_filter,
            )
            selected_ids: List[str] = []
            for row in raw_rows:
                if not bool(row.get("_is_failed")):
                    continue
                failure_category_value = str(row.get("failure_category") or "unknown")
                if category_filter and failure_category_value != category_filter:
                    continue
                row_id = str(row.get("id") or "").strip()
                if not row_id:
                    continue
                selected_ids.append(row_id)
                if len(selected_ids) >= normalized_limit:
                    break
            for job_id in selected_ids:
                job = self.session.get(ConversionJob, job_id)
                if job is not None:
                    target_jobs.append(job)

        updated_jobs: List[Dict[str, Any]] = []
        skipped_non_failed: List[str] = []
        for job in target_jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            helpdesk_sync = (
                payload.get("helpdesk_sync")
                if isinstance(payload.get("helpdesk_sync"), dict)
                else {}
            )
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            job_status = str(job.status or "").strip().lower()
            sync_status = (
                str(helpdesk_sync.get("sync_status") or result.get("sync_status") or "")
                .strip()
                .lower()
                or job_status
            )
            is_failed = (
                sync_status == JobStatus.FAILED.value
                or job_status == JobStatus.FAILED.value
            )
            if not is_failed:
                skipped_non_failed.append(str(job.id))
                continue
            triage_payload = (
                payload.get("triage") if isinstance(payload.get("triage"), dict) else {}
            )
            updated_triage = dict(triage_payload)
            updated_triage["status"] = normalized_status
            if triage_owner is not None:
                updated_triage["owner"] = normalized_owner
            if root_cause is not None:
                updated_triage["root_cause"] = normalized_root_cause
            if resolution is not None:
                updated_triage["resolution"] = normalized_resolution
            if note is not None:
                updated_triage["note"] = normalized_note
            if normalized_tags is not None:
                updated_triage["tags"] = normalized_tags
            updated_triage["updated_at"] = now.isoformat()
            updated_triage["updated_by_id"] = user_id

            history = (
                payload.get("triage_history")
                if isinstance(payload.get("triage_history"), list)
                else []
            )
            history_row = {
                "at": now.isoformat(),
                "by_user_id": user_id,
                "status": normalized_status,
                "owner": updated_triage.get("owner"),
                "root_cause": updated_triage.get("root_cause"),
                "resolution": updated_triage.get("resolution"),
                "note": updated_triage.get("note"),
                "tags": updated_triage.get("tags"),
            }
            updated_history = [row for row in history if isinstance(row, dict)]
            updated_history.append(history_row)
            if len(updated_history) > 50:
                updated_history = updated_history[-50:]

            updated_payload = dict(payload)
            updated_payload["triage"] = updated_triage
            updated_payload["triage_status"] = normalized_status
            updated_payload["triage_history"] = updated_history
            job.payload = updated_payload
            self.session.add(job)

            updated_jobs.append(
                {
                    "id": str(job.id),
                    "incident_id": payload.get("incident_id"),
                    "triage_status": normalized_status,
                    "triage_owner": updated_triage.get("owner"),
                    "triage_updated_at": updated_triage.get("updated_at"),
                }
            )
        if updated_jobs:
            self.session.flush()

        return {
            "source": source,
            "window_days": normalized_window,
            "filters": {
                "provider": provider_filter,
                "failure_category": category_filter,
                "provider_ticket_status": provider_ticket_status_filter,
            },
            "triage_update": {
                "triage_status": normalized_status,
                "triage_owner": normalized_owner if triage_owner is not None else None,
                "root_cause": normalized_root_cause if root_cause is not None else None,
                "resolution": normalized_resolution if resolution is not None else None,
                "note": normalized_note if note is not None else None,
                "tags": normalized_tags if normalized_tags is not None else None,
            },
            "requested": len(normalized_job_ids) if normalized_job_ids else normalized_limit,
            "updated_total": len(updated_jobs),
            "updated_jobs": updated_jobs,
            "skipped_not_found_total": len(skipped_not_found),
            "skipped_not_found_job_ids": skipped_not_found,
            "skipped_non_failed_total": len(skipped_non_failed),
            "skipped_non_failed_job_ids": skipped_non_failed,
            "updated_at": now.isoformat(),
        }

    def enqueue_breakage_helpdesk_failure_replay_jobs(
        self,
        *,
        job_ids: Optional[List[str]] = None,
        window_days: int = 7,
        provider: Optional[str] = None,
        failure_category: Optional[str] = None,
        provider_ticket_status: Optional[str] = None,
        limit: int = 100,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_limit = self._normalize_bulk_limit(limit)
        normalized_window = self._normalize_window_days(window_days)
        provider_filter = str(provider or "").strip().lower() or None
        category_filter = str(failure_category or "").strip().lower() or None
        provider_ticket_status_filter = (
            str(provider_ticket_status or "").strip().lower() or None
        )

        now = _utcnow()
        replay_batch_id = f"bh-replay-{_uuid()}"
        normalized_job_ids: List[str] = []
        seen_job_ids = set()
        for raw in (job_ids or []):
            value = str(raw or "").strip()
            if not value or value in seen_job_ids:
                continue
            seen_job_ids.add(value)
            normalized_job_ids.append(value)

        source = "job_ids"
        selected_job_ids: List[str] = []
        skipped_not_found: List[str] = []
        if normalized_job_ids:
            for job_id in normalized_job_ids[:normalized_limit]:
                job = self.session.get(ConversionJob, job_id)
                if (
                    not job
                    or str(job.task_type or "") != self._BREAKAGE_HELPDESK_TASK_TYPE
                ):
                    skipped_not_found.append(job_id)
                    continue
                selected_job_ids.append(str(job.id))
        else:
            source = "filters"
            since = self._window_since(normalized_window)
            raw_rows = self._collect_breakage_helpdesk_rows(
                since=since,
                provider_filter=provider_filter,
                provider_ticket_status_filter=provider_ticket_status_filter,
            )
            for row in raw_rows:
                if not bool(row.get("_is_failed")):
                    continue
                failure_category_value = str(row.get("failure_category") or "unknown")
                if category_filter and failure_category_value != category_filter:
                    continue
                row_id = str(row.get("id") or "").strip()
                if not row_id:
                    continue
                selected_job_ids.append(row_id)
                if len(selected_job_ids) >= normalized_limit:
                    break

        created_jobs: List[Dict[str, Any]] = []
        skipped_non_failed: List[str] = []
        skipped_missing_incident: List[str] = []
        enqueue_errors: List[Dict[str, str]] = []
        breakage_service = BreakageIncidentService(self.session)
        for idx, source_job_id in enumerate(selected_job_ids, start=1):
            source_job = self.session.get(ConversionJob, source_job_id)
            if (
                not source_job
                or str(source_job.task_type or "") != self._BREAKAGE_HELPDESK_TASK_TYPE
            ):
                skipped_not_found.append(source_job_id)
                continue
            source_payload = (
                source_job.payload if isinstance(source_job.payload, dict) else {}
            )
            source_helpdesk_sync = (
                source_payload.get("helpdesk_sync")
                if isinstance(source_payload.get("helpdesk_sync"), dict)
                else {}
            )
            source_result = (
                source_payload.get("result")
                if isinstance(source_payload.get("result"), dict)
                else {}
            )
            source_job_status = str(source_job.status or "").strip().lower()
            source_sync_status = (
                str(
                    source_helpdesk_sync.get("sync_status")
                    or source_result.get("sync_status")
                    or source_payload.get("sync_status")
                    or source_job_status
                )
                .strip()
                .lower()
            )
            is_failed = (
                source_sync_status == JobStatus.FAILED.value
                or source_job_status == JobStatus.FAILED.value
            )
            if not is_failed:
                skipped_non_failed.append(source_job_id)
                continue
            incident_id = str(source_payload.get("incident_id") or "").strip()
            if not incident_id:
                skipped_missing_incident.append(source_job_id)
                continue
            provider_value = (
                str(
                    source_helpdesk_sync.get("provider")
                    or source_payload.get("provider")
                    or "stub"
                )
                .strip()
                .lower()
                or "stub"
            )
            integration = (
                source_payload.get("integration")
                if isinstance(source_payload.get("integration"), dict)
                else {}
            )
            source_metadata = (
                source_payload.get("metadata")
                if isinstance(source_payload.get("metadata"), dict)
                else {}
            )
            replay_idempotency_key = (
                f"replay-{_stable_hash([source_job_id, now.isoformat(), str(idx)])[:24]}"
            )
            replay_metadata = dict(source_metadata)
            replay_metadata["replay"] = {
                "batch_id": replay_batch_id,
                "source_job_id": source_job_id,
                "requested_at": now.isoformat(),
                "requested_by_id": user_id,
                "requested_total": len(selected_job_ids),
                "replay_index": idx,
            }
            source_max_attempts = int(source_job.max_attempts or 0)
            retry_max_attempts = (
                source_max_attempts if 1 <= source_max_attempts <= 10 else None
            )
            try:
                replay_job = breakage_service.enqueue_helpdesk_stub_sync(
                    incident_id,
                    user_id=user_id,
                    metadata_json=replay_metadata,
                    provider=provider_value,
                    idempotency_key=replay_idempotency_key,
                    retry_max_attempts=retry_max_attempts,
                    integration_json=integration,
                )
            except Exception as exc:
                enqueue_errors.append(
                    {
                        "source_job_id": source_job_id,
                        "incident_id": incident_id,
                        "error": str(exc),
                    }
                )
                continue
            created_jobs.append(
                {
                    "batch_id": replay_batch_id,
                    "source_job_id": source_job_id,
                    "job_id": str(replay_job.id),
                    "incident_id": incident_id,
                    "provider": provider_value,
                    "idempotency_key": replay_idempotency_key,
                }
            )
        if created_jobs:
            self.session.flush()

        return {
            "source": source,
            "batch_id": replay_batch_id,
            "generated_at": now.isoformat(),
            "window_days": normalized_window,
            "filters": {
                "provider": provider_filter,
                "failure_category": category_filter,
                "provider_ticket_status": provider_ticket_status_filter,
            },
            "limit": normalized_limit,
            "requested_job_ids_total": len(normalized_job_ids),
            "selected_jobs_total": len(selected_job_ids),
            "created_jobs_total": len(created_jobs),
            "created_jobs": created_jobs,
            "skipped_not_found_total": len(skipped_not_found),
            "skipped_not_found_job_ids": skipped_not_found,
            "skipped_non_failed_total": len(skipped_non_failed),
            "skipped_non_failed_job_ids": skipped_non_failed,
            "skipped_missing_incident_total": len(skipped_missing_incident),
            "skipped_missing_incident_job_ids": skipped_missing_incident,
            "errors_total": len(enqueue_errors),
            "errors": enqueue_errors,
        }

    def get_breakage_helpdesk_failure_replay_batch(
        self,
        batch_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_batch_id = str(batch_id or "").strip()
        if not normalized_batch_id:
            raise ValueError("batch_id must not be empty")
        normalized_page = self._normalize_page(page)
        normalized_page_size = self._normalize_page_size(page_size)
        rows = self._collect_breakage_helpdesk_replay_rows(
            batch_id_filter=normalized_batch_id,
        )
        if not rows:
            raise ValueError(f"Replay batch not found: {normalized_batch_id}")
        by_job_status: Counter[str] = Counter(str(row.get("status") or "unknown") for row in rows)
        by_sync_status: Counter[str] = Counter(
            str(row.get("sync_status") or "unknown") for row in rows
        )
        by_provider: Counter[str] = Counter(str(row.get("provider") or "unknown") for row in rows)
        by_failure_category: Counter[str] = Counter(
            str(row.get("failure_category") or "unknown") for row in rows
        )
        requested_by_ids = sorted(
            {
                int(row.get("requested_by_id"))
                for row in rows
                if row.get("requested_by_id") is not None
            }
        )
        requested_totals = [
            int(row.get("requested_total"))
            for row in rows
            if row.get("requested_total") is not None and int(row.get("requested_total")) >= 0
        ]
        duration_values = [
            float(row.get("_duration_seconds"))
            for row in rows
            if row.get("_duration_seconds") is not None
        ]
        display_rows = [
            {key: value for key, value in row.items() if not key.startswith("_")}
            for row in rows
        ]
        paged = self._paginate(display_rows, page=normalized_page, page_size=normalized_page_size)
        return {
            "generated_at": _utcnow().isoformat(),
            "batch_id": normalized_batch_id,
            "total": int(paged["total"]),
            "pagination": {
                "page": int(paged["page"]),
                "page_size": int(paged["page_size"]),
                "pages": int(paged["pages"]),
                "total": int(paged["total"]),
            },
            "requested_total": (
                int(max(requested_totals))
                if requested_totals
                else int(paged["total"])
            ),
            "requested_by_ids": requested_by_ids,
            "by_job_status": dict(by_job_status),
            "by_sync_status": dict(by_sync_status),
            "by_provider": dict(by_provider),
            "by_failure_category": dict(by_failure_category),
            "duration_seconds": self._duration_stats(duration_values),
            "jobs": paged["rows"],
        }

    def list_breakage_helpdesk_failure_replay_batches(
        self,
        *,
        window_days: int = 7,
        provider: Optional[str] = None,
        job_status: Optional[str] = None,
        sync_status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        normalized_page = self._normalize_page(page)
        normalized_page_size = self._normalize_page_size(page_size)
        provider_filter = str(provider or "").strip().lower() or None
        job_status_filter = str(job_status or "").strip().lower() or None
        sync_status_filter = str(sync_status or "").strip().lower() or None
        if job_status_filter and job_status_filter not in {
            JobStatus.PENDING.value,
            JobStatus.PROCESSING.value,
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            "unknown",
        }:
            raise ValueError(
                "job_status must be one of: cancelled, completed, failed, pending, processing, unknown"
            )
        if sync_status_filter and sync_status_filter not in {
            JobStatus.PENDING.value,
            JobStatus.PROCESSING.value,
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            "queued",
            "unknown",
        }:
            raise ValueError(
                "sync_status must be one of: cancelled, completed, failed, pending, processing, queued, unknown"
            )
        since = self._window_since(normalized_window)
        rows = self._collect_breakage_helpdesk_replay_rows(
            since=since,
            provider_filter=provider_filter,
            job_status_filter=job_status_filter,
            sync_status_filter=sync_status_filter,
        )

        overall_by_job_status: Counter[str] = Counter(
            str(row.get("status") or "unknown") for row in rows
        )
        overall_by_sync_status: Counter[str] = Counter(
            str(row.get("sync_status") or "unknown") for row in rows
        )
        overall_by_provider: Counter[str] = Counter(
            str(row.get("provider") or "unknown") for row in rows
        )
        overall_by_failure_category: Counter[str] = Counter(
            str(row.get("failure_category") or "unknown") for row in rows
        )

        grouped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            batch_id = str(row.get("batch_id") or "").strip()
            if not batch_id:
                continue
            bucket = grouped.setdefault(
                batch_id,
                {
                    "batch_id": batch_id,
                    "rows": [],
                    "by_job_status": Counter(),
                    "by_sync_status": Counter(),
                    "by_provider": Counter(),
                    "by_failure_category": Counter(),
                    "requested_by_ids": set(),
                    "requested_totals": [],
                    "duration_values": [],
                    "first_created_at_dt": None,
                    "last_created_at_dt": None,
                    "first_requested_at_dt": None,
                    "last_requested_at_dt": None,
                },
            )
            bucket["rows"].append(row)
            job_status_value = str(row.get("status") or "unknown")
            sync_status_value = str(row.get("sync_status") or "unknown")
            provider_value = str(row.get("provider") or "unknown")
            failure_category_value = str(row.get("failure_category") or "unknown")
            bucket["by_job_status"][job_status_value] += 1
            bucket["by_sync_status"][sync_status_value] += 1
            bucket["by_provider"][provider_value] += 1
            bucket["by_failure_category"][failure_category_value] += 1
            requested_by_id = row.get("requested_by_id")
            if requested_by_id is not None:
                bucket["requested_by_ids"].add(int(requested_by_id))
            requested_total = row.get("requested_total")
            if requested_total is not None and int(requested_total) >= 0:
                bucket["requested_totals"].append(int(requested_total))
            duration_value = row.get("_duration_seconds")
            if duration_value is not None:
                bucket["duration_values"].append(float(duration_value))
            created_dt = row.get("_created_at_dt")
            requested_dt = row.get("_requested_at_dt")
            if created_dt is not None:
                if bucket["first_created_at_dt"] is None or created_dt < bucket["first_created_at_dt"]:
                    bucket["first_created_at_dt"] = created_dt
                if bucket["last_created_at_dt"] is None or created_dt > bucket["last_created_at_dt"]:
                    bucket["last_created_at_dt"] = created_dt
            if requested_dt is not None:
                if (
                    bucket["first_requested_at_dt"] is None
                    or requested_dt < bucket["first_requested_at_dt"]
                ):
                    bucket["first_requested_at_dt"] = requested_dt
                if (
                    bucket["last_requested_at_dt"] is None
                    or requested_dt > bucket["last_requested_at_dt"]
                ):
                    bucket["last_requested_at_dt"] = requested_dt

        summaries: List[Dict[str, Any]] = []
        for batch_id, bucket in grouped.items():
            by_job_status = bucket["by_job_status"]
            jobs_total = int(sum(by_job_status.values()))
            failed_jobs = int(by_job_status.get(JobStatus.FAILED.value, 0))
            completed_jobs = int(by_job_status.get(JobStatus.COMPLETED.value, 0))
            pending_jobs = int(by_job_status.get(JobStatus.PENDING.value, 0))
            processing_jobs = int(by_job_status.get(JobStatus.PROCESSING.value, 0))
            first_requested_at_dt = (
                bucket["first_requested_at_dt"] or bucket["first_created_at_dt"]
            )
            last_requested_at_dt = (
                bucket["last_requested_at_dt"] or bucket["last_created_at_dt"]
            )
            first_created_at_dt = bucket["first_created_at_dt"]
            last_created_at_dt = bucket["last_created_at_dt"]
            summaries.append(
                {
                    "batch_id": batch_id,
                    "jobs_total": jobs_total,
                    "requested_total": (
                        int(max(bucket["requested_totals"]))
                        if bucket["requested_totals"]
                        else jobs_total
                    ),
                    "requested_by_ids": sorted(bucket["requested_by_ids"]),
                    "by_job_status": dict(by_job_status),
                    "by_sync_status": dict(bucket["by_sync_status"]),
                    "by_provider": dict(bucket["by_provider"]),
                    "by_failure_category": dict(bucket["by_failure_category"]),
                    "failed_jobs": failed_jobs,
                    "completed_jobs": completed_jobs,
                    "pending_jobs": pending_jobs,
                    "processing_jobs": processing_jobs,
                    "failed_rate": self._safe_ratio(failed_jobs, jobs_total),
                    "duration_seconds": self._duration_stats(bucket["duration_values"]),
                    "first_requested_at": (
                        first_requested_at_dt.isoformat()
                        if first_requested_at_dt is not None
                        else None
                    ),
                    "last_requested_at": (
                        last_requested_at_dt.isoformat()
                        if last_requested_at_dt is not None
                        else None
                    ),
                    "first_created_at": (
                        first_created_at_dt.isoformat()
                        if first_created_at_dt is not None
                        else None
                    ),
                    "last_created_at": (
                        last_created_at_dt.isoformat()
                        if last_created_at_dt is not None
                        else None
                    ),
                    "_sort_dt": (
                        last_requested_at_dt
                        or last_created_at_dt
                        or first_requested_at_dt
                        or first_created_at_dt
                    ),
                }
            )

        summaries.sort(
            key=lambda row: (
                row.get("_sort_dt") is not None,
                row.get("_sort_dt"),
                row.get("batch_id"),
            ),
            reverse=True,
        )
        display_summaries = [
            {key: value for key, value in row.items() if key != "_sort_dt"}
            for row in summaries
        ]
        paged = self._paginate(
            display_summaries,
            page=normalized_page,
            page_size=normalized_page_size,
        )
        return {
            "generated_at": _utcnow().isoformat(),
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "filters": {
                "provider": provider_filter,
                "job_status": job_status_filter,
                "sync_status": sync_status_filter,
            },
            "total_batches": int(paged["total"]),
            "total_jobs": int(len(rows)),
            "pagination": {
                "page": int(paged["page"]),
                "page_size": int(paged["page_size"]),
                "pages": int(paged["pages"]),
                "total": int(paged["total"]),
            },
            "by_job_status": dict(overall_by_job_status),
            "by_sync_status": dict(overall_by_sync_status),
            "by_provider": dict(overall_by_provider),
            "by_failure_category": dict(overall_by_failure_category),
            "batches": paged["rows"],
        }

    def export_breakage_helpdesk_failure_replay_batch(
        self,
        batch_id: str,
        *,
        export_format: str = "json",
    ) -> Dict[str, Any]:
        normalized_batch_id = str(batch_id or "").strip()
        if not normalized_batch_id:
            raise ValueError("batch_id must not be empty")
        rows = self._collect_breakage_helpdesk_replay_rows(
            batch_id_filter=normalized_batch_id,
        )
        if not rows:
            raise ValueError(f"Replay batch not found: {normalized_batch_id}")

        by_job_status: Counter[str] = Counter(str(row.get("status") or "unknown") for row in rows)
        by_sync_status: Counter[str] = Counter(
            str(row.get("sync_status") or "unknown") for row in rows
        )
        by_provider: Counter[str] = Counter(str(row.get("provider") or "unknown") for row in rows)
        by_failure_category: Counter[str] = Counter(
            str(row.get("failure_category") or "unknown") for row in rows
        )
        requested_by_ids = sorted(
            {
                int(row.get("requested_by_id"))
                for row in rows
                if row.get("requested_by_id") is not None
            }
        )
        requested_totals = [
            int(row.get("requested_total"))
            for row in rows
            if row.get("requested_total") is not None and int(row.get("requested_total")) >= 0
        ]
        duration_values = [
            float(row.get("_duration_seconds"))
            for row in rows
            if row.get("_duration_seconds") is not None
        ]
        display_rows = [
            {key: value for key, value in row.items() if not key.startswith("_")}
            for row in rows
        ]
        payload = {
            "generated_at": _utcnow().isoformat(),
            "batch_id": normalized_batch_id,
            "total": len(display_rows),
            "requested_total": (
                int(max(requested_totals))
                if requested_totals
                else int(len(display_rows))
            ),
            "requested_by_ids": requested_by_ids,
            "by_job_status": dict(by_job_status),
            "by_sync_status": dict(by_sync_status),
            "by_provider": dict(by_provider),
            "by_failure_category": dict(by_failure_category),
            "duration_seconds": self._duration_stats(duration_values),
            "jobs": display_rows,
        }
        normalized = str(export_format or "json").strip().lower()
        if normalized == "json":
            return {
                "content": json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                "media_type": "application/json",
                "filename": f"parallel-ops-breakage-helpdesk-replay-batch-{normalized_batch_id}.json",
            }
        if normalized == "csv":
            csv_io = io.StringIO()
            writer = csv.DictWriter(
                csv_io,
                fieldnames=[
                    "batch_id",
                    "job_id",
                    "source_job_id",
                    "replay_index",
                    "incident_id",
                    "provider",
                    "sync_status",
                    "status",
                    "failure_category",
                    "idempotency_key",
                    "requested_at",
                    "requested_by_id",
                    "requested_total",
                    "archived",
                    "archived_at",
                    "attempt_count",
                    "max_attempts",
                    "last_error",
                    "duration_seconds",
                    "created_at",
                    "started_at",
                    "completed_at",
                ],
            )
            writer.writeheader()
            for row in display_rows:
                writer.writerow(row)
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": f"parallel-ops-breakage-helpdesk-replay-batch-{normalized_batch_id}.csv",
            }
        if normalized == "md":
            lines = [
                "# Parallel Ops Breakage Helpdesk Replay Batch",
                "",
                f"- generated_at: {payload.get('generated_at') or ''}",
                f"- batch_id: {normalized_batch_id}",
                f"- total: {payload.get('total') or 0}",
                f"- requested_total: {payload.get('requested_total') or 0}",
                f"- requested_by_ids: {','.join(str(v) for v in requested_by_ids)}",
                "",
                "| Replay Job ID | Source Job ID | Provider | Sync Status | Status | Failure Category | Requested At |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for row in display_rows:
                lines.append(
                    "| "
                    f"{row.get('job_id') or ''} | "
                    f"{row.get('source_job_id') or ''} | "
                    f"{row.get('provider') or ''} | "
                    f"{row.get('sync_status') or ''} | "
                    f"{row.get('status') or ''} | "
                    f"{row.get('failure_category') or ''} | "
                    f"{row.get('requested_at') or ''} |"
                )
            return {
                "content": ("\n".join(lines) + "\n").encode("utf-8"),
                "media_type": "text/markdown",
                "filename": f"parallel-ops-breakage-helpdesk-replay-batch-{normalized_batch_id}.md",
            }
        raise ValueError("export_format must be json, csv or md")

    def breakage_helpdesk_replay_trends(
        self,
        *,
        window_days: int = 7,
        bucket_days: int = 1,
        provider: Optional[str] = None,
        job_status: Optional[str] = None,
        sync_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        normalized_bucket = self._normalize_bucket_days(bucket_days)
        if normalized_bucket > normalized_window:
            raise ValueError("bucket_days must be <= window_days")

        provider_filter = str(provider or "").strip().lower() or None
        job_status_filter = str(job_status or "").strip().lower() or None
        sync_status_filter = str(sync_status or "").strip().lower() or None
        if job_status_filter and job_status_filter not in {
            JobStatus.PENDING.value,
            JobStatus.PROCESSING.value,
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            "unknown",
        }:
            raise ValueError(
                "job_status must be one of: cancelled, completed, failed, pending, processing, unknown"
            )
        if sync_status_filter and sync_status_filter not in {
            JobStatus.PENDING.value,
            JobStatus.PROCESSING.value,
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            "queued",
            "unknown",
        }:
            raise ValueError(
                "sync_status must be one of: cancelled, completed, failed, pending, processing, queued, unknown"
            )

        since = self._window_since(normalized_window)
        now = _utcnow()
        bucket_span = timedelta(days=normalized_bucket)
        bucket_seconds = float(bucket_span.total_seconds())

        points: List[Dict[str, Any]] = []
        cursor = since
        while cursor < now:
            bucket_end = min(cursor + bucket_span, now)
            points.append(
                {
                    "bucket_start": cursor.isoformat(),
                    "bucket_end": bucket_end.isoformat(),
                    "total_jobs": 0,
                    "failed_jobs": 0,
                    "batches_total": 0,
                    "_batch_ids": set(),
                    "_by_job_status": Counter(),
                    "_by_sync_status": Counter(),
                    "_by_provider": Counter(),
                }
            )
            cursor = bucket_end
        if not points:
            points.append(
                {
                    "bucket_start": since.isoformat(),
                    "bucket_end": now.isoformat(),
                    "total_jobs": 0,
                    "failed_jobs": 0,
                    "batches_total": 0,
                    "_batch_ids": set(),
                    "_by_job_status": Counter(),
                    "_by_sync_status": Counter(),
                    "_by_provider": Counter(),
                }
            )

        def bucket_index(created_at: Optional[datetime]) -> Optional[int]:
            if created_at is None:
                return None
            delta = (created_at - since).total_seconds()
            if delta < 0:
                return None
            idx = int(delta // bucket_seconds) if bucket_seconds > 0 else 0
            if idx < 0:
                return None
            if idx >= len(points):
                return len(points) - 1
            return idx

        rows = self._collect_breakage_helpdesk_replay_rows(
            since=since,
            provider_filter=provider_filter,
            job_status_filter=job_status_filter,
            sync_status_filter=sync_status_filter,
        )
        by_provider: Counter[str] = Counter()
        by_job_status: Counter[str] = Counter()
        by_sync_status: Counter[str] = Counter()
        all_batch_ids: set[str] = set()
        for row in rows:
            idx = bucket_index(row.get("_created_at_dt"))
            if idx is None:
                continue
            status_value = str(row.get("status") or "unknown")
            sync_status_value = str(row.get("sync_status") or "unknown")
            provider_value = str(row.get("provider") or "unknown")
            batch_id = str(row.get("batch_id") or "").strip()
            point = points[idx]
            point["total_jobs"] += 1
            point["_by_job_status"][status_value] += 1
            point["_by_sync_status"][sync_status_value] += 1
            point["_by_provider"][provider_value] += 1
            if batch_id:
                point["_batch_ids"].add(batch_id)
                all_batch_ids.add(batch_id)
            if status_value == JobStatus.FAILED.value or sync_status_value == JobStatus.FAILED.value:
                point["failed_jobs"] += 1

            by_provider[provider_value] += 1
            by_job_status[status_value] += 1
            by_sync_status[sync_status_value] += 1

        for point in points:
            point["batches_total"] = len(point.pop("_batch_ids", set()))
            point["by_job_status"] = dict(point.pop("_by_job_status", Counter()))
            point["by_sync_status"] = dict(point.pop("_by_sync_status", Counter()))
            point["by_provider"] = dict(point.pop("_by_provider", Counter()))
            point["failed_rate"] = self._safe_ratio(
                int(point.get("failed_jobs") or 0),
                int(point.get("total_jobs") or 0),
            )

        total_jobs = int(sum(int(point.get("total_jobs") or 0) for point in points))
        failed_jobs = int(sum(int(point.get("failed_jobs") or 0) for point in points))
        return {
            "generated_at": now.isoformat(),
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "bucket_days": normalized_bucket,
            "filters": {
                "provider": provider_filter,
                "job_status": job_status_filter,
                "sync_status": sync_status_filter,
            },
            "points": points,
            "aggregates": {
                "total_jobs": total_jobs,
                "failed_jobs": failed_jobs,
                "failed_rate": self._safe_ratio(failed_jobs, total_jobs),
                "total_batches": int(len(all_batch_ids)),
            },
            "by_provider": dict(by_provider),
            "by_job_status": dict(by_job_status),
            "by_sync_status": dict(by_sync_status),
        }

    def _breakage_helpdesk_replay_trend_export_rows(
        self,
        trends: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for row in trends.get("points") or []:
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    "bucket_start": row.get("bucket_start"),
                    "bucket_end": row.get("bucket_end"),
                    "total_jobs": row.get("total_jobs"),
                    "failed_jobs": row.get("failed_jobs"),
                    "failed_rate": row.get("failed_rate"),
                    "batches_total": row.get("batches_total"),
                }
            )
        return rows

    def export_breakage_helpdesk_replay_trends(
        self,
        *,
        window_days: int = 7,
        bucket_days: int = 1,
        provider: Optional[str] = None,
        job_status: Optional[str] = None,
        sync_status: Optional[str] = None,
        export_format: str = "json",
    ) -> Dict[str, Any]:
        trends = self.breakage_helpdesk_replay_trends(
            window_days=window_days,
            bucket_days=bucket_days,
            provider=provider,
            job_status=job_status,
            sync_status=sync_status,
        )
        normalized = str(export_format or "json").strip().lower()
        if normalized == "json":
            content = json.dumps(trends, ensure_ascii=False, indent=2).encode("utf-8")
            return {
                "content": content,
                "media_type": "application/json",
                "filename": "parallel-ops-breakage-helpdesk-replay-trends.json",
            }

        rows = self._breakage_helpdesk_replay_trend_export_rows(trends)
        if normalized == "csv":
            csv_io = io.StringIO()
            writer = csv.DictWriter(
                csv_io,
                fieldnames=[
                    "bucket_start",
                    "bucket_end",
                    "total_jobs",
                    "failed_jobs",
                    "failed_rate",
                    "batches_total",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": "parallel-ops-breakage-helpdesk-replay-trends.csv",
            }

        if normalized == "md":
            lines = [
                "# Parallel Ops Breakage Helpdesk Replay Trends",
                "",
                f"- generated_at: {trends.get('generated_at') or ''}",
                f"- window_days: {trends.get('window_days') or ''}",
                f"- bucket_days: {trends.get('bucket_days') or ''}",
                f"- window_since: {trends.get('window_since') or ''}",
                "",
                "| Bucket Start | Bucket End | Total Jobs | Failed Jobs | Failed Rate | Batches Total |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
            for row in rows:
                lines.append(
                    "| "
                    f"{row.get('bucket_start') or ''} | "
                    f"{row.get('bucket_end') or ''} | "
                    f"{row.get('total_jobs') or 0} | "
                    f"{row.get('failed_jobs') or 0} | "
                    f"{row.get('failed_rate') if row.get('failed_rate') is not None else ''} | "
                    f"{row.get('batches_total') or 0} |"
                )
            return {
                "content": ("\n".join(lines) + "\n").encode("utf-8"),
                "media_type": "text/markdown",
                "filename": "parallel-ops-breakage-helpdesk-replay-trends.md",
            }
        raise ValueError("export_format must be json, csv or md")

    def cleanup_breakage_helpdesk_failure_replay_batches(
        self,
        *,
        ttl_hours: int = 168,
        limit: int = 200,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        normalized_ttl_hours = self._normalize_cleanup_ttl_hours(ttl_hours)
        normalized_limit = self._normalize_cleanup_limit(limit)
        now = _utcnow()
        cutoff = now - timedelta(hours=normalized_ttl_hours)

        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type == self._BREAKAGE_HELPDESK_TASK_TYPE)
            .filter(ConversionJob.created_at.isnot(None))
            .filter(ConversionJob.created_at <= cutoff)
            .order_by(ConversionJob.created_at.asc(), ConversionJob.id.asc())
            .all()
        )

        terminal_statuses = {
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        }
        archived_jobs = 0
        scanned_jobs = 0
        skipped_non_terminal_total = 0
        skipped_already_archived_total = 0
        batch_ids: set[str] = set()
        job_ids: List[str] = []

        for job in jobs:
            if archived_jobs >= normalized_limit:
                break
            scanned_jobs += 1
            payload = job.payload if isinstance(job.payload, dict) else {}
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            replay = metadata.get("replay") if isinstance(metadata.get("replay"), dict) else {}
            batch_id = str(replay.get("batch_id") or "").strip()
            if not batch_id:
                continue
            if bool(replay.get("archived")):
                skipped_already_archived_total += 1
                continue
            helpdesk_sync = (
                payload.get("helpdesk_sync")
                if isinstance(payload.get("helpdesk_sync"), dict)
                else {}
            )
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            job_status = str(job.status or "").strip().lower()
            sync_status = (
                str(
                    helpdesk_sync.get("sync_status")
                    or result.get("sync_status")
                    or payload.get("sync_status")
                    or job_status
                )
                .strip()
                .lower()
            )
            if job_status not in terminal_statuses or sync_status not in terminal_statuses:
                skipped_non_terminal_total += 1
                continue
            if not dry_run:
                replay["archived"] = True
                replay["archived_at"] = now.isoformat()
                replay["archive_reason"] = "ttl_expired"
                replay["archive_ttl_hours"] = normalized_ttl_hours
                metadata["replay"] = replay
                payload["metadata"] = metadata
                job.payload = payload
                self.session.add(job)

            archived_jobs += 1
            batch_ids.add(batch_id)
            job_ids.append(str(job.id))

        if archived_jobs > 0 and not dry_run:
            self.session.flush()
        return {
            "generated_at": now.isoformat(),
            "ttl_hours": normalized_ttl_hours,
            "limit": normalized_limit,
            "dry_run": bool(dry_run),
            "cutoff_at": cutoff.isoformat(),
            "archived_jobs": int(archived_jobs),
            "archived_batches": int(len(batch_ids)),
            "batch_ids": sorted(batch_ids),
            "job_ids": job_ids,
            "scanned_jobs": int(scanned_jobs),
            "skipped_non_terminal_total": int(skipped_non_terminal_total),
            "skipped_already_archived_total": int(skipped_already_archived_total),
        }

    def export_breakage_helpdesk_failures(
        self,
        *,
        window_days: int = 7,
        provider: Optional[str] = None,
        failure_category: Optional[str] = None,
        provider_ticket_status: Optional[str] = None,
        export_format: str = "json",
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        since = self._window_since(normalized_window)
        provider_filter = str(provider or "").strip().lower() or None
        category_filter = str(failure_category or "").strip().lower() or None
        provider_ticket_status_filter = (
            str(provider_ticket_status or "").strip().lower() or None
        )

        raw_rows = self._collect_breakage_helpdesk_rows(
            since=since,
            provider_filter=provider_filter,
            provider_ticket_status_filter=provider_ticket_status_filter,
        )
        rows: List[Dict[str, Any]] = []
        by_provider: Counter[str] = Counter()
        by_failure_category: Counter[str] = Counter()
        by_provider_ticket_status: Counter[str] = Counter()
        for raw_row in raw_rows:
            if not bool(raw_row.get("_is_failed")):
                continue
            failure_category_value = str(raw_row.get("failure_category") or "unknown")
            if category_filter and failure_category_value != category_filter:
                continue
            provider_value = str(raw_row.get("provider") or "unknown")
            provider_ticket_status_value = (
                str(raw_row.get("provider_ticket_status") or "").strip().lower() or "none"
            )
            by_provider[provider_value] += 1
            by_failure_category[failure_category_value] += 1
            by_provider_ticket_status[provider_ticket_status_value] += 1
            rows.append(
                {
                    key: value
                    for key, value in raw_row.items()
                    if key not in {"_is_failed", "_created_at_dt"}
                }
            )

        payload = {
            "generated_at": _utcnow().isoformat(),
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "provider_filter": provider_filter,
            "failure_category_filter": category_filter,
            "provider_ticket_status_filter": provider_ticket_status_filter,
            "total": len(rows),
            "by_provider": dict(by_provider),
            "by_failure_category": dict(by_failure_category),
            "by_provider_ticket_status": dict(by_provider_ticket_status),
            "jobs": rows,
        }
        normalized = str(export_format or "json").strip().lower()
        if normalized == "json":
            return {
                "content": json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                "media_type": "application/json",
                "filename": "parallel-ops-breakage-helpdesk-failures.json",
            }
        if normalized == "csv":
            csv_io = io.StringIO()
            writer = csv.DictWriter(
                csv_io,
                fieldnames=[
                    "id",
                    "incident_id",
                    "provider",
                    "sync_status",
                    "failure_category",
                    "error_code",
                    "error_message",
                    "provider_ticket_status",
                    "triage_status",
                    "triage_owner",
                    "triage_root_cause",
                    "triage_resolution",
                    "triage_note",
                    "triage_updated_at",
                    "triage_updated_by_id",
                    "external_ticket_id",
                    "task_type",
                    "status",
                    "attempt_count",
                    "max_attempts",
                    "last_error",
                    "dedupe_key",
                    "created_at",
                    "started_at",
                    "completed_at",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": "parallel-ops-breakage-helpdesk-failures.csv",
            }
        md_content = None
        if normalized == "md":
            lines = [
                "# Parallel Ops Breakage Helpdesk Failures",
                "",
                f"- generated_at: {payload.get('generated_at') or ''}",
                f"- window_days: {payload.get('window_days') or ''}",
                f"- window_since: {payload.get('window_since') or ''}",
                f"- provider_filter: {provider_filter or ''}",
                f"- failure_category_filter: {category_filter or ''}",
                f"- provider_ticket_status_filter: {provider_ticket_status_filter or ''}",
                f"- total: {len(rows)}",
                "",
                "| Job ID | Incident ID | Provider | Failure Category | Error Code | Status | Created At |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for row in rows:
                lines.append(
                    "| "
                    f"{row.get('id') or ''} | "
                    f"{row.get('incident_id') or ''} | "
                    f"{row.get('provider') or ''} | "
                    f"{row.get('failure_category') or ''} | "
                    f"{row.get('error_code') or ''} | "
                    f"{row.get('status') or ''} | "
                    f"{row.get('created_at') or ''} |"
                )
            md_content = ("\n".join(lines) + "\n").encode("utf-8")
            return {
                "content": md_content,
                "media_type": "text/markdown",
                "filename": "parallel-ops-breakage-helpdesk-failures.md",
            }
        if normalized == "zip":
            csv_export = self.export_breakage_helpdesk_failures(
                window_days=window_days,
                provider=provider,
                failure_category=failure_category,
                provider_ticket_status=provider_ticket_status,
                export_format="csv",
            )
            if md_content is None:
                md_export = self.export_breakage_helpdesk_failures(
                    window_days=window_days,
                    provider=provider,
                    failure_category=failure_category,
                    provider_ticket_status=provider_ticket_status,
                    export_format="md",
                )
                md_content = md_export["content"]
            zip_io = io.BytesIO()
            with ZipFile(zip_io, mode="w", compression=ZIP_DEFLATED) as zf:
                zf.writestr(
                    "failures.json",
                    json.dumps(payload, ensure_ascii=False, indent=2),
                )
                zf.writestr(
                    "failures.csv",
                    (csv_export.get("content") or b"").decode("utf-8"),
                )
                zf.writestr(
                    "summary.md",
                    (md_content or b"").decode("utf-8"),
                )
            return {
                "content": zip_io.getvalue(),
                "media_type": "application/zip",
                "filename": "parallel-ops-breakage-helpdesk-failures.zip",
            }
        raise ValueError("export_format must be json, csv, md or zip")

    def _build_breakage_helpdesk_failures_export_job_dedupe_key(
        self,
        *,
        window_days: int,
        provider: Optional[str],
        failure_category: Optional[str],
        provider_ticket_status: Optional[str],
        export_format: str,
    ) -> str:
        token = _stable_hash(
            [
                str(window_days),
                str(provider or ""),
                str(failure_category or ""),
                str(provider_ticket_status or ""),
                str(export_format or ""),
            ]
        )
        return f"parallel-ops-breakage-helpdesk-failures-export:{token}"

    def _get_breakage_helpdesk_failures_export_job(self, job_id: str) -> ConversionJob:
        job = self.session.get(ConversionJob, job_id)
        if (
            not job
            or str(job.task_type or "") != self._BREAKAGE_HELPDESK_FAILURE_EXPORT_TASK_TYPE
        ):
            raise ValueError(f"Parallel ops breakage-helpdesk export job not found: {job_id}")
        return job

    def _build_breakage_helpdesk_failures_export_job_view(
        self, job: ConversionJob
    ) -> Dict[str, Any]:
        payload = job.payload if isinstance(job.payload, dict) else {}
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        export_info = payload.get("export") if isinstance(payload.get("export"), dict) else {}
        export_result = (
            payload.get("export_result")
            if isinstance(payload.get("export_result"), dict)
            else {}
        )
        size_bytes = export_result.get("size_bytes")
        try:
            result_size = int(size_bytes) if size_bytes is not None else None
        except Exception:
            result_size = None
        attempt_count = int(job.attempt_count or 0)
        max_attempts = int(job.max_attempts or 0)
        return {
            "job_id": job.id,
            "task_type": job.task_type,
            "status": job.status,
            "sync_status": (
                str(export_info.get("sync_status") or "").strip().lower()
                or str(job.status or "").strip().lower()
            ),
            "filters": filters,
            "export_format": payload.get("export_format"),
            "filename": export_result.get("filename"),
            "media_type": export_result.get("media_type"),
            "result_size_bytes": result_size,
            "download_ready": bool(
                str(job.status or "") == JobStatus.COMPLETED.value
                and export_result.get("content_b64")
            ),
            "last_error": job.last_error,
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "retry_budget": {
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
                "remaining_attempts": max(max_attempts - attempt_count, 0),
            },
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    def enqueue_breakage_helpdesk_failures_export_job(
        self,
        *,
        window_days: int = 7,
        provider: Optional[str] = None,
        failure_category: Optional[str] = None,
        provider_ticket_status: Optional[str] = None,
        export_format: str = "json",
        execute_immediately: bool = False,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        normalized_provider = str(provider or "").strip().lower() or None
        normalized_failure_category = str(failure_category or "").strip().lower() or None
        normalized_provider_ticket_status = (
            str(provider_ticket_status or "").strip().lower() or None
        )
        normalized_format = self._normalize_breakage_helpdesk_export_format(export_format)
        payload = {
            "filters": {
                "window_days": normalized_window,
                "provider": normalized_provider,
                "failure_category": normalized_failure_category,
                "provider_ticket_status": normalized_provider_ticket_status,
            },
            "export_format": normalized_format,
            "export": {
                "sync_status": "queued",
                "created_at": _utcnow().isoformat(),
                "created_by_id": user_id,
            },
        }
        dedupe_key = self._build_breakage_helpdesk_failures_export_job_dedupe_key(
            window_days=normalized_window,
            provider=normalized_provider,
            failure_category=normalized_failure_category,
            provider_ticket_status=normalized_provider_ticket_status,
            export_format=normalized_format,
        )
        job = self._job_service.create_job(
            task_type=self._BREAKAGE_HELPDESK_FAILURE_EXPORT_TASK_TYPE,
            payload=payload,
            user_id=user_id,
            dedupe=True,
            dedupe_key=dedupe_key,
        )
        if execute_immediately:
            self.execute_breakage_helpdesk_failures_export_job(job.id, user_id=user_id)
            refreshed = self.session.get(ConversionJob, job.id)
            if refreshed:
                job = refreshed
        return self._build_breakage_helpdesk_failures_export_job_view(job)

    def execute_breakage_helpdesk_failures_export_job(
        self,
        job_id: str,
        *,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        job = self._get_breakage_helpdesk_failures_export_job(job_id)
        payload = job.payload if isinstance(job.payload, dict) else {}
        export_result = (
            payload.get("export_result")
            if isinstance(payload.get("export_result"), dict)
            else {}
        )
        if (
            str(job.status or "") == JobStatus.COMPLETED.value
            and export_result.get("content_b64")
        ):
            return self._build_breakage_helpdesk_failures_export_job_view(job)

        now = _utcnow()
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        normalized_window = self._normalize_window_days(int(filters.get("window_days") or 7))
        normalized_provider = str(filters.get("provider") or "").strip().lower() or None
        normalized_failure_category = (
            str(filters.get("failure_category") or "").strip().lower() or None
        )
        normalized_provider_ticket_status = (
            str(filters.get("provider_ticket_status") or "").strip().lower() or None
        )
        normalized_format = self._normalize_breakage_helpdesk_export_format(
            str(payload.get("export_format") or "json")
        )

        job.status = JobStatus.PROCESSING.value
        job.started_at = now
        job.attempt_count = int(job.attempt_count or 0) + 1
        self.session.add(job)
        self.session.flush()

        try:
            exported = self.export_breakage_helpdesk_failures(
                window_days=normalized_window,
                provider=normalized_provider,
                failure_category=normalized_failure_category,
                provider_ticket_status=normalized_provider_ticket_status,
                export_format=normalized_format,
            )
            content_bytes = bytes(exported.get("content") or b"")
            encoded_content = base64.b64encode(content_bytes).decode("ascii")
            updated_payload = dict(payload)
            updated_payload["filters"] = {
                "window_days": normalized_window,
                "provider": normalized_provider,
                "failure_category": normalized_failure_category,
                "provider_ticket_status": normalized_provider_ticket_status,
            }
            updated_payload["export"] = {
                "sync_status": "completed",
                "updated_at": now.isoformat(),
                "updated_by_id": user_id,
            }
            updated_payload["export_result"] = {
                "filename": exported.get("filename"),
                "media_type": exported.get("media_type"),
                "size_bytes": len(content_bytes),
                "content_sha1": hashlib.sha1(content_bytes).hexdigest(),
                "content_b64": encoded_content,
            }
            updated_payload["result"] = {
                "filename": exported.get("filename"),
                "media_type": exported.get("media_type"),
                "size_bytes": len(content_bytes),
                "updated_at": now.isoformat(),
            }
            job.payload = updated_payload
            job.status = JobStatus.COMPLETED.value
            job.completed_at = now
            job.last_error = None
            self.session.add(job)
            self.session.flush()
            return self._build_breakage_helpdesk_failures_export_job_view(job)
        except Exception as exc:
            updated_payload = dict(payload)
            updated_payload["export"] = {
                "sync_status": "failed",
                "error_message": str(exc),
                "updated_at": now.isoformat(),
                "updated_by_id": user_id,
            }
            job.payload = updated_payload
            job.status = JobStatus.FAILED.value
            job.completed_at = now
            job.last_error = str(exc)
            self.session.add(job)
            self.session.flush()
            raise ValueError(str(exc))

    def get_breakage_helpdesk_failures_export_job(self, job_id: str) -> Dict[str, Any]:
        job = self._get_breakage_helpdesk_failures_export_job(job_id)
        return self._build_breakage_helpdesk_failures_export_job_view(job)

    def run_breakage_helpdesk_failures_export_job(
        self,
        job_id: str,
        *,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self.execute_breakage_helpdesk_failures_export_job(job_id, user_id=user_id)

    def download_breakage_helpdesk_failures_export_job(self, job_id: str) -> Dict[str, Any]:
        job = self._get_breakage_helpdesk_failures_export_job(job_id)
        if str(job.status or "") != JobStatus.COMPLETED.value:
            raise ValueError(f"Export job is not completed yet: {job_id}")
        payload = job.payload if isinstance(job.payload, dict) else {}
        result = payload.get("export_result")
        if not isinstance(result, dict):
            raise ValueError(f"Export result payload missing for job: {job_id}")
        encoded = result.get("content_b64")
        if not encoded:
            raise ValueError(f"Export content missing for job: {job_id}")
        try:
            decoded = base64.b64decode(str(encoded).encode("ascii"))
        except Exception as exc:
            raise ValueError(f"Export content decode failed for job: {job_id}") from exc
        return {
            "content": decoded,
            "media_type": result.get("media_type") or "application/octet-stream",
            "filename": result.get("filename") or "parallel-ops-breakage-helpdesk-failures.bin",
        }

    def cleanup_expired_breakage_helpdesk_failures_export_results(
        self,
        *,
        ttl_hours: int = 24,
        limit: int = 200,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_ttl_hours = self._normalize_cleanup_ttl_hours(ttl_hours)
        normalized_limit = self._normalize_cleanup_limit(limit)
        now = _utcnow()
        cutoff = now - timedelta(hours=normalized_ttl_hours)
        jobs = (
            self.session.query(ConversionJob)
            .filter(ConversionJob.task_type == self._BREAKAGE_HELPDESK_FAILURE_EXPORT_TASK_TYPE)
            .filter(ConversionJob.status == JobStatus.COMPLETED.value)
            .filter(ConversionJob.completed_at.isnot(None))
            .filter(ConversionJob.completed_at <= cutoff)
            .order_by(ConversionJob.completed_at.asc())
            .limit(normalized_limit)
            .all()
        )
        expired_jobs = 0
        skipped_jobs = 0
        touched_job_ids: List[str] = []
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            result = (
                payload.get("export_result")
                if isinstance(payload.get("export_result"), dict)
                else {}
            )
            if not result.get("content_b64"):
                skipped_jobs += 1
                continue
            updated_payload = dict(payload)
            updated_result = dict(result)
            updated_result.pop("content_b64", None)
            updated_result["content_expired_at"] = now.isoformat()
            updated_result["content_ttl_hours"] = normalized_ttl_hours
            updated_payload["export_result"] = updated_result
            export_info = (
                updated_payload.get("export")
                if isinstance(updated_payload.get("export"), dict)
                else {}
            )
            export_info = dict(export_info)
            export_info["sync_status"] = "expired"
            export_info["content_expired_at"] = now.isoformat()
            export_info["content_ttl_hours"] = normalized_ttl_hours
            export_info["updated_by_id"] = user_id
            updated_payload["export"] = export_info
            job.payload = updated_payload
            self.session.add(job)
            expired_jobs += 1
            touched_job_ids.append(str(job.id))
        if expired_jobs:
            self.session.flush()
        return {
            "ttl_hours": normalized_ttl_hours,
            "limit": normalized_limit,
            "expired_jobs": expired_jobs,
            "skipped_jobs": skipped_jobs,
            "job_ids": touched_job_ids,
        }

    def breakage_helpdesk_failures_export_jobs_overview(
        self,
        *,
        window_days: int = 7,
        provider: Optional[str] = None,
        failure_category: Optional[str] = None,
        export_format: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_window = self._normalize_window_days(window_days)
        normalized_page = self._normalize_page(page)
        normalized_page_size = self._normalize_page_size(page_size)
        provider_filter = str(provider or "").strip().lower() or None
        failure_category_filter = str(failure_category or "").strip().lower() or None
        export_format_filter: Optional[str] = None
        if export_format is not None:
            export_format_filter = self._normalize_breakage_helpdesk_export_format(
                export_format
            )
        since = self._window_since(normalized_window)
        jobs = (
            self.session.query(ConversionJob)
            .filter(
                ConversionJob.task_type
                == self._BREAKAGE_HELPDESK_FAILURE_EXPORT_TASK_TYPE
            )
            .filter(ConversionJob.created_at >= since)
            .order_by(ConversionJob.created_at.desc())
            .all()
        )

        by_job_status: Counter[str] = Counter()
        by_sync_status: Counter[str] = Counter()
        by_provider: Counter[str] = Counter()
        by_failure_category: Counter[str] = Counter()
        by_export_format: Counter[str] = Counter()
        duration_seconds: List[float] = []
        rows: List[Dict[str, Any]] = []
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
            export_info = payload.get("export") if isinstance(payload.get("export"), dict) else {}
            provider_value = (
                str(filters.get("provider") or payload.get("provider") or "all")
                .strip()
                .lower()
                or "all"
            )
            export_format_value = (
                str(payload.get("export_format") or "json").strip().lower() or "json"
            )
            failure_category_value = (
                str(filters.get("failure_category") or payload.get("failure_category") or "all")
                .strip()
                .lower()
                or "all"
            )
            if provider_filter and provider_value != provider_filter:
                continue
            if failure_category_filter and failure_category_value != failure_category_filter:
                continue
            if export_format_filter and export_format_value != export_format_filter:
                continue
            job_status_value = str(job.status or "unknown").strip().lower() or "unknown"
            sync_status_value = (
                str(export_info.get("sync_status") or job_status_value).strip().lower()
                or job_status_value
            )
            by_job_status[job_status_value] += 1
            by_sync_status[sync_status_value] += 1
            by_provider[provider_value] += 1
            by_failure_category[failure_category_value] += 1
            by_export_format[export_format_value] += 1
            row_duration_seconds: Optional[float] = None
            if job.started_at and job.completed_at and job.completed_at >= job.started_at:
                row_duration_seconds = float(
                    (job.completed_at - job.started_at).total_seconds()
                )
                duration_seconds.append(row_duration_seconds)
            rows.append(
                {
                    **self._build_breakage_helpdesk_failures_export_job_view(job),
                    "provider": provider_value,
                    "failure_category": failure_category_value,
                    "export_format": export_format_value,
                    "sync_status": sync_status_value,
                    "duration_seconds": (
                        round(row_duration_seconds, 4)
                        if row_duration_seconds is not None
                        else None
                    ),
                }
            )

        paged = self._paginate(rows, page=normalized_page, page_size=normalized_page_size)
        return {
            "generated_at": _utcnow().isoformat(),
            "window_days": normalized_window,
            "window_since": since.isoformat(),
            "filters": {
                "provider": provider_filter,
                "failure_category": failure_category_filter,
                "export_format": export_format_filter,
            },
            "total": int(paged["total"]),
            "pagination": {
                "page": int(paged["page"]),
                "page_size": int(paged["page_size"]),
                "pages": int(paged["pages"]),
                "total": int(paged["total"]),
            },
            "by_job_status": dict(by_job_status),
            "by_sync_status": dict(by_sync_status),
            "by_provider": dict(by_provider),
            "by_failure_category": dict(by_failure_category),
            "by_export_format": dict(by_export_format),
            "duration_seconds": self._duration_stats(duration_seconds),
            "jobs": paged["rows"],
        }

    def _prometheus_escape(self, value: Any) -> str:
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace('"', '\\"')
        )

    def _prometheus_line(
        self,
        name: str,
        value: Optional[float],
        *,
        labels: Optional[Dict[str, Any]] = None,
    ) -> str:
        metric_value = float(value) if value is not None else 0.0
        label_rows = []
        for key, raw in sorted((labels or {}).items()):
            if raw is None:
                continue
            label_rows.append(f'{key}="{self._prometheus_escape(raw)}"')
        if label_rows:
            return f"{name}{{{','.join(label_rows)}}} {metric_value}"
        return f"{name} {metric_value}"

    def _breakage_design_loopback_metrics(self) -> Dict[str, Any]:
        """Tier-B #3 §3.7 (taskbook ``c2e404f``). Point-in-time,
        full-table SQL aggregate over ``BreakageIncident.eco_id``
        — the §3.2 durable substrate, **not** an in-memory
        ``event_bus`` counter (which would lose on restart /
        multi-process). Live-link 口径 (§3.B): a row counts iff
        ``eco_id IN (SELECT id FROM meta_ecos)`` — SQL NULL
        semantics make this also exclude ``eco_id IS NULL``, and
        the IN-subquery shape matches §3.2's CAS predicate so a
        dangling ``eco_id`` (the linked ECO was hard-deleted; no
        FK cascade) is correctly excluded.

        Called **only from** ``prometheus_metrics`` (§3.A
        resolution (b), Medium 1) — ``summary()`` is NOT touched
        and ``/parallel-ops/summary[/export]`` JSON stays
        byte-identical.

        Three ``func.count`` + ``GROUP BY`` round-trips; no
        ``query(BreakageIncident).all()`` (pinned by the §5 AST
        guard ``test_no_load_all_uses_sql_aggregate``).
        """

        live_link = BreakageIncident.eco_id.in_(sa_select(ECO.id))
        links_total = (
            self.session.query(sa_func.count(BreakageIncident.id))
            .filter(live_link)
            .scalar()
            or 0
        )
        by_status_rows = (
            self.session.query(
                BreakageIncident.status,
                sa_func.count(BreakageIncident.id),
            )
            .filter(live_link)
            .group_by(BreakageIncident.status)
            .all()
        )
        by_severity_rows = (
            self.session.query(
                BreakageIncident.severity,
                sa_func.count(BreakageIncident.id),
            )
            .filter(live_link)
            .group_by(BreakageIncident.severity)
            .all()
        )
        return {
            "links_total": int(links_total),
            "by_status": {
                str(status or "unknown"): int(count)
                for status, count in by_status_rows
            },
            "by_severity": {
                str(severity or "unknown"): int(count)
                for severity, count in by_severity_rows
            },
        }

    def prometheus_metrics(
        self,
        *,
        window_days: int = 7,
        site_id: Optional[str] = None,
        target_object: Optional[str] = None,
        template_key: Optional[str] = None,
        overlay_cache_hit_rate_warn: Optional[float] = None,
        overlay_cache_min_requests_warn: Optional[int] = None,
        doc_sync_dead_letter_rate_warn: Optional[float] = None,
        workflow_failed_rate_warn: Optional[float] = None,
        breakage_open_rate_warn: Optional[float] = None,
        breakage_helpdesk_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_triage_coverage_warn: Optional[float] = None,
        breakage_helpdesk_export_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_provider_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_provider_failed_min_jobs_warn: Optional[int] = None,
        breakage_helpdesk_provider_failed_rate_critical: Optional[float] = None,
        breakage_helpdesk_provider_failed_min_jobs_critical: Optional[int] = None,
        breakage_helpdesk_replay_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_replay_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_replay_pending_total_warn: Optional[int] = None,
        doc_sync_checkout_gate_block_on_dead_letter_only: Optional[bool] = None,
        doc_sync_checkout_gate_max_pending_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_processing_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_failed_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_dead_letter_warn: Optional[int] = None,
        doc_sync_dead_letter_trend_delta_warn: Optional[int] = None,
    ) -> str:
        summary = self.summary(
            window_days=window_days,
            site_id=site_id,
            target_object=target_object,
            template_key=template_key,
            overlay_cache_hit_rate_warn=overlay_cache_hit_rate_warn,
            overlay_cache_min_requests_warn=overlay_cache_min_requests_warn,
            doc_sync_dead_letter_rate_warn=doc_sync_dead_letter_rate_warn,
            workflow_failed_rate_warn=workflow_failed_rate_warn,
            breakage_open_rate_warn=breakage_open_rate_warn,
            breakage_helpdesk_failed_rate_warn=breakage_helpdesk_failed_rate_warn,
            breakage_helpdesk_failed_total_warn=breakage_helpdesk_failed_total_warn,
            breakage_helpdesk_triage_coverage_warn=breakage_helpdesk_triage_coverage_warn,
            breakage_helpdesk_export_failed_total_warn=breakage_helpdesk_export_failed_total_warn,
            breakage_helpdesk_provider_failed_rate_warn=breakage_helpdesk_provider_failed_rate_warn,
            breakage_helpdesk_provider_failed_min_jobs_warn=breakage_helpdesk_provider_failed_min_jobs_warn,
            breakage_helpdesk_provider_failed_rate_critical=breakage_helpdesk_provider_failed_rate_critical,
            breakage_helpdesk_provider_failed_min_jobs_critical=breakage_helpdesk_provider_failed_min_jobs_critical,
            breakage_helpdesk_replay_failed_rate_warn=breakage_helpdesk_replay_failed_rate_warn,
            breakage_helpdesk_replay_failed_total_warn=breakage_helpdesk_replay_failed_total_warn,
            breakage_helpdesk_replay_pending_total_warn=breakage_helpdesk_replay_pending_total_warn,
            doc_sync_checkout_gate_block_on_dead_letter_only=doc_sync_checkout_gate_block_on_dead_letter_only,
            doc_sync_checkout_gate_max_pending_warn=doc_sync_checkout_gate_max_pending_warn,
            doc_sync_checkout_gate_max_processing_warn=doc_sync_checkout_gate_max_processing_warn,
            doc_sync_checkout_gate_max_failed_warn=doc_sync_checkout_gate_max_failed_warn,
            doc_sync_checkout_gate_max_dead_letter_warn=doc_sync_checkout_gate_max_dead_letter_warn,
            doc_sync_dead_letter_trend_delta_warn=doc_sync_dead_letter_trend_delta_warn,
        )
        failure_trends = self.breakage_helpdesk_failure_trends(
            window_days=window_days,
            bucket_days=1,
        )
        common_labels = {
            "window_days": int(summary.get("window_days") or 0),
            "site_id": site_id,
            "target_object": target_object,
            "template_key": template_key,
        }

        lines: List[str] = [
            "# HELP yuantus_parallel_doc_sync_jobs_total Document sync jobs in selected window.",
            "# TYPE yuantus_parallel_doc_sync_jobs_total gauge",
            self._prometheus_line(
                "yuantus_parallel_doc_sync_jobs_total",
                summary.get("doc_sync", {}).get("total"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_doc_sync_dead_letter_total Doc sync dead-letter jobs in selected window.",
            "# TYPE yuantus_parallel_doc_sync_dead_letter_total gauge",
            self._prometheus_line(
                "yuantus_parallel_doc_sync_dead_letter_total",
                summary.get("doc_sync", {}).get("dead_letter_total"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_doc_sync_success_rate Doc sync success rate in selected window.",
            "# TYPE yuantus_parallel_doc_sync_success_rate gauge",
            self._prometheus_line(
                "yuantus_parallel_doc_sync_success_rate",
                summary.get("doc_sync", {}).get("success_rate"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_doc_sync_checkout_gate_threshold_hits_total Number of exceeded checkout-gate thresholds.",
            "# TYPE yuantus_parallel_doc_sync_checkout_gate_threshold_hits_total gauge",
            self._prometheus_line(
                "yuantus_parallel_doc_sync_checkout_gate_threshold_hits_total",
                summary.get("doc_sync", {})
                .get("checkout_gate", {})
                .get("threshold_hits_total"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_doc_sync_checkout_gate_blocking Checkout-gate is blocking (1) or not (0).",
            "# TYPE yuantus_parallel_doc_sync_checkout_gate_blocking gauge",
            self._prometheus_line(
                "yuantus_parallel_doc_sync_checkout_gate_blocking",
                1
                if bool(
                    summary.get("doc_sync", {})
                    .get("checkout_gate", {})
                    .get("is_blocking")
                )
                else 0,
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_doc_sync_dead_letter_trend_delta Delta between max and min dead-letter totals in trend buckets.",
            "# TYPE yuantus_parallel_doc_sync_dead_letter_trend_delta gauge",
            self._prometheus_line(
                "yuantus_parallel_doc_sync_dead_letter_trend_delta",
                summary.get("doc_sync", {})
                .get("dead_letter_trend", {})
                .get("aggregates", {})
                .get("delta_dead_letter_total"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_workflow_runs_total Workflow custom action runs in selected window.",
            "# TYPE yuantus_parallel_workflow_runs_total gauge",
            self._prometheus_line(
                "yuantus_parallel_workflow_runs_total",
                summary.get("workflow_actions", {}).get("total"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_workflow_failed_rate Workflow custom action failed rate in selected window.",
            "# TYPE yuantus_parallel_workflow_failed_rate gauge",
            self._prometheus_line(
                "yuantus_parallel_workflow_failed_rate",
                summary.get("workflow_actions", {}).get("failed_rate"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_total Breakage incidents in selected window.",
            "# TYPE yuantus_parallel_breakage_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_total",
                summary.get("breakages", {}).get("total"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_open_total Open breakage incidents in selected window.",
            "# TYPE yuantus_parallel_breakage_open_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_open_total",
                summary.get("breakages", {}).get("open_total"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_jobs_total Breakage-helpdesk sync jobs in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_jobs_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_jobs_total",
                summary.get("breakages", {}).get("helpdesk", {}).get("total_jobs"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_failed_total Failed breakage-helpdesk sync jobs in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_failed_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_failed_total",
                summary.get("breakages", {}).get("helpdesk", {}).get("failed_jobs"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_failed_rate Failed breakage-helpdesk sync job ratio in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_failed_rate gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_failed_rate",
                summary.get("breakages", {}).get("helpdesk", {}).get("failed_rate"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_triaged_total Triaged failed breakage-helpdesk jobs in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_triaged_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_triaged_total",
                summary.get("breakages", {}).get("helpdesk", {}).get("triaged_jobs"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_triage_rate Triage coverage ratio among failed breakage-helpdesk jobs in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_triage_rate gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_triage_rate",
                summary.get("breakages", {}).get("helpdesk", {}).get("triage_rate"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_replay_jobs_total Replay breakage-helpdesk jobs in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_replay_jobs_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_replay_jobs_total",
                summary.get("breakages", {}).get("helpdesk", {}).get("replay_jobs_total"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_replay_batches_total Replay breakage-helpdesk batches in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_replay_batches_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_replay_batches_total",
                summary.get("breakages", {}).get("helpdesk", {}).get("replay_batches_total"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_replay_failed_total Failed replay breakage-helpdesk jobs in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_replay_failed_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_replay_failed_total",
                summary.get("breakages", {}).get("helpdesk", {}).get("replay_failed_jobs"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_replay_failed_rate Failed replay breakage-helpdesk ratio in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_replay_failed_rate gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_replay_failed_rate",
                summary.get("breakages", {}).get("helpdesk", {}).get("replay_failed_rate"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_replay_pending_total Pending replay breakage-helpdesk jobs in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_replay_pending_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_replay_pending_total",
                summary.get("breakages", {}).get("helpdesk", {}).get("replay_pending_jobs"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_external_ticket_total Breakage-helpdesk jobs with external ticket id.",
            "# TYPE yuantus_parallel_breakage_helpdesk_external_ticket_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_external_ticket_total",
                summary.get("breakages", {}).get("helpdesk", {}).get("with_external_ticket"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_export_jobs_total Breakage-helpdesk export jobs in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_export_jobs_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_export_jobs_total",
                summary.get("breakages", {}).get("helpdesk_export", {}).get("total_jobs"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_export_failed_total Failed breakage-helpdesk export jobs in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_export_failed_total gauge",
            self._prometheus_line(
                "yuantus_parallel_breakage_helpdesk_export_failed_total",
                summary.get("breakages", {}).get("helpdesk_export", {}).get("failed_jobs"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_breakage_helpdesk_provider_failed_total Failed breakage-helpdesk jobs by provider in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_provider_failed_total gauge",
            "# HELP yuantus_parallel_breakage_helpdesk_provider_failed_rate Failed breakage-helpdesk rate by provider in selected window.",
            "# TYPE yuantus_parallel_breakage_helpdesk_provider_failed_rate gauge",
            "# HELP yuantus_parallel_consumption_template_versions_total Consumption template versions tracked.",
            "# TYPE yuantus_parallel_consumption_template_versions_total gauge",
            self._prometheus_line(
                "yuantus_parallel_consumption_template_versions_total",
                summary.get("consumption_templates", {}).get("versions_total"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_overlay_cache_requests Overlay cache requests in current process.",
            "# TYPE yuantus_parallel_overlay_cache_requests gauge",
            self._prometheus_line(
                "yuantus_parallel_overlay_cache_requests",
                summary.get("overlay_cache", {}).get("requests"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_overlay_cache_hit_rate Overlay cache hit rate in current process.",
            "# TYPE yuantus_parallel_overlay_cache_hit_rate gauge",
            self._prometheus_line(
                "yuantus_parallel_overlay_cache_hit_rate",
                summary.get("overlay_cache", {}).get("hit_rate"),
                labels=common_labels,
            ),
            "# HELP yuantus_parallel_slo_hints_total Number of active SLO warning hints.",
            "# TYPE yuantus_parallel_slo_hints_total gauge",
            self._prometheus_line(
                "yuantus_parallel_slo_hints_total",
                len(summary.get("slo_hints") or []),
                labels=common_labels,
            ),
        ]

        lines.extend(
            [
                "# HELP yuantus_parallel_doc_sync_by_status Doc-sync job counts by status.",
                "# TYPE yuantus_parallel_doc_sync_by_status gauge",
                "# HELP yuantus_parallel_doc_sync_by_direction Doc-sync job counts by transfer direction.",
                "# TYPE yuantus_parallel_doc_sync_by_direction gauge",
            ]
        )

        by_status = summary.get("doc_sync", {}).get("by_status") or {}
        for status, count in sorted(by_status.items()):
            labels = {**common_labels, "status": status}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_doc_sync_by_status",
                    count,
                    labels=labels,
                )
            )
        by_direction = summary.get("doc_sync", {}).get("by_direction") or {}
        for direction, count in sorted(by_direction.items()):
            labels = {**common_labels, "direction": direction}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_doc_sync_by_direction",
                    count,
                    labels=labels,
                )
            )
        lines.extend(
            [
                "# HELP yuantus_parallel_doc_sync_checkout_gate_threshold_hit_count Observed status count for exceeded checkout-gate threshold.",
                "# TYPE yuantus_parallel_doc_sync_checkout_gate_threshold_hit_count gauge",
                "# HELP yuantus_parallel_doc_sync_checkout_gate_threshold_value Configured checkout-gate threshold for exceeded status.",
                "# TYPE yuantus_parallel_doc_sync_checkout_gate_threshold_value gauge",
                "# HELP yuantus_parallel_doc_sync_checkout_gate_threshold_exceeded_by Difference between observed count and threshold.",
                "# TYPE yuantus_parallel_doc_sync_checkout_gate_threshold_exceeded_by gauge",
            ]
        )
        gate_threshold_hits = (
            summary.get("doc_sync", {}).get("checkout_gate", {}).get("threshold_hits") or []
        )
        for row in gate_threshold_hits:
            if not isinstance(row, dict):
                continue
            labels = {**common_labels, "status": row.get("status")}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_doc_sync_checkout_gate_threshold_hit_count",
                    row.get("count"),
                    labels=labels,
                )
            )
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_doc_sync_checkout_gate_threshold_value",
                    row.get("threshold"),
                    labels=labels,
                )
            )
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_doc_sync_checkout_gate_threshold_exceeded_by",
                    row.get("exceeded_by"),
                    labels=labels,
                )
            )

        by_result_code = summary.get("workflow_actions", {}).get("by_result_code") or {}
        for result_code, count in sorted(by_result_code.items()):
            labels = {**common_labels, "result_code": result_code}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_workflow_by_result_code",
                    count,
                    labels=labels,
                )
            )

        by_provider = summary.get("breakages", {}).get("helpdesk", {}).get("by_provider") or {}
        for provider, count in sorted(by_provider.items()):
            labels = {**common_labels, "provider": provider}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_helpdesk_by_provider",
                    count,
                    labels=labels,
                )
            )
        replay_by_provider = (
            summary.get("breakages", {}).get("helpdesk", {}).get("replay_by_provider") or {}
        )
        for provider, count in sorted(replay_by_provider.items()):
            labels = {**common_labels, "provider": provider}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_helpdesk_replay_by_provider",
                    count,
                    labels=labels,
                )
            )
        by_provider_failed = (
            summary.get("breakages", {}).get("helpdesk", {}).get("by_provider_failed") or {}
        )
        for provider, count in sorted(by_provider_failed.items()):
            labels = {**common_labels, "provider": provider}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_helpdesk_provider_failed_total",
                    count,
                    labels=labels,
                )
            )
        provider_failed_rates = (
            summary.get("breakages", {}).get("helpdesk", {}).get("provider_failed_rates")
            or {}
        )
        for provider, provider_row in sorted(provider_failed_rates.items()):
            if not isinstance(provider_row, dict):
                continue
            labels = {**common_labels, "provider": provider}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_helpdesk_provider_failed_rate",
                    provider_row.get("failed_rate"),
                    labels=labels,
                )
            )

        by_provider_ticket_status = (
            summary.get("breakages", {})
            .get("helpdesk", {})
            .get("by_provider_ticket_status")
            or {}
        )
        for provider_ticket_status, count in sorted(by_provider_ticket_status.items()):
            labels = {**common_labels, "provider_ticket_status": provider_ticket_status}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_helpdesk_by_provider_ticket_status",
                    count,
                    labels=labels,
                )
            )
        by_triage_status = (
            summary.get("breakages", {}).get("helpdesk", {}).get("by_triage_status") or {}
        )
        for triage_status, count in sorted(by_triage_status.items()):
            labels = {**common_labels, "triage_status": triage_status}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_helpdesk_by_triage_status",
                    count,
                    labels=labels,
                )
            )
        helpdesk_export_by_status = (
            summary.get("breakages", {}).get("helpdesk_export", {}).get("by_job_status") or {}
        )
        for job_status, count in sorted(helpdesk_export_by_status.items()):
            labels = {**common_labels, "status": job_status}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_helpdesk_export_by_status",
                    count,
                    labels=labels,
                )
            )
        for failure_category, count in sorted(
            (failure_trends.get("by_failure_category") or {}).items()
        ):
            labels = {**common_labels, "failure_category": failure_category}
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_helpdesk_failed_by_failure_category",
                    count,
                    labels=labels,
                )
            )
        for point in failure_trends.get("points") or []:
            if not isinstance(point, dict):
                continue
            labels = {
                **common_labels,
                "bucket_start": point.get("bucket_start"),
                "bucket_end": point.get("bucket_end"),
            }
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_helpdesk_failure_trend_failed_total",
                    point.get("failed_jobs"),
                    labels=labels,
                )
            )
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_helpdesk_failure_trend_total_jobs",
                    point.get("total_jobs"),
                    labels=labels,
                )
            )

        # Tier-B #3 §3.7 design-loopback link gauges (taskbook
        # ``c2e404f``). Point-in-time, full-table SQL aggregate;
        # NO ``common_labels`` (§3.D Medium 2) — emitting a
        # full-table value under ``window_days`` / filter labels
        # would be a lie. ``*_links_total`` has no labels at all;
        # ``*_by_status`` only ``{status}``; ``*_by_severity``
        # only ``{severity}``.
        loopback_metrics = self._breakage_design_loopback_metrics()
        lines.extend(
            [
                "# HELP yuantus_parallel_breakage_design_loopback_links_total Breakage incidents with a live design-loopback ECO link.",
                "# TYPE yuantus_parallel_breakage_design_loopback_links_total gauge",
                self._prometheus_line(
                    "yuantus_parallel_breakage_design_loopback_links_total",
                    loopback_metrics["links_total"],
                ),
                "# HELP yuantus_parallel_breakage_design_loopback_links_by_status Breakage incidents with a live design-loopback ECO link, grouped by incident status.",
                "# TYPE yuantus_parallel_breakage_design_loopback_links_by_status gauge",
            ]
        )
        for status_value, count in sorted(loopback_metrics["by_status"].items()):
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_design_loopback_links_by_status",
                    count,
                    labels={"status": status_value},
                )
            )
        lines.extend(
            [
                "# HELP yuantus_parallel_breakage_design_loopback_links_by_severity Breakage incidents with a live design-loopback ECO link, grouped by incident severity.",
                "# TYPE yuantus_parallel_breakage_design_loopback_links_by_severity gauge",
            ]
        )
        for severity_value, count in sorted(loopback_metrics["by_severity"].items()):
            lines.append(
                self._prometheus_line(
                    "yuantus_parallel_breakage_design_loopback_links_by_severity",
                    count,
                    labels={"severity": severity_value},
                )
            )

        return "\n".join(lines) + "\n"

    def alerts(
        self,
        *,
        window_days: int = 7,
        site_id: Optional[str] = None,
        target_object: Optional[str] = None,
        template_key: Optional[str] = None,
        level: Optional[str] = None,
        overlay_cache_hit_rate_warn: Optional[float] = None,
        overlay_cache_min_requests_warn: Optional[int] = None,
        doc_sync_dead_letter_rate_warn: Optional[float] = None,
        workflow_failed_rate_warn: Optional[float] = None,
        breakage_open_rate_warn: Optional[float] = None,
        breakage_helpdesk_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_triage_coverage_warn: Optional[float] = None,
        breakage_helpdesk_export_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_provider_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_provider_failed_min_jobs_warn: Optional[int] = None,
        breakage_helpdesk_provider_failed_rate_critical: Optional[float] = None,
        breakage_helpdesk_provider_failed_min_jobs_critical: Optional[int] = None,
        breakage_helpdesk_replay_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_replay_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_replay_pending_total_warn: Optional[int] = None,
        doc_sync_checkout_gate_block_on_dead_letter_only: Optional[bool] = None,
        doc_sync_checkout_gate_max_pending_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_processing_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_failed_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_dead_letter_warn: Optional[int] = None,
        doc_sync_dead_letter_trend_delta_warn: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_level = str(level or "").strip().lower() or None
        if normalized_level and normalized_level not in {"warn", "critical", "info"}:
            raise ValueError("level must be one of: warn, critical, info")

        summary = self.summary(
            window_days=window_days,
            site_id=site_id,
            target_object=target_object,
            template_key=template_key,
            overlay_cache_hit_rate_warn=overlay_cache_hit_rate_warn,
            overlay_cache_min_requests_warn=overlay_cache_min_requests_warn,
            doc_sync_dead_letter_rate_warn=doc_sync_dead_letter_rate_warn,
            workflow_failed_rate_warn=workflow_failed_rate_warn,
            breakage_open_rate_warn=breakage_open_rate_warn,
            breakage_helpdesk_failed_rate_warn=breakage_helpdesk_failed_rate_warn,
            breakage_helpdesk_failed_total_warn=breakage_helpdesk_failed_total_warn,
            breakage_helpdesk_triage_coverage_warn=breakage_helpdesk_triage_coverage_warn,
            breakage_helpdesk_export_failed_total_warn=breakage_helpdesk_export_failed_total_warn,
            breakage_helpdesk_provider_failed_rate_warn=breakage_helpdesk_provider_failed_rate_warn,
            breakage_helpdesk_provider_failed_min_jobs_warn=breakage_helpdesk_provider_failed_min_jobs_warn,
            breakage_helpdesk_provider_failed_rate_critical=breakage_helpdesk_provider_failed_rate_critical,
            breakage_helpdesk_provider_failed_min_jobs_critical=breakage_helpdesk_provider_failed_min_jobs_critical,
            breakage_helpdesk_replay_failed_rate_warn=breakage_helpdesk_replay_failed_rate_warn,
            breakage_helpdesk_replay_failed_total_warn=breakage_helpdesk_replay_failed_total_warn,
            breakage_helpdesk_replay_pending_total_warn=breakage_helpdesk_replay_pending_total_warn,
            doc_sync_checkout_gate_block_on_dead_letter_only=doc_sync_checkout_gate_block_on_dead_letter_only,
            doc_sync_checkout_gate_max_pending_warn=doc_sync_checkout_gate_max_pending_warn,
            doc_sync_checkout_gate_max_processing_warn=doc_sync_checkout_gate_max_processing_warn,
            doc_sync_checkout_gate_max_failed_warn=doc_sync_checkout_gate_max_failed_warn,
            doc_sync_checkout_gate_max_dead_letter_warn=doc_sync_checkout_gate_max_dead_letter_warn,
            doc_sync_dead_letter_trend_delta_warn=doc_sync_dead_letter_trend_delta_warn,
        )
        hints = summary.get("slo_hints") or []
        if normalized_level:
            hints = [
                row
                for row in hints
                if str((row or {}).get("level") or "").strip().lower() == normalized_level
            ]

        code_counter = Counter(str((row or {}).get("code") or "unknown") for row in hints)
        return {
            "generated_at": summary.get("generated_at"),
            "window_days": summary.get("window_days"),
            "window_since": summary.get("window_since"),
            "filters": {
                "site_id": site_id,
                "target_object": target_object,
                "template_key": template_key,
                "level": normalized_level,
            },
            "slo_thresholds": summary.get("slo_thresholds") or {},
            "status": "warning" if hints else "ok",
            "total": len(hints),
            "by_code": dict(code_counter),
            "hints": hints,
        }

    def _summary_export_rows(self, summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        def push(metric: str, value: Any) -> None:
            rows.append({"metric": metric, "value": value})

        push("window_days", summary.get("window_days"))
        push("doc_sync.total", (summary.get("doc_sync") or {}).get("total"))
        push(
            "doc_sync.dead_letter_total",
            (summary.get("doc_sync") or {}).get("dead_letter_total"),
        )
        push(
            "doc_sync.success_rate",
            (summary.get("doc_sync") or {}).get("success_rate"),
        )
        push(
            "doc_sync.checkout_gate.threshold_hits_total",
            (summary.get("doc_sync") or {})
            .get("checkout_gate", {})
            .get("threshold_hits_total"),
        )
        push(
            "doc_sync.checkout_gate.is_blocking",
            1
            if bool(
                (summary.get("doc_sync") or {})
                .get("checkout_gate", {})
                .get("is_blocking")
            )
            else 0,
        )
        push(
            "doc_sync.dead_letter_trend.delta_dead_letter_total",
            (summary.get("doc_sync") or {})
            .get("dead_letter_trend", {})
            .get("aggregates", {})
            .get("delta_dead_letter_total"),
        )
        push(
            "workflow_actions.total",
            (summary.get("workflow_actions") or {}).get("total"),
        )
        push(
            "workflow_actions.failed_rate",
            (summary.get("workflow_actions") or {}).get("failed_rate"),
        )
        push("breakages.total", (summary.get("breakages") or {}).get("total"))
        push("breakages.open_total", (summary.get("breakages") or {}).get("open_total"))
        push(
            "breakages.helpdesk.total_jobs",
            (summary.get("breakages") or {}).get("helpdesk", {}).get("total_jobs"),
        )
        push(
            "breakages.helpdesk.failed_jobs",
            (summary.get("breakages") or {}).get("helpdesk", {}).get("failed_jobs"),
        )
        push(
            "breakages.helpdesk.failed_rate",
            (summary.get("breakages") or {}).get("helpdesk", {}).get("failed_rate"),
        )
        push(
            "breakages.helpdesk.triaged_jobs",
            (summary.get("breakages") or {}).get("helpdesk", {}).get("triaged_jobs"),
        )
        push(
            "breakages.helpdesk.triage_rate",
            (summary.get("breakages") or {}).get("helpdesk", {}).get("triage_rate"),
        )
        push(
            "breakages.helpdesk.replay_jobs_total",
            (summary.get("breakages") or {}).get("helpdesk", {}).get("replay_jobs_total"),
        )
        push(
            "breakages.helpdesk.replay_batches_total",
            (summary.get("breakages") or {}).get("helpdesk", {}).get("replay_batches_total"),
        )
        push(
            "breakages.helpdesk.replay_failed_jobs",
            (summary.get("breakages") or {}).get("helpdesk", {}).get("replay_failed_jobs"),
        )
        push(
            "breakages.helpdesk.replay_failed_rate",
            (summary.get("breakages") or {}).get("helpdesk", {}).get("replay_failed_rate"),
        )
        push(
            "breakages.helpdesk.replay_pending_jobs",
            (summary.get("breakages") or {}).get("helpdesk", {}).get("replay_pending_jobs"),
        )
        push(
            "breakages.helpdesk.with_external_ticket",
            (summary.get("breakages") or {})
            .get("helpdesk", {})
            .get("with_external_ticket"),
        )
        push(
            "breakages.helpdesk.with_provider_ticket_status",
            (summary.get("breakages") or {})
            .get("helpdesk", {})
            .get("with_provider_ticket_status"),
        )
        push(
            "breakages.helpdesk_export.total_jobs",
            (summary.get("breakages") or {}).get("helpdesk_export", {}).get("total_jobs"),
        )
        push(
            "breakages.helpdesk_export.failed_jobs",
            (summary.get("breakages") or {}).get("helpdesk_export", {}).get("failed_jobs"),
        )
        push(
            "breakages.helpdesk_export.expired_jobs",
            (summary.get("breakages") or {}).get("helpdesk_export", {}).get("expired_jobs"),
        )
        push(
            "consumption_templates.versions_total",
            (summary.get("consumption_templates") or {}).get("versions_total"),
        )
        push(
            "overlay_cache.requests",
            (summary.get("overlay_cache") or {}).get("requests"),
        )
        push(
            "overlay_cache.hit_rate",
            (summary.get("overlay_cache") or {}).get("hit_rate"),
        )
        push("slo_hints.total", len(summary.get("slo_hints") or []))

        for status, count in sorted(((summary.get("doc_sync") or {}).get("by_status") or {}).items()):
            push(f"doc_sync.by_status.{status}", count)
        for direction, count in sorted(
            ((summary.get("doc_sync") or {}).get("by_direction") or {}).items()
        ):
            push(f"doc_sync.by_direction.{direction}", count)
        for row in (
            (summary.get("doc_sync") or {})
            .get("checkout_gate", {})
            .get("threshold_hits")
            or []
        ):
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "unknown")
            push(
                f"doc_sync.checkout_gate.threshold_hit.{status}.count",
                row.get("count"),
            )
            push(
                f"doc_sync.checkout_gate.threshold_hit.{status}.threshold",
                row.get("threshold"),
            )
            push(
                f"doc_sync.checkout_gate.threshold_hit.{status}.exceeded_by",
                row.get("exceeded_by"),
            )
        for code, count in sorted(
            ((summary.get("workflow_actions") or {}).get("by_result_code") or {}).items()
        ):
            push(f"workflow_actions.by_result_code.{code}", count)
        for provider, count in sorted(
            ((summary.get("breakages") or {}).get("helpdesk", {}).get("by_provider") or {}).items()
        ):
            push(f"breakages.helpdesk.by_provider.{provider}", count)
        for provider, count in sorted(
            (
                (summary.get("breakages") or {})
                .get("helpdesk", {})
                .get("replay_by_provider")
                or {}
            ).items()
        ):
            push(f"breakages.helpdesk.replay_by_provider.{provider}", count)
        for sync_status, count in sorted(
            (
                (summary.get("breakages") or {})
                .get("helpdesk", {})
                .get("replay_by_sync_status")
                or {}
            ).items()
        ):
            push(f"breakages.helpdesk.replay_by_sync_status.{sync_status}", count)
        for provider, count in sorted(
            (
                (summary.get("breakages") or {})
                .get("helpdesk", {})
                .get("by_provider_failed")
                or {}
            ).items()
        ):
            push(f"breakages.helpdesk.by_provider_failed.{provider}", count)
        for provider, provider_row in sorted(
            (
                (summary.get("breakages") or {})
                .get("helpdesk", {})
                .get("provider_failed_rates")
                or {}
            ).items()
        ):
            if not isinstance(provider_row, dict):
                continue
            push(
                f"breakages.helpdesk.provider_failed_rate.{provider}",
                provider_row.get("failed_rate"),
            )
        for provider_ticket_status, count in sorted(
            (
                (summary.get("breakages") or {})
                .get("helpdesk", {})
                .get("by_provider_ticket_status")
                or {}
            ).items()
        ):
            push(
                f"breakages.helpdesk.by_provider_ticket_status.{provider_ticket_status}",
                count,
            )
        for triage_status, count in sorted(
            ((summary.get("breakages") or {}).get("helpdesk", {}).get("by_triage_status") or {}).items()
        ):
            push(f"breakages.helpdesk.by_triage_status.{triage_status}", count)
        for job_status, count in sorted(
            ((summary.get("breakages") or {}).get("helpdesk_export", {}).get("by_job_status") or {}).items()
        ):
            push(f"breakages.helpdesk_export.by_job_status.{job_status}", count)
        return rows

    def export_summary(
        self,
        *,
        window_days: int = 7,
        site_id: Optional[str] = None,
        target_object: Optional[str] = None,
        template_key: Optional[str] = None,
        export_format: str = "json",
        report_lang: Optional[str] = None,
        report_type: Optional[str] = None,
        locale_profile_id: Optional[str] = None,
        overlay_cache_hit_rate_warn: Optional[float] = None,
        overlay_cache_min_requests_warn: Optional[int] = None,
        doc_sync_dead_letter_rate_warn: Optional[float] = None,
        workflow_failed_rate_warn: Optional[float] = None,
        breakage_open_rate_warn: Optional[float] = None,
        breakage_helpdesk_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_triage_coverage_warn: Optional[float] = None,
        breakage_helpdesk_export_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_provider_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_provider_failed_min_jobs_warn: Optional[int] = None,
        breakage_helpdesk_provider_failed_rate_critical: Optional[float] = None,
        breakage_helpdesk_provider_failed_min_jobs_critical: Optional[int] = None,
        breakage_helpdesk_replay_failed_rate_warn: Optional[float] = None,
        breakage_helpdesk_replay_failed_total_warn: Optional[int] = None,
        breakage_helpdesk_replay_pending_total_warn: Optional[int] = None,
        doc_sync_checkout_gate_block_on_dead_letter_only: Optional[bool] = None,
        doc_sync_checkout_gate_max_pending_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_processing_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_failed_warn: Optional[int] = None,
        doc_sync_checkout_gate_max_dead_letter_warn: Optional[int] = None,
        doc_sync_dead_letter_trend_delta_warn: Optional[int] = None,
    ) -> Dict[str, Any]:
        summary = self.summary(
            window_days=window_days,
            site_id=site_id,
            target_object=target_object,
            template_key=template_key,
            overlay_cache_hit_rate_warn=overlay_cache_hit_rate_warn,
            overlay_cache_min_requests_warn=overlay_cache_min_requests_warn,
            doc_sync_dead_letter_rate_warn=doc_sync_dead_letter_rate_warn,
            workflow_failed_rate_warn=workflow_failed_rate_warn,
            breakage_open_rate_warn=breakage_open_rate_warn,
            breakage_helpdesk_failed_rate_warn=breakage_helpdesk_failed_rate_warn,
            breakage_helpdesk_failed_total_warn=breakage_helpdesk_failed_total_warn,
            breakage_helpdesk_triage_coverage_warn=breakage_helpdesk_triage_coverage_warn,
            breakage_helpdesk_export_failed_total_warn=breakage_helpdesk_export_failed_total_warn,
            breakage_helpdesk_provider_failed_rate_warn=breakage_helpdesk_provider_failed_rate_warn,
            breakage_helpdesk_provider_failed_min_jobs_warn=breakage_helpdesk_provider_failed_min_jobs_warn,
            breakage_helpdesk_provider_failed_rate_critical=breakage_helpdesk_provider_failed_rate_critical,
            breakage_helpdesk_provider_failed_min_jobs_critical=breakage_helpdesk_provider_failed_min_jobs_critical,
            breakage_helpdesk_replay_failed_rate_warn=breakage_helpdesk_replay_failed_rate_warn,
            breakage_helpdesk_replay_failed_total_warn=breakage_helpdesk_replay_failed_total_warn,
            breakage_helpdesk_replay_pending_total_warn=breakage_helpdesk_replay_pending_total_warn,
            doc_sync_checkout_gate_block_on_dead_letter_only=doc_sync_checkout_gate_block_on_dead_letter_only,
            doc_sync_checkout_gate_max_pending_warn=doc_sync_checkout_gate_max_pending_warn,
            doc_sync_checkout_gate_max_processing_warn=doc_sync_checkout_gate_max_processing_warn,
            doc_sync_checkout_gate_max_failed_warn=doc_sync_checkout_gate_max_failed_warn,
            doc_sync_checkout_gate_max_dead_letter_warn=doc_sync_checkout_gate_max_dead_letter_warn,
            doc_sync_dead_letter_trend_delta_warn=doc_sync_dead_letter_trend_delta_warn,
        )
        locale_context = _resolve_report_locale_context(
            self.session,
            locale_profile_id=locale_profile_id,
            report_lang=report_lang,
            report_type=report_type or "parallel_ops_summary",
        )
        normalized = str(export_format or "json").strip().lower()
        if normalized == "json":
            payload = dict(summary)
            if locale_context:
                payload["locale"] = locale_context
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            return {
                "content": content,
                "media_type": "application/json",
                "filename": "parallel-ops-summary.json",
            }

        rows = self._summary_export_rows(summary)
        if normalized == "csv":
            csv_io = io.StringIO()
            writer = csv.DictWriter(csv_io, fieldnames=["metric", "value"])
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": "parallel-ops-summary.csv",
            }

        if normalized == "md":
            lines = [
                "# Parallel Ops Summary",
                "",
                f"- generated_at: {summary.get('generated_at') or ''}",
                f"- window_days: {summary.get('window_days') or ''}",
                f"- window_since: {summary.get('window_since') or ''}",
                "",
            ]
            if locale_context:
                lines.extend(
                    [
                        "## Locale",
                        "",
                        f"- lang: {locale_context.get('lang') or ''}",
                        f"- profile_id: {locale_context.get('id') or ''}",
                        f"- report_type: {locale_context.get('report_type') or locale_context.get('requested_report_type') or ''}",
                        f"- timezone: {locale_context.get('timezone') or ''}",
                        "",
                    ]
                )
            lines.extend(
                [
                "| Metric | Value |",
                "| --- | --- |",
                ]
            )
            for row in rows:
                lines.append(f"| {row['metric']} | {row['value']} |")
            return {
                "content": ("\n".join(lines) + "\n").encode("utf-8"),
                "media_type": "text/markdown",
                "filename": "parallel-ops-summary.md",
            }

        raise ValueError("export_format must be json, csv or md")

    def _trend_export_rows(self, trends: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for row in trends.get("points") or []:
            if not isinstance(row, dict):
                continue
            doc_sync = row.get("doc_sync") if isinstance(row.get("doc_sync"), dict) else {}
            doc_sync_directions = (
                doc_sync.get("directions") if isinstance(doc_sync.get("directions"), dict) else {}
            )
            doc_sync_dead_letter_directions = (
                doc_sync.get("dead_letter_directions")
                if isinstance(doc_sync.get("dead_letter_directions"), dict)
                else {}
            )
            workflow = (
                row.get("workflow_actions")
                if isinstance(row.get("workflow_actions"), dict)
                else {}
            )
            breakages = row.get("breakages") if isinstance(row.get("breakages"), dict) else {}
            rows.append(
                {
                    "bucket_start": row.get("bucket_start"),
                    "bucket_end": row.get("bucket_end"),
                    "doc_sync_total": doc_sync.get("total"),
                    "doc_sync_push_total": doc_sync_directions.get("push"),
                    "doc_sync_pull_total": doc_sync_directions.get("pull"),
                    "doc_sync_unknown_direction_total": doc_sync_directions.get("unknown"),
                    "doc_sync_failed_total": doc_sync.get("failed_total"),
                    "doc_sync_dead_letter_total": doc_sync.get("dead_letter_total"),
                    "doc_sync_dead_letter_push_total": doc_sync_dead_letter_directions.get("push"),
                    "doc_sync_dead_letter_pull_total": doc_sync_dead_letter_directions.get("pull"),
                    "doc_sync_success_rate": doc_sync.get("success_rate"),
                    "doc_sync_dead_letter_rate": doc_sync.get("dead_letter_rate"),
                    "workflow_total": workflow.get("total"),
                    "workflow_failed_total": workflow.get("failed_total"),
                    "workflow_failed_rate": workflow.get("failed_rate"),
                    "breakages_total": breakages.get("total"),
                    "breakages_open_total": breakages.get("open_total"),
                    "breakages_open_rate": breakages.get("open_rate"),
                }
            )
        return rows

    def export_trends(
        self,
        *,
        window_days: int = 7,
        bucket_days: int = 1,
        site_id: Optional[str] = None,
        target_object: Optional[str] = None,
        template_key: Optional[str] = None,
        export_format: str = "json",
        report_lang: Optional[str] = None,
        report_type: Optional[str] = None,
        locale_profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        trends = self.trends(
            window_days=window_days,
            bucket_days=bucket_days,
            site_id=site_id,
            target_object=target_object,
            template_key=template_key,
        )
        locale_context = _resolve_report_locale_context(
            self.session,
            locale_profile_id=locale_profile_id,
            report_lang=report_lang,
            report_type=report_type or "parallel_ops_trends",
        )
        normalized = str(export_format or "json").strip().lower()
        if normalized == "json":
            payload = dict(trends)
            if locale_context:
                payload["locale"] = locale_context
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            return {
                "content": content,
                "media_type": "application/json",
                "filename": "parallel-ops-trends.json",
            }

        rows = self._trend_export_rows(trends)
        if normalized == "csv":
            csv_io = io.StringIO()
            writer = csv.DictWriter(
                csv_io,
                fieldnames=[
                    "bucket_start",
                    "bucket_end",
                    "doc_sync_total",
                    "doc_sync_push_total",
                    "doc_sync_pull_total",
                    "doc_sync_unknown_direction_total",
                    "doc_sync_failed_total",
                    "doc_sync_dead_letter_total",
                    "doc_sync_dead_letter_push_total",
                    "doc_sync_dead_letter_pull_total",
                    "doc_sync_success_rate",
                    "doc_sync_dead_letter_rate",
                    "workflow_total",
                    "workflow_failed_total",
                    "workflow_failed_rate",
                    "breakages_total",
                    "breakages_open_total",
                    "breakages_open_rate",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return {
                "content": csv_io.getvalue().encode("utf-8"),
                "media_type": "text/csv",
                "filename": "parallel-ops-trends.csv",
            }

        if normalized == "md":
            lines = [
                "# Parallel Ops Trends",
                "",
                f"- generated_at: {trends.get('generated_at') or ''}",
                f"- window_days: {trends.get('window_days') or ''}",
                f"- bucket_days: {trends.get('bucket_days') or ''}",
                f"- window_since: {trends.get('window_since') or ''}",
                "",
            ]
            if locale_context:
                lines.extend(
                    [
                        "## Locale",
                        "",
                        f"- lang: {locale_context.get('lang') or ''}",
                        f"- profile_id: {locale_context.get('id') or ''}",
                        f"- report_type: {locale_context.get('report_type') or locale_context.get('requested_report_type') or ''}",
                        f"- timezone: {locale_context.get('timezone') or ''}",
                        "",
                    ]
                )
            lines.extend(
                [
                "| Bucket Start | Bucket End | DocSync Total | DocSync Push | DocSync Pull | DocSync Failed | DocSync DeadLetter | DL Push | DL Pull | Workflow Total | Workflow Failed | Breakages Total | Breakages Open |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for row in rows:
                lines.append(
                    "| "
                    f"{row['bucket_start']} | "
                    f"{row['bucket_end']} | "
                    f"{row['doc_sync_total']} | "
                    f"{row['doc_sync_push_total']} | "
                    f"{row['doc_sync_pull_total']} | "
                    f"{row['doc_sync_failed_total']} | "
                    f"{row['doc_sync_dead_letter_total']} | "
                    f"{row['doc_sync_dead_letter_push_total']} | "
                    f"{row['doc_sync_dead_letter_pull_total']} | "
                    f"{row['workflow_total']} | "
                    f"{row['workflow_failed_total']} | "
                    f"{row['breakages_total']} | "
                    f"{row['breakages_open_total']} |"
                )
            return {
                "content": ("\n".join(lines) + "\n").encode("utf-8"),
                "media_type": "text/markdown",
                "filename": "parallel-ops-trends.md",
            }

        raise ValueError("export_format must be json, csv or md")
