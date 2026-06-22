from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from yuantus.config import get_settings
from yuantus.context import get_request_context
from yuantus.meta_engine.models.job import ConversionJob, JobStatus
from yuantus.meta_engine.services.job_service import JobService


@dataclass(frozen=True)
class SchedulerTask:
    name: str
    task_type: str
    interval_seconds: int
    priority: int = 90
    enabled: bool = True
    max_attempts: Optional[int] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SchedulerDecision:
    name: str
    task_type: str
    action: str
    reason: str
    job_id: Optional[str] = None
    dedupe_key: Optional[str] = None


@dataclass(frozen=True)
class SchedulerRunResult:
    enqueued: List[SchedulerDecision]
    skipped: List[SchedulerDecision]
    disabled: List[SchedulerDecision]
    would_enqueue: List[SchedulerDecision]

    @property
    def enqueued_count(self) -> int:
        return len(self.enqueued)

    @property
    def would_enqueue_count(self) -> int:
        return len(self.would_enqueue)


def default_scheduler_tasks(settings: Any) -> List[SchedulerTask]:
    bom_to_mbom_payload: Dict[str, Any] = {
        "source_item_ids": _csv_values(
            getattr(settings, "SCHEDULER_BOM_TO_MBOM_SOURCE_ITEM_IDS", "")
        ),
    }
    bom_to_mbom_plant_code = str(
        getattr(settings, "SCHEDULER_BOM_TO_MBOM_PLANT_CODE", "") or ""
    ).strip()
    if bom_to_mbom_plant_code:
        bom_to_mbom_payload["plant_code"] = bom_to_mbom_plant_code

    return [
        SchedulerTask(
            name="eco_approval_escalation",
            task_type="eco_approval_escalation",
            interval_seconds=int(
                getattr(settings, "SCHEDULER_ECO_ESCALATION_INTERVAL_SECONDS", 300)
                or 0
            ),
            priority=int(getattr(settings, "SCHEDULER_ECO_ESCALATION_PRIORITY", 80) or 80),
            enabled=bool(getattr(settings, "SCHEDULER_ECO_ESCALATION_ENABLED", True)),
            max_attempts=int(
                getattr(settings, "SCHEDULER_ECO_ESCALATION_MAX_ATTEMPTS", 1) or 1
            ),
        ),
        SchedulerTask(
            name="audit_retention_prune",
            task_type="audit_retention_prune",
            interval_seconds=int(
                getattr(settings, "SCHEDULER_AUDIT_RETENTION_INTERVAL_SECONDS", 3600)
                or 0
            ),
            priority=int(
                getattr(settings, "SCHEDULER_AUDIT_RETENTION_PRIORITY", 95) or 95
            ),
            enabled=bool(getattr(settings, "SCHEDULER_AUDIT_RETENTION_ENABLED", True)),
            max_attempts=int(
                getattr(settings, "SCHEDULER_AUDIT_RETENTION_MAX_ATTEMPTS", 1) or 1
            ),
        ),
        SchedulerTask(
            name="ecm_publication_outbox_prune",
            task_type="ecm_publication_outbox_prune",
            interval_seconds=int(
                getattr(settings, "SCHEDULER_ECM_OUTBOX_PRUNE_INTERVAL_SECONDS", 3600)
                or 0
            ),
            priority=int(
                getattr(settings, "SCHEDULER_ECM_OUTBOX_PRUNE_PRIORITY", 95) or 95
            ),
            enabled=bool(getattr(settings, "SCHEDULER_ECM_OUTBOX_PRUNE_ENABLED", True)),
            max_attempts=int(
                getattr(settings, "SCHEDULER_ECM_OUTBOX_PRUNE_MAX_ATTEMPTS", 1) or 1
            ),
        ),
        SchedulerTask(
            name="bom_to_mbom_sync",
            task_type="bom_to_mbom_sync",
            interval_seconds=int(
                getattr(settings, "SCHEDULER_BOM_TO_MBOM_INTERVAL_SECONDS", 3600)
                or 0
            ),
            priority=int(getattr(settings, "SCHEDULER_BOM_TO_MBOM_PRIORITY", 85) or 85),
            enabled=bool(getattr(settings, "SCHEDULER_BOM_TO_MBOM_ENABLED", False)),
            max_attempts=int(
                getattr(settings, "SCHEDULER_BOM_TO_MBOM_MAX_ATTEMPTS", 1) or 1
            ),
            payload=bom_to_mbom_payload,
        ),
    ]


def _csv_values(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).split(",")
    result: List[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        item = str(raw or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


class SchedulerService:
    """Enqueue due periodic work into the existing meta job queue."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Optional[Any] = None,
        tasks: Optional[List[SchedulerTask]] = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.tasks = tasks if tasks is not None else default_scheduler_tasks(self.settings)

    def run_once(
        self,
        *,
        now: Optional[datetime] = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> SchedulerRunResult:
        now = now or datetime.utcnow()
        enqueued: List[SchedulerDecision] = []
        skipped: List[SchedulerDecision] = []
        disabled: List[SchedulerDecision] = []
        would_enqueue: List[SchedulerDecision] = []

        if not force and not bool(getattr(self.settings, "SCHEDULER_ENABLED", False)):
            for task in self.tasks:
                disabled.append(
                    SchedulerDecision(
                        name=task.name,
                        task_type=task.task_type,
                        action="disabled",
                        reason="scheduler_disabled",
                    )
                )
            return SchedulerRunResult(
                enqueued=enqueued,
                skipped=skipped,
                disabled=disabled,
                would_enqueue=would_enqueue,
            )

        for task in self.tasks:
            decision = self._evaluate_task(
                task,
                now=now,
                force=force,
                dry_run=dry_run,
            )
            if decision.action == "enqueued":
                enqueued.append(decision)
            elif decision.action == "would_enqueue":
                would_enqueue.append(decision)
            elif decision.action == "disabled":
                disabled.append(decision)
            else:
                skipped.append(decision)

        return SchedulerRunResult(
            enqueued=enqueued,
            skipped=skipped,
            disabled=disabled,
            would_enqueue=would_enqueue,
        )

    def _evaluate_task(
        self,
        task: SchedulerTask,
        *,
        now: datetime,
        force: bool,
        dry_run: bool,
    ) -> SchedulerDecision:
        dedupe_key = self._dedupe_key(task)
        if not task.enabled:
            return SchedulerDecision(
                name=task.name,
                task_type=task.task_type,
                action="disabled",
                reason="task_disabled",
                dedupe_key=dedupe_key,
            )
        if task.interval_seconds <= 0:
            return SchedulerDecision(
                name=task.name,
                task_type=task.task_type,
                action="disabled",
                reason="interval_disabled",
                dedupe_key=dedupe_key,
            )

        last_job = self._last_job(dedupe_key)
        if last_job and last_job.status in {
            JobStatus.PENDING.value,
            JobStatus.PROCESSING.value,
        }:
            return SchedulerDecision(
                name=task.name,
                task_type=task.task_type,
                action="skipped",
                reason="active_job_exists",
                job_id=last_job.id,
                dedupe_key=dedupe_key,
            )

        last_ts = self._job_timestamp(last_job) if last_job else None
        if not force and last_ts and last_ts > now - timedelta(seconds=task.interval_seconds):
            return SchedulerDecision(
                name=task.name,
                task_type=task.task_type,
                action="skipped",
                reason="not_due",
                job_id=last_job.id,
                dedupe_key=dedupe_key,
            )

        if dry_run:
            return SchedulerDecision(
                name=task.name,
                task_type=task.task_type,
                action="would_enqueue",
                reason="dry_run_due",
                dedupe_key=dedupe_key,
            )

        job = JobService(self.session).create_job(
            task.task_type,
            self._payload(task, now=now),
            user_id=self._system_user_id(),
            priority=task.priority,
            max_attempts=task.max_attempts,
            dedupe_key=dedupe_key,
            dedupe=True,
        )
        return SchedulerDecision(
            name=task.name,
            task_type=task.task_type,
            action="enqueued",
            reason="due",
            job_id=job.id,
            dedupe_key=dedupe_key,
        )

    def _last_job(self, dedupe_key: str) -> Optional[ConversionJob]:
        return (
            self.session.query(ConversionJob)
            .filter(ConversionJob.dedupe_key == dedupe_key)
            .order_by(ConversionJob.created_at.desc())
            .first()
        )

    def _payload(self, task: SchedulerTask, *, now: datetime) -> Dict[str, Any]:
        ctx = get_request_context()
        payload = dict(task.payload or {})
        tenant_id = payload.get("tenant_id") or ctx.tenant_id
        org_id = payload.get("org_id") or ctx.org_id
        user_id = payload.get("user_id") or ctx.user_id or self._system_user_id()
        payload.update(
            {
                "scheduler_task": task.name,
                "scheduler_enqueued_at": now.isoformat(),
                "user_id": int(user_id) if str(user_id).isdigit() else user_id,
            }
        )
        if tenant_id:
            payload["tenant_id"] = str(tenant_id)
        if org_id:
            payload["org_id"] = str(org_id)
        return payload

    def _dedupe_key(self, task: SchedulerTask) -> str:
        ctx = get_request_context()
        tenant_id = str(ctx.tenant_id or "none").strip() or "none"
        org_id = str(ctx.org_id or "none").strip() or "none"
        return f"scheduler:{task.name}:tenant:{tenant_id}:org:{org_id}"

    def _system_user_id(self) -> int:
        return int(getattr(self.settings, "SCHEDULER_SYSTEM_USER_ID", 1) or 1)

    @staticmethod
    def _job_timestamp(job: ConversionJob) -> Optional[datetime]:
        return job.created_at or job.scheduled_at or job.completed_at or job.started_at
