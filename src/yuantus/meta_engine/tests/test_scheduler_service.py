from __future__ import annotations

import inspect
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.context import org_id_var, tenant_id_var
from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.models.job import ConversionJob, JobStatus
from yuantus.meta_engine.services.scheduler_service import SchedulerService, SchedulerTask
from yuantus.models.base import Base


@pytest.fixture()
def session():
    import_all_models()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[ConversionJob.__table__])
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def scoped_context():
    tenant_token = tenant_id_var.set("tenant-1")
    org_token = org_id_var.set("org-1")
    try:
        yield
    finally:
        org_id_var.reset(org_token)
        tenant_id_var.reset(tenant_token)


def _settings(**overrides):
    data = {
        "SCHEDULER_ENABLED": True,
        "SCHEDULER_SYSTEM_USER_ID": 1,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _task(
    *,
    name: str = "sample",
    task_type: str = "quota_test",
    interval_seconds: int = 300,
    enabled: bool = True,
) -> SchedulerTask:
    return SchedulerTask(
        name=name,
        task_type=task_type,
        interval_seconds=interval_seconds,
        enabled=enabled,
        max_attempts=1,
    )


def test_scheduler_disabled_without_force_does_not_enqueue(session, scoped_context):
    service = SchedulerService(
        session,
        settings=_settings(SCHEDULER_ENABLED=False),
        tasks=[_task()],
    )

    result = service.run_once(now=datetime(2026, 4, 21, 1, 0, 0))

    assert result.enqueued == []
    assert [d.reason for d in result.disabled] == ["scheduler_disabled"]
    assert session.query(ConversionJob).count() == 0


def test_scheduler_force_enqueues_due_task_with_scoped_payload(session, scoped_context):
    now = datetime(2026, 4, 21, 1, 0, 0)
    service = SchedulerService(
        session,
        settings=_settings(SCHEDULER_ENABLED=False, SCHEDULER_SYSTEM_USER_ID=9),
        tasks=[_task()],
    )

    result = service.run_once(now=now, force=True)

    assert result.enqueued_count == 1
    job = session.query(ConversionJob).one()
    assert job.task_type == "quota_test"
    assert job.created_by_id == 9
    assert job.dedupe_key == "scheduler:sample:tenant:tenant-1:org:org-1"
    assert job.payload["tenant_id"] == "tenant-1"
    assert job.payload["org_id"] == "org-1"
    assert job.payload["user_id"] == 9
    assert job.payload["scheduler_task"] == "sample"
    assert job.payload["scheduler_enqueued_at"] == now.isoformat()


def test_scheduler_dry_run_reports_due_task_without_enqueue(session, scoped_context):
    now = datetime(2026, 4, 21, 1, 0, 0)
    service = SchedulerService(
        session,
        settings=_settings(SCHEDULER_ENABLED=False, SCHEDULER_SYSTEM_USER_ID=9),
        tasks=[_task()],
    )

    result = service.run_once(now=now, force=True, dry_run=True)

    assert result.enqueued == []
    assert result.enqueued_count == 0
    assert result.would_enqueue_count == 1
    decision = result.would_enqueue[0]
    assert decision.action == "would_enqueue"
    assert decision.reason == "dry_run_due"
    assert decision.task_type == "quota_test"
    assert decision.job_id is None
    assert decision.dedupe_key == "scheduler:sample:tenant:tenant-1:org:org-1"
    assert session.query(ConversionJob).count() == 0


def test_scheduler_dry_run_still_reports_active_existing_job(session, scoped_context):
    session.add(
        ConversionJob(
            task_type="quota_test",
            payload={"scheduler_task": "sample"},
            status=JobStatus.PENDING.value,
            dedupe_key="scheduler:sample:tenant:tenant-1:org:org-1",
            created_at=datetime(2026, 4, 21, 1, 0, 0),
        )
    )
    session.commit()
    service = SchedulerService(session, settings=_settings(), tasks=[_task()])

    result = service.run_once(
        now=datetime(2026, 4, 21, 2, 0, 0),
        force=True,
        dry_run=True,
    )

    assert result.would_enqueue == []
    assert [d.reason for d in result.skipped] == ["active_job_exists"]
    assert session.query(ConversionJob).count() == 1


def test_scheduler_skips_active_existing_job(session, scoped_context):
    session.add(
        ConversionJob(
            task_type="quota_test",
            payload={"scheduler_task": "sample"},
            status=JobStatus.PENDING.value,
            dedupe_key="scheduler:sample:tenant:tenant-1:org:org-1",
            created_at=datetime(2026, 4, 21, 1, 0, 0),
        )
    )
    session.commit()
    service = SchedulerService(session, settings=_settings(), tasks=[_task()])

    result = service.run_once(now=datetime(2026, 4, 21, 2, 0, 0), force=True)

    assert result.enqueued == []
    assert [d.reason for d in result.skipped] == ["active_job_exists"]
    assert session.query(ConversionJob).count() == 1


def test_scheduler_skips_recent_completed_job_until_interval(session, scoped_context):
    session.add(
        ConversionJob(
            task_type="quota_test",
            payload={"scheduler_task": "sample"},
            status=JobStatus.COMPLETED.value,
            dedupe_key="scheduler:sample:tenant:tenant-1:org:org-1",
            created_at=datetime(2026, 4, 21, 1, 59, 0),
        )
    )
    session.commit()
    service = SchedulerService(
        session,
        settings=_settings(),
        tasks=[_task(interval_seconds=300)],
    )

    result = service.run_once(now=datetime(2026, 4, 21, 2, 0, 0))

    assert result.enqueued == []
    assert [d.reason for d in result.skipped] == ["not_due"]
    assert session.query(ConversionJob).count() == 1


def test_scheduler_enqueues_after_interval_elapsed(session, scoped_context):
    session.add(
        ConversionJob(
            task_type="quota_test",
            payload={"scheduler_task": "sample"},
            status=JobStatus.COMPLETED.value,
            dedupe_key="scheduler:sample:tenant:tenant-1:org:org-1",
            created_at=datetime(2026, 4, 21, 1, 0, 0),
        )
    )
    session.commit()
    service = SchedulerService(
        session,
        settings=_settings(),
        tasks=[_task(interval_seconds=300)],
    )

    result = service.run_once(now=datetime(2026, 4, 21, 2, 0, 0))

    assert result.enqueued_count == 1
    assert session.query(ConversionJob).count() == 2


def test_disabled_task_is_reported_without_enqueue(session, scoped_context):
    service = SchedulerService(
        session,
        settings=_settings(),
        tasks=[_task(enabled=False)],
    )

    result = service.run_once(now=datetime(2026, 4, 21, 1, 0, 0))

    assert result.enqueued == []
    assert [d.reason for d in result.disabled] == ["task_disabled"]


def test_default_task_registry_keeps_scheduler_tasks_bounded(session):
    settings = _settings(
        SCHEDULER_ECO_ESCALATION_ENABLED=True,
        SCHEDULER_ECO_ESCALATION_INTERVAL_SECONDS=300,
        SCHEDULER_ECO_ESCALATION_PRIORITY=80,
        SCHEDULER_ECO_ESCALATION_MAX_ATTEMPTS=1,
        SCHEDULER_AUDIT_RETENTION_ENABLED=True,
        SCHEDULER_AUDIT_RETENTION_INTERVAL_SECONDS=3600,
        SCHEDULER_AUDIT_RETENTION_PRIORITY=95,
        SCHEDULER_AUDIT_RETENTION_MAX_ATTEMPTS=1,
        SCHEDULER_BOM_TO_MBOM_ENABLED=False,
        SCHEDULER_BOM_TO_MBOM_INTERVAL_SECONDS=3600,
        SCHEDULER_BOM_TO_MBOM_PRIORITY=85,
        SCHEDULER_BOM_TO_MBOM_MAX_ATTEMPTS=1,
        SCHEDULER_BOM_TO_MBOM_SOURCE_ITEM_IDS="part-1, part-2,part-1",
        SCHEDULER_BOM_TO_MBOM_PLANT_CODE="PLANT-1",
    )

    tasks = SchedulerService(session, settings=settings).tasks
    names = [task.name for task in tasks]

    assert names == [
        "eco_approval_escalation",
        "audit_retention_prune",
        "ecm_publication_outbox_prune",
        "bom_to_mbom_sync",
    ]
    bom_to_mbom = tasks[3]
    assert bom_to_mbom.enabled is False
    assert bom_to_mbom.payload == {
        "source_item_ids": ["part-1", "part-2"],
        "plant_code": "PLANT-1",
    }


def test_eco_scheduler_task_delegates_to_existing_service():
    from yuantus.meta_engine.tasks.scheduler_tasks import eco_approval_escalation

    session = MagicMock()
    with patch(
        "yuantus.meta_engine.tasks.scheduler_tasks.ECOApprovalService"
    ) as service_cls:
        service_cls.return_value.escalate_overdue_approvals.return_value = {
            "escalated": 1,
            "items": [{"eco_id": "eco-1"}],
        }

        result = eco_approval_escalation({"user_id": 7}, session)

    service_cls.assert_called_once_with(session)
    service_cls.return_value.escalate_overdue_approvals.assert_called_once_with(user_id=7)
    assert result == {
        "ok": True,
        "task": "eco_approval_escalation",
        "escalated": 1,
        "items": [{"eco_id": "eco-1"}],
    }


def test_audit_retention_scheduler_task_prunes_when_enabled():
    from yuantus.meta_engine.tasks.scheduler_tasks import audit_retention_prune

    settings = SimpleNamespace(AUDIT_RETENTION_DAYS=30, AUDIT_RETENTION_MAX_ROWS=1000)
    session = MagicMock()
    with patch("yuantus.meta_engine.tasks.scheduler_tasks.get_settings", return_value=settings):
        with patch("yuantus.meta_engine.tasks.scheduler_tasks.prune_audit_logs") as prune:
            with patch("yuantus.meta_engine.tasks.scheduler_tasks.mark_prune") as mark:
                prune.return_value = 3

                result = audit_retention_prune({"tenant_id": "tenant-1"}, session)

    prune.assert_called_once_with(
        session,
        retention_days=30,
        retention_max_rows=1000,
        tenant_id="tenant-1",
    )
    mark.assert_called_once_with("tenant-1")
    assert result["deleted"] == 3
    assert result["tenant_id"] == "tenant-1"


def test_audit_retention_scheduler_task_skips_when_retention_disabled():
    from yuantus.meta_engine.tasks.scheduler_tasks import audit_retention_prune

    settings = SimpleNamespace(AUDIT_RETENTION_DAYS=0, AUDIT_RETENTION_MAX_ROWS=0)
    with patch("yuantus.meta_engine.tasks.scheduler_tasks.get_settings", return_value=settings):
        with patch("yuantus.meta_engine.tasks.scheduler_tasks.prune_audit_logs") as prune:
            result = audit_retention_prune({}, MagicMock())

    prune.assert_not_called()
    assert result["skipped"] is True
    assert result["reason"] == "retention_disabled"


def test_ecm_outbox_prune_scheduler_task_passes_batch_limit():
    from yuantus.meta_engine.tasks.scheduler_tasks import ecm_publication_outbox_prune

    settings = SimpleNamespace(
        PUBLICATION_ECM_OUTBOX_RETENTION_DAYS=90,
        PUBLICATION_ECM_OUTBOX_RETENTION_BATCH_SIZE=1000,
    )
    session = MagicMock()
    with patch(
        "yuantus.meta_engine.tasks.scheduler_tasks.get_settings", return_value=settings
    ):
        with patch(
            "yuantus.meta_engine.ecm_publication.service.EcmPublicationOutboxService"
        ) as Svc:
            Svc.return_value.prune_terminal.return_value = 7
            result = ecm_publication_outbox_prune({}, session)

    # the production path MUST bound the delete by the batch size (regression guard)
    Svc.return_value.prune_terminal.assert_called_once_with(
        retention_days=90, limit=1000
    )
    assert result["deleted"] == 7
    assert result["retention_days"] == 90
    assert result["batch_size"] == 1000


def test_ecm_outbox_prune_scheduler_task_skips_when_disabled():
    from yuantus.meta_engine.tasks.scheduler_tasks import ecm_publication_outbox_prune

    settings = SimpleNamespace(
        PUBLICATION_ECM_OUTBOX_RETENTION_DAYS=0,
        PUBLICATION_ECM_OUTBOX_RETENTION_BATCH_SIZE=1000,
    )
    with patch(
        "yuantus.meta_engine.tasks.scheduler_tasks.get_settings", return_value=settings
    ):
        with patch(
            "yuantus.meta_engine.ecm_publication.service.EcmPublicationOutboxService"
        ) as Svc:
            result = ecm_publication_outbox_prune({}, MagicMock())

    Svc.return_value.prune_terminal.assert_not_called()
    assert result["skipped"] is True
    assert result["reason"] == "retention_disabled"


def test_bom_to_mbom_scheduler_task_skips_without_source_ids():
    from yuantus.meta_engine.tasks.scheduler_tasks import bom_to_mbom_sync

    result = bom_to_mbom_sync({}, MagicMock())

    assert result == {
        "ok": True,
        "task": "bom_to_mbom_sync",
        "skipped": True,
        "reason": "no_source_item_ids",
        "created": 0,
        "skipped_count": 0,
        "errors": [],
        "items": [],
    }


def test_bom_to_mbom_scheduler_task_creates_for_released_part():
    from yuantus.meta_engine.tasks.scheduler_tasks import bom_to_mbom_sync

    item = SimpleNamespace(
        id="part-1",
        item_type_id="Part",
        is_current=True,
        state="Released",
        properties={"item_number": "P-001"},
    )
    mbom = SimpleNamespace(id="mbom-1", name="MBOM P-001")
    session = MagicMock()
    session.get.return_value = item

    with patch(
        "yuantus.meta_engine.tasks.scheduler_tasks._latest_mbom_for_source",
        return_value=None,
    ) as latest:
        with patch("yuantus.meta_engine.tasks.scheduler_tasks.MBOMService") as svc_cls:
            svc_cls.return_value.create_mbom_from_ebom.return_value = mbom

            result = bom_to_mbom_sync(
                {
                    "source_item_ids": ["part-1"],
                    "user_id": 7,
                    "plant_code": "PLANT-1",
                    "effective_from": "2026-04-21T00:00:00",
                    "transformation_rules": {"collapse_phantom": True},
                },
                session,
            )

    latest.assert_called_once_with(session, "part-1")
    svc_cls.assert_called_once_with(session)
    call = svc_cls.return_value.create_mbom_from_ebom.call_args
    assert call.args[0] == "part-1"
    assert call.args[1].startswith("MBOM P-001-")
    assert call.kwargs["version"] == "1.0"
    assert call.kwargs["plant_code"] == "PLANT-1"
    assert call.kwargs["user_id"] == 7
    assert call.kwargs["transformation_rules"] == {"collapse_phantom": True}
    assert result["ok"] is True
    assert result["created"] == 1
    assert result["items"] == [
        {"source_item_id": "part-1", "mbom_id": "mbom-1", "name": "MBOM P-001"}
    ]


def test_bom_to_mbom_scheduler_task_skips_non_released_by_default():
    from yuantus.meta_engine.tasks.scheduler_tasks import bom_to_mbom_sync

    item = SimpleNamespace(
        id="part-1",
        item_type_id="Part",
        is_current=True,
        state="Draft",
        properties={},
    )
    session = MagicMock()
    session.get.return_value = item

    with patch("yuantus.meta_engine.tasks.scheduler_tasks.MBOMService") as svc_cls:
        result = bom_to_mbom_sync({"source_item_ids": "part-1"}, session)

    svc_cls.return_value.create_mbom_from_ebom.assert_not_called()
    assert result["created"] == 0
    assert result["skipped_items"] == [
        {"source_item_id": "part-1", "reason": "not_released", "state": "Draft"}
    ]


def test_bom_to_mbom_scheduler_task_skips_existing_mbom():
    from yuantus.meta_engine.tasks.scheduler_tasks import bom_to_mbom_sync

    item = SimpleNamespace(
        id="part-1",
        item_type_id="Part",
        is_current=True,
        state="Released",
        properties={},
    )
    existing = SimpleNamespace(id="mbom-existing")
    session = MagicMock()
    session.get.return_value = item

    with patch(
        "yuantus.meta_engine.tasks.scheduler_tasks._latest_mbom_for_source",
        return_value=existing,
    ):
        with patch("yuantus.meta_engine.tasks.scheduler_tasks.MBOMService") as svc_cls:
            result = bom_to_mbom_sync({"source_item_id": "part-1"}, session)

    svc_cls.return_value.create_mbom_from_ebom.assert_not_called()
    assert result["created"] == 0
    assert result["skipped_items"] == [
        {
            "source_item_id": "part-1",
            "reason": "mbom_exists",
            "mbom_id": "mbom-existing",
        }
    ]


def test_cli_registers_scheduler_command_and_worker_handlers():
    import yuantus.cli as cli

    scheduler_src = inspect.getsource(cli.scheduler)
    worker_src = inspect.getsource(cli.worker)

    assert "SchedulerService" in scheduler_src
    assert "dry_run" in scheduler_src
    assert "run_once(force=force, dry_run=dry_run)" in scheduler_src
    assert '"would_enqueue": [d.__dict__ for d in result.would_enqueue]' in scheduler_src
    assert "eco_approval_escalation" in worker_src
    assert "audit_retention_prune" in worker_src
    assert "ecm_publication_outbox_prune" in worker_src
    assert "bom_to_mbom_sync" in worker_src
