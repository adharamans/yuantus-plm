from __future__ import annotations

import io
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.models.eco import ECO, ECOStage
from yuantus.meta_engine.models.item import Item  # noqa: F401
from yuantus.meta_engine.models.job import ConversionJob
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
from yuantus.meta_engine.report_locale.models import ReportLocaleProfile
from yuantus.meta_engine.services.parallel_tasks_service import (
    BreakageIncidentService,
    ConsumptionPlanService,
    DocumentMultiSiteService,
    ECOActivityValidationService,
    ParallelOpsOverviewService,
    ThreeDOverlayService,
    WorkflowCustomActionService,
    WorkorderDocumentPackService,
)
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            RBACUser.__table__,
            RemoteSite.__table__,
            ECOActivityGate.__table__,
            ECOActivityGateEvent.__table__,
            WorkflowCustomActionRule.__table__,
            WorkflowCustomActionRun.__table__,
            ConsumptionPlan.__table__,
            ConsumptionRecord.__table__,
            BreakageIncident.__table__,
            WorkorderDocumentLink.__table__,
            ThreeDOverlay.__table__,
            ConversionJob.__table__,
            ReportLocaleProfile.__table__,
            ItemVersion.__table__,
            # Tier-B #3 §3.7: prometheus_metrics now reads
            # `eco_id IN (SELECT id FROM meta_ecos)` for the
            # design-loopback link gauges.
            ECOStage.__table__,
            ECO.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_document_multi_site_service_can_upsert_and_replay_jobs(session):
    service = DocumentMultiSiteService(session)

    site_a = service.upsert_remote_site(
        name="site-a",
        endpoint="https://example.test/plm",
        auth_secret="secret-token",
    )
    site_b = service.upsert_remote_site(
        name="site-b",
        endpoint="https://example-b.test/plm",
        auth_secret="secret-token-b",
    )
    session.commit()

    assert site_a.name == "site-a"
    assert site_b.name == "site-b"
    assert site_a.auth_secret_ciphertext and "secret-token" not in site_a.auth_secret_ciphertext

    # A -> B push sample
    push_job = service.enqueue_sync(
        site_id=site_a.id,
        direction="push",
        document_ids=["doc-b", "doc-a", "doc-a"],
        user_id=7,
    )
    assert push_job.task_type == "document_sync_push"
    assert push_job.payload["document_ids"] == ["doc-a", "doc-b"]

    # B -> A pull sample
    pull_job = service.enqueue_sync(
        site_id=site_b.id,
        direction="pull",
        document_ids=["doc-x"],
        user_id=7,
    )
    assert pull_job.task_type == "document_sync_pull"

    jobs = service.list_sync_jobs(site_id=site_a.id, limit=10)
    assert len(jobs) == 1
    assert jobs[0].id == push_job.id

    replay = service.replay_sync_job(push_job.id, user_id=7)
    assert replay.id != push_job.id
    assert replay.payload.get("replay_of") == push_job.id


def test_document_multi_site_service_idempotency_filters_and_dead_letter_view(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-filter",
        endpoint="https://example-filter.test/plm",
        auth_secret="secret-token",
    )
    session.commit()

    first = service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=["doc-1"],
        idempotency_key="sync-k-1",
        metadata_json={"retry_max_attempts": 4},
    )
    second = service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=["doc-1"],
        idempotency_key="sync-k-1",
        metadata_json={"retry_max_attempts": 4},
    )
    session.commit()

    assert first.id == second.id
    assert first.payload["idempotency_conflicts"] == 1
    assert first.payload["sync_trace"]["trace_id"]
    assert first.payload["sync_trace"]["payload_hash"]
    assert first.max_attempts == 4

    first.status = "failed"
    first.attempt_count = 4
    first.max_attempts = 4
    first.last_error = "network timeout"
    session.flush()

    view = service.build_sync_job_view(first)
    assert view["is_dead_letter"] is True
    assert view["dead_letter_reason"] == "network timeout"
    assert view["retry_budget"]["remaining_attempts"] == 0

    third = service.enqueue_sync(
        site_id=site.id,
        direction="pull",
        document_ids=["doc-2"],
        idempotency_key="sync-k-2",
    )
    third.status = "completed"
    third.created_at = datetime.utcnow() + timedelta(seconds=1)
    session.commit()

    pending = service.list_sync_jobs(site_id=site.id, status="failed", limit=10)
    assert len(pending) == 1
    assert pending[0].id == first.id

    with pytest.raises(ValueError, match="status must be one of"):
        service.list_sync_jobs(status="deadletter")


def test_document_multi_site_sync_summary_by_site_and_window(session):
    service = DocumentMultiSiteService(session)
    site_a = service.upsert_remote_site(
        name="site-a-summary",
        endpoint="https://summary-a.example.test/plm",
        auth_secret="tok-a",
    )
    site_b = service.upsert_remote_site(
        name="site-b-summary",
        endpoint="https://summary-b.example.test/plm",
        auth_secret="tok-b",
    )
    session.commit()

    now = datetime.utcnow()
    job_a_ok = service.enqueue_sync(
        site_id=site_a.id,
        direction="push",
        document_ids=["doc-a-1"],
        idempotency_key="sum-a-ok",
    )
    job_a_dead = service.enqueue_sync(
        site_id=site_a.id,
        direction="pull",
        document_ids=["doc-a-2"],
        idempotency_key="sum-a-dead",
    )
    job_b_processing = service.enqueue_sync(
        site_id=site_b.id,
        direction="push",
        document_ids=["doc-b-1"],
        idempotency_key="sum-b-processing",
    )
    job_b_old = service.enqueue_sync(
        site_id=site_b.id,
        direction="pull",
        document_ids=["doc-b-2"],
        idempotency_key="sum-b-old",
    )

    job_a_ok.status = "completed"
    job_a_ok.attempt_count = 1
    job_a_ok.max_attempts = 3
    job_a_ok.created_at = now - timedelta(hours=1)

    job_a_dead.status = "failed"
    job_a_dead.attempt_count = 3
    job_a_dead.max_attempts = 3
    job_a_dead.last_error = "network timeout"
    job_a_dead.created_at = now - timedelta(hours=2)

    job_b_processing.status = "processing"
    job_b_processing.attempt_count = 1
    job_b_processing.max_attempts = 3
    job_b_processing.created_at = now - timedelta(hours=3)

    job_b_old.status = "completed"
    job_b_old.attempt_count = 1
    job_b_old.max_attempts = 3
    job_b_old.created_at = now - timedelta(days=12)
    session.commit()

    summary = service.sync_summary(window_days=7)
    assert summary["window_days"] == 7
    assert summary["total_jobs"] == 3
    assert summary["total_sites"] == 2
    assert summary["overall_by_status"]["completed"] == 1
    assert summary["overall_by_status"]["failed"] == 1
    assert summary["overall_by_status"]["processing"] == 1
    assert summary["overall_dead_letter_total"] == 1

    by_site = {row["site_id"]: row for row in summary["sites"]}
    site_a_summary = by_site[site_a.id]
    assert site_a_summary["total"] == 2
    assert site_a_summary["directions"]["push"] == 1
    assert site_a_summary["directions"]["pull"] == 1
    assert site_a_summary["dead_letter_total"] == 1
    assert site_a_summary["success_rate"] == 0.5
    assert site_a_summary["failure_rate"] == 0.5
    assert site_a_summary["last_job_at"] is not None

    site_b_summary = by_site[site_b.id]
    assert site_b_summary["total"] == 1
    assert site_b_summary["by_status"] == {"processing": 1}
    assert site_b_summary["dead_letter_total"] == 0
    assert site_b_summary["success_rate"] == 0.0
    assert site_b_summary["failure_rate"] == 0.0

    filtered = service.sync_summary(site_id=site_a.id, window_days=7)
    assert filtered["site_filter"] == site_a.id
    assert filtered["total_jobs"] == 2
    assert filtered["total_sites"] == 1
    assert filtered["sites"][0]["site_id"] == site_a.id

    with pytest.raises(ValueError, match="window_days must be between 1 and 90"):
        service.sync_summary(window_days=0)


def test_document_multi_site_dead_letter_list_batch_replay_and_summary_export(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-ops",
        endpoint="https://ops.example.test/plm",
        auth_secret="ops-token",
    )
    session.commit()

    first = service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=["doc-1"],
        idempotency_key="ops-dead-1",
    )
    second = service.enqueue_sync(
        site_id=site.id,
        direction="pull",
        document_ids=["doc-2"],
        idempotency_key="ops-failed-not-dead",
    )
    first.status = "failed"
    first.attempt_count = 3
    first.max_attempts = 3
    first.last_error = "retry exhausted"
    second.status = "failed"
    second.attempt_count = 1
    second.max_attempts = 3
    second.last_error = "temporary network"
    session.commit()

    dead_letters = service.list_dead_letter_sync_jobs(site_id=site.id, window_days=7, limit=50)
    assert len(dead_letters) == 1
    assert dead_letters[0].id == first.id

    replay_result = service.replay_sync_jobs_batch(
        site_id=site.id,
        only_dead_letter=True,
        window_days=7,
        limit=20,
        user_id=9,
    )
    assert replay_result["requested"] == 1
    assert replay_result["replayed"] == 1
    assert replay_result["failed"] == 0
    assert len(replay_result["replayed_jobs"]) == 1
    replayed_job_id = replay_result["replayed_jobs"][0]["replayed_job_id"]
    replayed_job = service.get_sync_job(replayed_job_id)
    assert replayed_job is not None
    assert replayed_job.payload.get("replay_of") == first.id

    replay_result_all_failed = service.replay_sync_jobs_batch(
        site_id=site.id,
        only_dead_letter=False,
        window_days=7,
        limit=20,
        user_id=9,
    )
    assert replay_result_all_failed["requested"] == 2
    assert replay_result_all_failed["replayed"] == 2

    exported_json = service.export_sync_summary(site_id=site.id, window_days=7, export_format="json")
    assert exported_json["filename"] == "doc-sync-summary.json"
    assert exported_json["media_type"] == "application/json"
    parsed_json = json.loads(exported_json["content"].decode("utf-8"))
    assert parsed_json["site_filter"] == site.id

    exported_csv = service.export_sync_summary(site_id=site.id, window_days=7, export_format="csv")
    assert exported_csv["filename"] == "doc-sync-summary.csv"
    csv_text = exported_csv["content"].decode("utf-8")
    assert "dead_letter_total" in csv_text
    assert site.id in csv_text

    exported_md = service.export_sync_summary(site_id=site.id, window_days=7, export_format="md")
    assert exported_md["filename"] == "doc-sync-summary.md"
    md_text = exported_md["content"].decode("utf-8")
    assert "# Doc Sync Summary" in md_text
    assert site.id in md_text

    with pytest.raises(ValueError, match="export_format must be json, csv or md"):
        service.export_sync_summary(site_id=site.id, window_days=7, export_format="xlsx")

    with pytest.raises(ValueError, match="window_days must be between 1 and 90"):
        service.list_dead_letter_sync_jobs(site_id=site.id, window_days=0, limit=10)
    with pytest.raises(ValueError, match="limit must be between 1 and 500"):
        service.replay_sync_jobs_batch(site_id=site.id, limit=0)


def test_document_multi_site_checkout_gate_blocks_on_sync_backlog(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-checkout",
        endpoint="https://checkout.example.test/plm",
        auth_secret="checkout-token",
    )
    session.commit()

    item_id = "item-checkout-1"
    job_pending = service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=[item_id],
        idempotency_key="checkout-pending",
    )
    job_processing = service.enqueue_sync(
        site_id=site.id,
        direction="pull",
        document_ids=[item_id],
        idempotency_key="checkout-processing",
    )
    job_processing.status = "processing"
    job_failed = service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=[item_id],
        idempotency_key="checkout-failed",
    )
    job_failed.status = "failed"
    job_failed.attempt_count = 3
    job_failed.max_attempts = 3
    job_failed.last_error = "retry exhausted"
    job_completed = service.enqueue_sync(
        site_id=site.id,
        direction="pull",
        document_ids=[item_id],
        idempotency_key="checkout-completed",
    )
    job_completed.status = "completed"
    service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=["item-other"],
        idempotency_key="checkout-other",
    )
    session.commit()

    gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        window_days=7,
        limit=20,
    )
    assert gate["blocking"] is True
    assert gate["blocking_total"] == 3
    assert gate["blocking_counts"]["pending"] == 1
    assert gate["blocking_counts"]["processing"] == 1
    assert gate["blocking_counts"]["failed"] == 1
    assert gate["blocking_counts"]["dead_letter"] == 1
    blocking_ids = {row["id"] for row in gate["blocking_jobs"]}
    assert job_pending.id in blocking_ids
    assert job_processing.id in blocking_ids
    assert job_failed.id in blocking_ids
    assert job_completed.id not in blocking_ids

    clear_gate = service.evaluate_checkout_sync_gate(
        item_id="item-other",
        site_id=site.id,
        window_days=7,
        limit=20,
    )
    assert clear_gate["blocking"] is True
    assert clear_gate["blocking_total"] == 1
    assert clear_gate["blocking_counts"]["pending"] == 1

    with pytest.raises(ValueError, match="item_id must not be empty"):
        service.evaluate_checkout_sync_gate(item_id="", site_id=site.id)
    with pytest.raises(ValueError, match="site_id must not be empty"):
        service.evaluate_checkout_sync_gate(item_id=item_id, site_id="")
    with pytest.raises(ValueError, match="window_days must be between 1 and 90"):
        service.evaluate_checkout_sync_gate(item_id=item_id, site_id=site.id, window_days=0)
    with pytest.raises(ValueError, match="limit must be between 1 and 500"):
        service.evaluate_checkout_sync_gate(item_id=item_id, site_id=site.id, limit=0)


def test_document_multi_site_checkout_gate_supports_document_scope(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-checkout-scope",
        endpoint="https://checkout-scope.example.test/plm",
        auth_secret="checkout-token",
    )
    session.commit()

    target_file_id = "file-a"
    other_file_id = "file-b"
    target_job = service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=[target_file_id],
        idempotency_key="checkout-scope-target",
    )
    other_job = service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=[other_file_id],
        idempotency_key="checkout-scope-other",
    )
    session.commit()

    gate = service.evaluate_checkout_sync_gate(
        item_id="item-checkout-scope",
        version_id="ver-checkout-scope",
        document_ids=[target_file_id],
        site_id=site.id,
        window_days=7,
        limit=20,
    )
    assert gate["blocking"] is True
    assert gate["blocking_total"] == 1
    assert gate["monitored_document_ids"] == [target_file_id, "ver-checkout-scope"]
    assert gate["matched_document_ids"] == [target_file_id]
    assert gate["blocking_jobs"][0]["id"] == target_job.id
    assert gate["blocking_jobs"][0]["matched_document_ids"] == [target_file_id]
    assert gate["blocking_jobs"][0]["id"] != other_job.id


def test_document_multi_site_checkout_gate_supports_thresholds_and_dead_letter_policy(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-checkout-thresholds",
        endpoint="https://checkout-thresholds.example.test/plm",
        auth_secret="checkout-token",
    )
    session.commit()

    item_id = "item-checkout-thresholds-1"
    service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=[item_id],
        idempotency_key="checkout-thresholds-pending",
    )
    failed_job = service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=[item_id],
        idempotency_key="checkout-thresholds-failed",
    )
    failed_job.status = "failed"
    failed_job.attempt_count = 1
    failed_job.max_attempts = 3
    failed_job.last_error = "temporary retry"
    session.commit()

    tolerant_gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        max_pending=1,
        max_failed=1,
        max_dead_letter=0,
    )
    assert tolerant_gate["blocking"] is False
    assert tolerant_gate["blocking_counts"]["pending"] == 1
    assert tolerant_gate["blocking_counts"]["failed"] == 1
    assert tolerant_gate["blocking_counts"]["dead_letter"] == 0
    assert tolerant_gate["blocking_reasons"] == []

    failed_job.attempt_count = 3
    failed_job.max_attempts = 3
    failed_job.last_error = "retry exhausted"
    session.commit()

    dead_letter_gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        block_on_dead_letter_only=True,
        max_pending=99,
        max_processing=99,
        max_failed=99,
        max_dead_letter=0,
    )
    assert dead_letter_gate["blocking"] is True
    assert dead_letter_gate["policy"]["block_on_dead_letter_only"] is True
    assert dead_letter_gate["thresholds"]["dead_letter"] == 0
    assert dead_letter_gate["blocking_reasons"] == [
        {"status": "dead_letter", "count": 1, "threshold": 0}
    ]
    assert dead_letter_gate["blocking_total"] == 1

    relaxed_dead_letter_gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        block_on_dead_letter_only=True,
        max_dead_letter=1,
    )
    assert relaxed_dead_letter_gate["blocking"] is False
    assert relaxed_dead_letter_gate["blocking_counts"]["dead_letter"] == 1
    assert relaxed_dead_letter_gate["blocking_reasons"] == []

    with pytest.raises(ValueError, match="max_pending must be a non-negative integer"):
        service.evaluate_checkout_sync_gate(item_id=item_id, site_id=site.id, max_pending=-1)


def test_document_multi_site_checkout_gate_warn_mode_returns_warning_without_blocking(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-checkout-warn-mode",
        endpoint="https://checkout-warn.example.test/plm",
        auth_secret="checkout-token",
    )
    session.commit()

    item_id = "item-checkout-warn-mode-1"
    service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=[item_id],
        idempotency_key="checkout-warn-mode-pending",
    )
    session.commit()

    gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        mode="warn",
        max_pending=0,
    )

    assert gate["mode"] == "warn"
    assert gate["verdict"] == "warn"
    assert gate["threshold_exceeded"] is True
    assert gate["warning"] is True
    assert gate["blocking"] is False
    assert gate["policy"]["mode"] == "warn"
    assert gate["blocking_reasons"] == [
        {"status": "pending", "count": 1, "threshold": 0}
    ]

    with pytest.raises(ValueError, match="mode must be block or warn"):
        service.evaluate_checkout_sync_gate(
            item_id=item_id,
            site_id=site.id,
            mode="invalid",
        )


def test_document_multi_site_checkout_gate_supports_direction_threshold_overrides(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-checkout-direction-thresholds",
        endpoint="https://checkout-direction-thresholds.example.test/plm",
        auth_secret="checkout-token",
    )
    session.commit()

    item_id = "item-checkout-direction-thresholds-1"
    service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=[item_id],
        idempotency_key="checkout-direction-thresholds-push",
    )
    service.enqueue_sync(
        site_id=site.id,
        direction="pull",
        document_ids=[item_id],
        idempotency_key="checkout-direction-thresholds-pull",
    )
    session.commit()

    direction_thresholds = {
        "push": {"pending": 1},
        "pull": {"pending": 0},
    }

    push_gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        direction="push",
        direction_thresholds=direction_thresholds,
        max_pending=0,
    )
    assert push_gate["effective_direction"] == "push"
    assert push_gate["thresholds"]["pending"] == 1
    assert push_gate["blocking"] is False
    assert push_gate["direction_thresholds"] == direction_thresholds

    pull_gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        direction="pull",
        direction_thresholds=direction_thresholds,
        max_pending=0,
    )
    assert pull_gate["effective_direction"] == "pull"
    assert pull_gate["thresholds"]["pending"] == 0
    assert pull_gate["blocking"] is True
    assert pull_gate["blocking_reasons"] == [
        {"status": "pending", "count": 1, "threshold": 0}
    ]

    with pytest.raises(ValueError, match="direction_thresholds keys must be push or pull"):
        service.evaluate_checkout_sync_gate(
            item_id=item_id,
            site_id=site.id,
            direction_thresholds={"sideways": {"pending": 1}},
        )


def test_document_multi_site_checkout_gate_direction_filter(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-checkout-direction",
        endpoint="https://checkout-direction.example.test/plm",
        auth_secret="checkout-token",
    )
    session.commit()

    item_id = "item-checkout-direction-1"
    service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=[item_id],
        idempotency_key="checkout-direction-push",
    )
    service.enqueue_sync(
        site_id=site.id,
        direction="pull",
        document_ids=[item_id],
        idempotency_key="checkout-direction-pull",
    )
    session.commit()

    # Without direction: sees both push + pull pending jobs
    all_gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
    )
    assert all_gate["direction"] is None
    assert all_gate["blocking_counts"]["pending"] == 2
    assert all_gate["blocking"] is True

    # With direction=push: sees only push job
    push_gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        direction="push",
        max_pending=0,
    )
    assert push_gate["direction"] == "push"
    assert push_gate["blocking_counts"]["pending"] == 1
    assert push_gate["blocking"] is True

    # With direction=pull: sees only pull job
    pull_gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        direction="pull",
        max_pending=0,
    )
    assert pull_gate["direction"] == "pull"
    assert pull_gate["blocking_counts"]["pending"] == 1
    assert pull_gate["blocking"] is True

    # Tolerant push threshold: push not blocking
    tolerant_push = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        direction="push",
        max_pending=1,
    )
    assert tolerant_push["blocking"] is False

    # Invalid direction raises ValueError
    with pytest.raises(ValueError, match="direction must be push or pull"):
        service.evaluate_checkout_sync_gate(
            item_id=item_id,
            site_id=site.id,
            direction="invalid",
        )


def test_document_multi_site_checkout_gate_defaults_to_site_direction(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-checkout-direction-default",
        endpoint="https://checkout-direction-default.example.test/plm",
        auth_secret="checkout-token",
        metadata_json={"direction": "push"},
    )
    session.commit()

    item_id = "item-checkout-direction-default-1"
    service.enqueue_sync(
        site_id=site.id,
        direction="push",
        document_ids=[item_id],
        idempotency_key="checkout-direction-default-push",
    )
    service.enqueue_sync(
        site_id=site.id,
        direction="pull",
        document_ids=[item_id],
        idempotency_key="checkout-direction-default-pull",
    )
    session.commit()

    gate = service.evaluate_checkout_sync_gate(
        item_id=item_id,
        site_id=site.id,
        max_pending=0,
    )

    assert gate["direction"] is None
    assert gate["effective_direction"] == "push"
    assert gate["blocking_counts"]["pending"] == 1
    assert gate["blocking"] is True
    assert len(gate["blocking_jobs"]) == 1
    assert gate["blocking_jobs"][0]["task_type"] == "document_sync_push"


def test_document_multi_site_probe_remote_site_supports_basic_auth_and_legacy_path(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-probe-basic",
        endpoint="https://mirror.example.test",
        auth_mode="basic",
        auth_secret="legacy-user:legacy-pass",
    )
    session.commit()

    calls = []

    class _Resp:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self.text = ""

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers=None, auth=None):
            calls.append({"url": url, "headers": headers or {}, "auth": auth})
            if str(url).endswith("/document_is_there/0"):
                return _Resp(200)
            return _Resp(404)

    with patch("yuantus.meta_engine.services.parallel_tasks_service.httpx.Client", _Client):
        result = service.probe_remote_site(site.id, timeout_s=1.0)

    assert result["status"] == "healthy"
    assert result["http_code"] == 200
    assert str(result["checked_target"]).endswith("/document_is_there/0")
    assert result["auth_mode"] == "basic"
    assert any(
        str(call["url"]).endswith("/document_is_there/0")
        and call["auth"] == ("legacy-user", "legacy-pass")
        for call in calls
    )


def test_document_multi_site_probe_remote_site_supports_custom_health_path_with_token(session):
    service = DocumentMultiSiteService(session)
    site = service.upsert_remote_site(
        name="site-probe-token",
        endpoint="https://token-probe.example.test",
        auth_mode="token",
        auth_secret="site-token-123",
        metadata_json={"health_path": "/readyz"},
    )
    session.commit()

    calls = []

    class _Resp:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self.text = ""

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers=None, auth=None):
            calls.append({"url": url, "headers": headers or {}, "auth": auth})
            if str(url).endswith("/readyz"):
                return _Resp(200)
            return _Resp(404)

    with patch("yuantus.meta_engine.services.parallel_tasks_service.httpx.Client", _Client):
        result = service.probe_remote_site(site.id, timeout_s=1.0)

    assert result["status"] == "healthy"
    assert result["http_code"] == 200
    assert str(result["checked_target"]).endswith("/readyz")
    assert result["auth_mode"] == "token"
    assert calls[0]["headers"].get("Authorization") == "Bearer site-token-123"
    assert calls[0]["auth"] is None


def test_eco_activity_validation_enforces_dependency_gate(session):
    service = ECOActivityValidationService(session)
    a1 = service.create_activity(eco_id="eco-1", name="design review")
    a2 = service.create_activity(
        eco_id="eco-1",
        name="quality review",
        depends_on_activity_ids=[a1.id],
    )
    session.commit()

    with pytest.raises(ValueError, match="Blocking dependencies"):
        service.transition_activity(activity_id=a2.id, to_status="completed", user_id=1)

    service.transition_activity(activity_id=a1.id, to_status="completed", user_id=1)
    service.transition_activity(activity_id=a2.id, to_status="completed", user_id=1)
    session.commit()

    blockers = service.blockers_for_eco("eco-1")
    assert blockers["total"] == 0
    events = service.recent_events("eco-1", limit=20)
    assert len(events) >= 4


def test_eco_activity_transition_state_machine_alias_and_evaluate(session):
    service = ECOActivityValidationService(session)
    a1 = service.create_activity(eco_id="eco-sm", name="prepare")
    a2 = service.create_activity(
        eco_id="eco-sm",
        name="apply",
        depends_on_activity_ids=[a1.id],
    )
    session.commit()

    blocked = service.evaluate_transition(activity_id=a2.id, to_status="done")
    assert blocked["to_status"] == "completed"
    assert blocked["can_transition"] is False
    assert blocked["reason_code"] == "blocking_dependencies"
    assert blocked["blockers"][0]["activity_id"] == a1.id

    with pytest.raises(ValueError, match="Blocking dependencies"):
        service.transition_activity(activity_id=a2.id, to_status="done", user_id=1)

    service.transition_activity(activity_id=a1.id, to_status="in_progress", user_id=1)
    service.transition_activity(activity_id=a1.id, to_status="done", user_id=1)
    service.transition_activity(activity_id=a2.id, to_status="in_progress", user_id=1)
    session.commit()
    refreshed_a2 = service.get_activity(a2.id)
    assert refreshed_a2 is not None
    assert refreshed_a2.status == "active"

    with pytest.raises(ValueError, match="Invalid transition: active -> pending"):
        service.transition_activity(activity_id=a2.id, to_status="draft", user_id=1)

    service.transition_activity(activity_id=a2.id, to_status="done", user_id=1)
    session.commit()
    done_a2 = service.get_activity(a2.id)
    assert done_a2 is not None
    assert done_a2.status == "completed"
    assert done_a2.closed_at is not None

    service.transition_activity(activity_id=a2.id, to_status="draft", user_id=1)
    session.commit()
    reopened_a2 = service.get_activity(a2.id)
    assert reopened_a2 is not None
    assert reopened_a2.status == "pending"
    assert reopened_a2.closed_at is None
    assert reopened_a2.closed_by_id is None

    with pytest.raises(ValueError, match="to_status must be one of"):
        service.evaluate_transition(activity_id=a2.id, to_status="unknown-status")


def test_eco_activity_bulk_transition_check_filters_missing_and_truncation(session):
    service = ECOActivityValidationService(session)
    parent = service.create_activity(eco_id="eco-bulk", name="parent", is_blocking=True)
    child = service.create_activity(
        eco_id="eco-bulk",
        name="child",
        depends_on_activity_ids=[parent.id],
        is_blocking=True,
    )
    non_blocking = service.create_activity(
        eco_id="eco-bulk",
        name="notify-only",
        is_blocking=False,
    )
    closed = service.create_activity(eco_id="eco-bulk", name="closed", is_blocking=True)
    service.transition_activity(activity_id=parent.id, to_status="done", user_id=1)
    service.transition_activity(activity_id=closed.id, to_status="done", user_id=1)
    session.commit()

    filtered = service.evaluate_transitions_bulk(
        "eco-bulk",
        to_status="in_progress",
        activity_ids=[child.id, parent.id, non_blocking.id, "missing-activity"],
        include_terminal=False,
        include_non_blocking=False,
        limit=10,
    )
    assert filtered["to_status"] == "active"
    assert filtered["selected_total"] == 4
    assert filtered["total"] == 1
    assert filtered["ready_total"] == 1
    assert filtered["blocked_total"] == 0
    assert filtered["invalid_total"] == 0
    assert filtered["noop_total"] == 0
    assert filtered["missing_total"] == 1
    assert filtered["excluded_total"] == 2
    assert filtered["missing_activity_ids"] == ["missing-activity"]
    assert filtered["truncated"] is False
    assert filtered["decisions"][0]["activity_id"] == child.id
    assert filtered["decisions"][0]["can_transition"] is True

    truncated = service.evaluate_transitions_bulk(
        "eco-bulk",
        to_status="done",
        include_terminal=True,
        include_non_blocking=True,
        limit=1,
    )
    assert truncated["total"] == 4
    assert truncated["ready_total"] == 2
    assert truncated["noop_total"] == 2
    assert truncated["truncated"] is True
    assert len(truncated["decisions"]) == 1


def test_eco_activity_bulk_transition_executes_dependency_chain_and_guard_truncation(session):
    service = ECOActivityValidationService(session)
    parent = service.create_activity(eco_id="eco-bulk-run", name="parent", is_blocking=True)
    child = service.create_activity(
        eco_id="eco-bulk-run",
        name="child",
        depends_on_activity_ids=[parent.id],
        is_blocking=True,
    )
    notify = service.create_activity(
        eco_id="eco-bulk-run",
        name="notify",
        is_blocking=False,
    )
    done = service.create_activity(eco_id="eco-bulk-run", name="done-before", is_blocking=True)
    service.transition_activity(activity_id=done.id, to_status="done", user_id=1)
    session.commit()

    result = service.transition_activities_bulk(
        "eco-bulk-run",
        to_status="done",
        activity_ids=[child.id, parent.id, notify.id, done.id],
        include_terminal=False,
        include_non_blocking=False,
        limit=20,
        user_id=9,
        reason="bulk-transition",
    )
    session.commit()

    assert result["to_status"] == "completed"
    assert result["selected_total"] == 4
    assert result["total"] == 2
    assert result["executed_total"] == 2
    assert result["noop_total"] == 0
    assert result["blocked_total"] == 0
    assert result["invalid_total"] == 0
    assert result["excluded_total"] == 2
    actions = {row["activity_id"]: row["action"] for row in result["decisions"]}
    assert actions[parent.id] == "executed"
    assert actions[child.id] == "executed"

    refreshed_parent = service.get_activity(parent.id)
    refreshed_child = service.get_activity(child.id)
    assert refreshed_parent is not None and refreshed_parent.status == "completed"
    assert refreshed_child is not None and refreshed_child.status == "completed"

    with pytest.raises(ValueError, match="bulk execution truncated by limit"):
        service.transition_activities_bulk(
            "eco-bulk-run",
            to_status="done",
            include_terminal=True,
            include_non_blocking=True,
            limit=1,
            user_id=9,
        )


def test_eco_activity_sla_classification_and_filters(session):
    service = ECOActivityValidationService(session)
    now = datetime(2026, 3, 5, 12, 0, 0)

    overdue = service.create_activity(
        eco_id="eco-sla",
        name="overdue-check",
        assignee_id=11,
        properties={"due_at": (now - timedelta(hours=2)).isoformat() + "Z"},
    )
    due_soon = service.create_activity(
        eco_id="eco-sla",
        name="due-soon-check",
        assignee_id=11,
        properties={"due_at": (now + timedelta(hours=3)).isoformat()},
    )
    on_track = service.create_activity(
        eco_id="eco-sla",
        name="on-track-check",
        assignee_id=12,
        properties={"due_at": (now + timedelta(hours=72)).isoformat()},
    )
    no_due = service.create_activity(
        eco_id="eco-sla",
        name="no-due-check",
        assignee_id=11,
    )
    closed = service.create_activity(
        eco_id="eco-sla",
        name="closed-check",
        assignee_id=11,
        properties={"due_at": (now - timedelta(hours=5)).isoformat()},
    )
    service.transition_activity(activity_id=closed.id, to_status="completed", user_id=11)
    session.commit()

    overview = service.activity_sla(
        "eco-sla",
        now=now,
        due_soon_hours=24,
        include_closed=False,
        limit=10,
    )
    assert overview["total"] == 4
    assert overview["overdue_total"] == 1
    assert overview["due_soon_total"] == 1
    assert overview["on_track_total"] == 1
    assert overview["no_due_date_total"] == 1
    assert overview["closed_total"] == 0
    assert overview["status_counts"] == {"pending": 4}
    names = [row["name"] for row in overview["activities"]]
    assert names[0] == "overdue-check"
    assert names[1] == "due-soon-check"
    assert "closed-check" not in names

    assignee_view = service.activity_sla(
        "eco-sla",
        now=now,
        due_soon_hours=24,
        assignee_id=11,
        include_closed=True,
        limit=10,
    )
    assignee_names = [row["name"] for row in assignee_view["activities"]]
    assert "on-track-check" not in assignee_names
    assert "closed-check" in assignee_names
    assert assignee_view["closed_total"] == 1

    limit_view = service.activity_sla(
        "eco-sla",
        now=now,
        due_soon_hours=24,
        include_closed=True,
        limit=2,
    )
    assert limit_view["total"] == 5
    assert limit_view["truncated"] is True
    assert len(limit_view["activities"]) == 2
    assert overdue.id == limit_view["activities"][0]["id"]
    assert due_soon.id == limit_view["activities"][1]["id"]


def test_eco_activity_sla_validates_window_and_limit(session):
    service = ECOActivityValidationService(session)
    service.create_activity(eco_id="eco-1", name="a")
    session.commit()

    with pytest.raises(ValueError, match="due_soon_hours must be between 1 and 720"):
        service.activity_sla("eco-1", due_soon_hours=0)
    with pytest.raises(ValueError, match="limit must be between 1 and 500"):
        service.activity_sla("eco-1", limit=0)


def test_eco_activity_sla_alerts_and_export(session):
    service = ECOActivityValidationService(session)
    now = datetime(2026, 3, 5, 12, 0, 0)

    service.create_activity(
        eco_id="eco-alert",
        name="overdue-blocking-a",
        is_blocking=True,
        properties={"due_at": (now - timedelta(hours=3)).isoformat()},
    )
    service.create_activity(
        eco_id="eco-alert",
        name="overdue-blocking-b",
        is_blocking=True,
        properties={"due_at": (now - timedelta(hours=2)).isoformat()},
    )
    service.create_activity(
        eco_id="eco-alert",
        name="due-soon-a",
        is_blocking=False,
        properties={"due_at": (now + timedelta(hours=1)).isoformat()},
    )
    service.create_activity(
        eco_id="eco-alert",
        name="due-soon-b",
        is_blocking=False,
        properties={"due_at": (now + timedelta(hours=2)).isoformat()},
    )
    session.commit()

    alerts = service.activity_sla_alerts(
        "eco-alert",
        now=now,
        due_soon_hours=24,
        overdue_rate_warn=0.2,
        due_soon_count_warn=1,
        blocking_overdue_warn=1,
    )
    assert alerts["status"] == "warning"
    codes = {row["code"] for row in alerts["alerts"]}
    assert "eco_activity_sla_overdue_rate_high" in codes
    assert "eco_activity_sla_due_soon_count_high" in codes
    assert "eco_activity_sla_blocking_overdue_high" in codes
    assert alerts["metrics"]["open_total"] == 4
    assert alerts["metrics"]["overdue_total"] == 2
    assert alerts["metrics"]["due_soon_total"] == 2
    assert alerts["metrics"]["blocking_overdue_total"] == 2

    exported_json = service.export_activity_sla_alerts(
        "eco-alert",
        now=now,
        due_soon_hours=24,
        overdue_rate_warn=0.2,
        due_soon_count_warn=1,
        blocking_overdue_warn=1,
        export_format="json",
    )
    assert exported_json["filename"] == "eco-activity-sla-alerts.json"
    parsed = json.loads(exported_json["content"].decode("utf-8"))
    assert parsed["status"] == "warning"

    exported_csv = service.export_activity_sla_alerts(
        "eco-alert",
        now=now,
        due_soon_hours=24,
        overdue_rate_warn=0.2,
        due_soon_count_warn=1,
        blocking_overdue_warn=1,
        export_format="csv",
    )
    assert exported_csv["filename"] == "eco-activity-sla-alerts.csv"
    assert "alert_code" in exported_csv["content"].decode("utf-8")

    exported_md = service.export_activity_sla_alerts(
        "eco-alert",
        now=now,
        due_soon_hours=24,
        overdue_rate_warn=0.2,
        due_soon_count_warn=1,
        blocking_overdue_warn=1,
        export_format="md",
    )
    assert exported_md["filename"] == "eco-activity-sla-alerts.md"
    assert "# ECO Activity SLA Alerts" in exported_md["content"].decode("utf-8")

    with pytest.raises(ValueError, match="overdue_rate_warn must be between 0 and 1"):
        service.activity_sla_alerts("eco-alert", overdue_rate_warn=1.2)
    with pytest.raises(ValueError, match="due_soon_count_warn must be between 0 and 100000"):
        service.activity_sla_alerts("eco-alert", due_soon_count_warn=-1)
    with pytest.raises(ValueError, match="export_format must be json, csv or md"):
        service.export_activity_sla_alerts("eco-alert", export_format="xlsx")


def test_workflow_custom_actions_emit_event_and_create_job(session):
    service = WorkflowCustomActionService(session)
    service.create_rule(
        name="eco-before-event",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "eco.transition"},
        fail_strategy="warn",
    )
    service.create_rule(
        name="eco-before-job",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="create_job",
        action_params={"task_type": "workflow_notify"},
        fail_strategy="block",
    )
    session.commit()

    runs = service.evaluate_transition(
        object_id="eco-1",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        context={"source": "test"},
    )
    session.commit()

    assert len(runs) == 2
    assert all(run.status == "completed" for run in runs)
    jobs = session.query(ConversionJob).all()
    assert len(jobs) == 1
    assert jobs[0].task_type == "workflow_notify"


def test_workflow_custom_actions_order_conflicts_and_result_codes(session):
    service = WorkflowCustomActionService(session)
    low = service.create_rule(
        name="rule-z-low",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "z", "priority": 50},
        fail_strategy="warn",
    )
    high = service.create_rule(
        name="rule-a-high",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "a", "priority": 10},
        fail_strategy="warn",
    )
    session.commit()

    assert isinstance(high.action_params, dict)
    assert int(high.action_params.get("priority") or 0) == 10
    assert int((high.action_params.get("conflict_scope") or {}).get("count") or 0) >= 1

    runs = service.evaluate_transition(
        object_id="eco-order-1",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        context={"source": "order-test"},
    )
    session.commit()

    assert [run.rule_id for run in runs] == [high.id, low.id]
    assert all((run.result or {}).get("result_code") == "OK" for run in runs)
    assert (runs[0].result or {}).get("execution", {}).get("order") == 1
    assert (runs[1].result or {}).get("execution", {}).get("order") == 2

    retry_rule = service.create_rule(
        name="rule-retry-fail",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"priority": 5, "max_retries": 2},
        fail_strategy="retry",
    )
    session.commit()

    with patch.object(service, "_run_action", side_effect=RuntimeError("boom")):
        runs = service.evaluate_transition(
            object_id="eco-order-2",
            target_object="ECO",
            from_state="draft",
            to_state="progress",
            trigger_phase="before",
            context={"source": "retry-test"},
        )
    session.commit()

    target_run = [run for run in runs if run.rule_id == retry_rule.id][0]
    assert target_run.status == "failed"
    assert target_run.attempts == 3
    assert (target_run.result or {}).get("result_code") == "RETRY_EXHAUSTED"


def test_workflow_custom_actions_timeout_is_enforced(session):
    service = WorkflowCustomActionService(session)
    service.create_rule(
        name="rule-timeout-warn",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"priority": 1, "timeout_s": 0.01},
        fail_strategy="warn",
    )
    session.commit()

    def _slow_action(**kwargs):
        _ = kwargs
        return {"ok": True}

    with patch("time.monotonic", side_effect=[0.0, 0.02]):
        with patch.object(service, "_run_action", side_effect=_slow_action):
            runs = service.evaluate_transition(
                object_id="eco-timeout-1",
                target_object="ECO",
                from_state="draft",
                to_state="progress",
                trigger_phase="before",
                context={"source": "timeout-test"},
            )
    session.commit()

    assert len(runs) == 1
    assert runs[0].status == "warning"
    assert (runs[0].result or {}).get("result_code") == "WARN"


def test_workflow_custom_actions_match_runtime_scope_predicates(session):
    service = WorkflowCustomActionService(session)
    generic = service.create_rule(
        name="rule-generic",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "generic", "priority": 40},
        fail_strategy="warn",
    )
    scoped = service.create_rule(
        name="rule-workflow-map",
        target_object="ECO",
        workflow_map_id="wf-map-1",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "scoped", "priority": 10},
        fail_strategy="warn",
    )
    stage = service.create_rule(
        name="rule-stage",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "stage", "priority": 20},
        match_predicates={"stage_id": "stage-2"},
        fail_strategy="warn",
    )
    priority = service.create_rule(
        name="rule-priority",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "priority", "priority": 30},
        match_predicates={"eco_priority": "urgent"},
        fail_strategy="warn",
    )
    session.commit()

    runs = service.evaluate_transition(
        object_id="eco-runtime-1",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        context={
            "workflow_map_id": "wf-map-1",
            "stage_id": "stage-2",
            "eco_priority": "urgent",
        },
    )
    session.commit()

    assert [run.rule_id for run in runs] == [scoped.id, stage.id, priority.id, generic.id]

    filtered = service.evaluate_transition(
        object_id="eco-runtime-2",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        context={
            "workflow_map_id": "wf-map-2",
            "stage_id": "stage-9",
            "eco_priority": "normal",
        },
    )
    session.commit()

    assert [run.rule_id for run in filtered] == [generic.id]


def test_workflow_custom_actions_validate_match_predicates(session):
    service = WorkflowCustomActionService(session)

    with pytest.raises(
        ValueError,
        match="match_predicates only supports: actor_roles, eco_priority, eco_type, product_id, stage_id",
    ):
        service.create_rule(
            name="rule-invalid-predicate",
            target_object="ECO",
            from_state="draft",
            to_state="progress",
            trigger_phase="before",
            action_type="emit_event",
            match_predicates={"actor_role": "qa"},
        )

    with pytest.raises(
        ValueError,
        match="match_predicates.eco_priority must be one of: low, normal, high, urgent",
    ):
        service.create_rule(
            name="rule-invalid-priority",
            target_object="ECO",
            from_state="draft",
            to_state="progress",
            trigger_phase="before",
            action_type="emit_event",
            match_predicates={"eco_priority": "critical"},
        )

    with pytest.raises(
        ValueError, match="match_predicates.eco_type must be one of: bom, product, document"
    ):
        service.create_rule(
            name="rule-invalid-eco-type",
            target_object="ECO",
            from_state="draft",
            to_state="progress",
            trigger_phase="before",
            action_type="emit_event",
            match_predicates={"eco_type": "service"},
        )


def test_workflow_custom_actions_match_context_predicates(session):
    service = WorkflowCustomActionService(session)
    actor_rule = service.create_rule(
        name="rule-actor-role",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "actor", "priority": 10},
        match_predicates={"actor_roles": ["planner", "qa"]},
        fail_strategy="warn",
    )
    product_rule = service.create_rule(
        name="rule-product",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "product", "priority": 20},
        match_predicates={"product_id": "item-1"},
        fail_strategy="warn",
    )
    type_rule = service.create_rule(
        name="rule-eco-type",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "type", "priority": 30},
        match_predicates={"eco_type": "document"},
        fail_strategy="warn",
    )
    session.commit()

    runs = service.evaluate_transition(
        object_id="eco-context-1",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        context={
            "actor_roles": ["Engineer", "Planner"],
            "product_id": "item-1",
            "eco_type": "document",
        },
    )
    session.commit()

    assert [run.rule_id for run in runs] == [actor_rule.id, product_rule.id, type_rule.id]

    filtered = service.evaluate_transition(
        object_id="eco-context-2",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        context={
            "actor_roles": ["engineer"],
            "product_id": "item-9",
            "eco_type": "bom",
        },
    )
    session.commit()

    assert filtered == []


def test_consumption_plan_variance_dashboard(session):
    service = ConsumptionPlanService(session)
    plan = service.create_plan(name="plan-1", planned_quantity=10.0, uom="ea")
    service.add_actual(plan_id=plan.id, actual_quantity=6.0, source_type="workorder")
    service.add_actual(plan_id=plan.id, actual_quantity=5.0, source_type="report")
    session.commit()

    variance = service.variance(plan.id)
    assert variance["planned_quantity"] == 10.0
    assert variance["actual_quantity"] == 11.0
    assert variance["delta_quantity"] == 1.0
    assert variance["records"] == 2

    dashboard = service.dashboard()
    assert dashboard["total"] == 1
    assert dashboard["plans"][0]["plan_id"] == plan.id


def test_consumption_template_versioning_activation_and_impact_preview(session):
    service = ConsumptionPlanService(session)
    version_v1 = service.create_template_version(
        template_key="tpl-motor",
        name="motor-plan-v1",
        planned_quantity=10.0,
        version_label="v1",
        activate=True,
    )
    version_v2 = service.create_template_version(
        template_key="tpl-motor",
        name="motor-plan-v2",
        planned_quantity=12.0,
        version_label="v2",
        activate=True,
    )
    session.commit()

    rows = service.list_template_versions("tpl-motor")
    assert len(rows) == 2
    active_rows = [row for row in rows if (row.get("template") or {}).get("is_active")]
    assert len(active_rows) == 1
    assert active_rows[0]["id"] == version_v2.id

    switched = service.set_template_version_state(version_v1.id, activate=True)
    session.commit()
    assert switched.state == "active"

    refreshed = service.list_template_versions("tpl-motor")
    active_rows = [row for row in refreshed if (row.get("template") or {}).get("is_active")]
    assert len(active_rows) == 1
    assert active_rows[0]["id"] == version_v1.id

    impact = service.preview_template_impact(
        template_key="tpl-motor",
        planned_quantity=15.0,
    )
    assert impact["summary"]["versions_total"] == 2
    assert impact["summary"]["baseline_quantity"] == 10.0
    assert impact["summary"]["delta_quantity"] == 5.0
    assert impact["active_version"]["id"] == version_v1.id

    plain = service.create_plan(name="plain-plan", planned_quantity=5.0)
    with pytest.raises(ValueError, match="not a template version"):
        service.set_template_version_state(plain.id, activate=True)


def test_breakage_metrics_include_repeat_rate_and_hotspot(session):
    service = BreakageIncidentService(session)
    service.create_incident(
        description="bearing crack",
        product_item_id="p-1",
        bom_id="bom-root-1",
        bom_line_item_id="bom-1",
        routing_id="routing-1",
        mbom_id="mbom-1",
        severity="high",
        batch_code="b-1",
        responsibility="supplier-a",
    )
    service.create_incident(
        description="bearing crack",
        product_item_id="p-1",
        bom_id="bom-root-1",
        bom_line_item_id="bom-1",
        routing_id="routing-1",
        mbom_id="mbom-1",
        severity="high",
        batch_code="b-1",
        responsibility="supplier-a",
    )
    service.create_incident(
        description="wire short",
        product_item_id="p-2",
        bom_id="bom-root-2",
        bom_line_item_id="bom-2",
        routing_id="routing-2",
        mbom_id="mbom-2",
        severity="medium",
        batch_code="b-2",
        responsibility="line-b",
    )
    session.commit()

    metrics = service.metrics(
        product_item_id="p-1",
        bom_line_item_id="bom-1",
        responsibility="supplier-a",
        trend_window_days=14,
        page=1,
        page_size=1,
    )
    assert metrics["filters"]["product_item_id"] == "p-1"
    assert metrics["filters"]["bom_line_item_id"] == "bom-1"
    assert metrics["filters"]["responsibility"] == "supplier-a"
    assert metrics["pagination"]["page_size"] == 1
    assert metrics["pagination"]["total"] == 2
    assert len(metrics["incidents"]) == 1
    assert metrics["trend_window_days"] == 14
    assert len(metrics["trend"]) == 14
    assert metrics["total"] == 2
    assert metrics["repeated_event_count"] == 2
    assert metrics["repeated_failure_rate"] > 0
    assert metrics["hotspot_components"][0]["bom_line_item_id"] == "bom-1"
    assert metrics["by_responsibility"]["supplier-a"] == 2
    assert metrics["by_product_item"]["p-1"] == 2
    assert metrics["by_batch_code"]["b-1"] == 2
    assert metrics["by_bom_line_item"]["bom-1"] == 2
    assert metrics["by_bom_id"]["bom-root-1"] == 2
    assert metrics["by_mbom_id"]["mbom-1"] == 2
    assert metrics["by_routing_id"]["routing-1"] == 2
    assert metrics["top_product_items"][0]["product_item_id"] == "p-1"
    assert metrics["top_batch_codes"][0]["batch_code"] == "b-1"
    assert metrics["top_bom_line_items"][0]["bom_line_item_id"] == "bom-1"
    assert metrics["top_bom_ids"][0]["bom_id"] == "bom-root-1"
    assert metrics["top_mbom_ids"][0]["mbom_id"] == "mbom-1"
    assert metrics["top_routing_ids"][0]["routing_id"] == "routing-1"


def test_breakage_metrics_rejects_invalid_trend_window(session):
    service = BreakageIncidentService(session)
    with pytest.raises(ValueError, match="trend_window_days"):
        service.metrics(trend_window_days=10)


def test_breakage_metrics_groups_supports_group_by_and_pagination(session):
    service = BreakageIncidentService(session)
    service.create_incident(
        description="group-a-1",
        product_item_id="p-g-1",
        bom_id="bom-root-g-1",
        bom_line_item_id="bom-g-1",
        routing_id="routing-g-1",
        mbom_id="mbom-g-1",
        batch_code="b-g-1",
        responsibility="supplier-a",
    )
    service.create_incident(
        description="group-a-2",
        product_item_id="p-g-1",
        bom_id="bom-root-g-1",
        bom_line_item_id="bom-g-1",
        routing_id="routing-g-1",
        mbom_id="mbom-g-1",
        batch_code="b-g-1",
        responsibility="supplier-a",
    )
    service.create_incident(
        description="group-b-1",
        product_item_id="p-g-2",
        bom_id="bom-root-g-2",
        bom_line_item_id="bom-g-2",
        routing_id="routing-g-2",
        mbom_id="mbom-g-2",
        batch_code="b-g-2",
        responsibility="supplier-b",
    )
    session.commit()

    groups = service.metrics_groups(
        group_by="product_item_id",
        trend_window_days=14,
        page=1,
        page_size=1,
    )
    assert groups["group_by"] == "product_item_id"
    assert groups["total_groups"] == 2
    assert groups["pagination"]["page_size"] == 1
    assert groups["pagination"]["total"] == 2
    assert len(groups["groups"]) == 1
    assert groups["groups"][0]["group_value"] == "p-g-1"
    assert groups["groups"][0]["count"] == 2

    groups_batch = service.metrics_groups(group_by="batch_code")
    assert groups_batch["groups"][0]["group_value"] == "b-g-1"
    assert groups_batch["groups"][0]["count"] == 2

    groups_bom_line = service.metrics_groups(group_by="bom_line_item_id")
    assert groups_bom_line["groups"][0]["group_value"] == "bom-g-1"
    assert groups_bom_line["groups"][0]["count"] == 2

    groups_bom = service.metrics_groups(group_by="bom_id")
    assert groups_bom["groups"][0]["group_value"] == "bom-root-g-1"
    assert groups_bom["groups"][0]["count"] == 2

    groups_mbom = service.metrics_groups(group_by="mbom_id")
    assert groups_mbom["groups"][0]["group_value"] == "mbom-g-1"
    assert groups_mbom["groups"][0]["count"] == 2

    groups_routing = service.metrics_groups(group_by="routing_id")
    assert groups_routing["groups"][0]["group_value"] == "routing-g-1"
    assert groups_routing["groups"][0]["count"] == 2

    groups_filtered = service.metrics_groups(
        group_by="product_item_id",
        bom_line_item_id="bom-g-2",
    )
    assert groups_filtered["total_groups"] == 1
    assert groups_filtered["groups"][0]["group_value"] == "p-g-2"
    assert groups_filtered["filters"]["bom_line_item_id"] == "bom-g-2"


def test_breakage_metrics_groups_rejects_invalid_group_by(session):
    service = BreakageIncidentService(session)
    with pytest.raises(ValueError) as exc_info:
        service.metrics_groups(group_by="invalid")
    error = str(exc_info.value)
    assert "group_by must be one of" in error
    assert "bom_id" in error
    assert "mbom_id" in error
    assert "routing_id" in error


def test_breakage_metrics_export_json_csv_md(session):
    service = BreakageIncidentService(session)
    service.create_incident(
        description="export-bearing-crack",
        product_item_id="p-exp-1",
        bom_line_item_id="bom-exp-1",
        production_order_id="routing-exp-1",
        version_id="mbom-exp-1",
        severity="high",
        batch_code="batch-exp-1",
        responsibility="supplier-exp",
    )
    session.add(
        ReportLocaleProfile(
            id="rp-breakage-zh",
            name="Breakage ZH",
            lang="zh_CN",
            timezone="Asia/Shanghai",
            report_type="breakage_metrics",
            is_default=True,
        )
    )
    session.commit()

    exported_json = service.export_metrics(
        product_item_id="p-exp-1",
        bom_line_item_id="bom-exp-1",
        responsibility="supplier-exp",
        trend_window_days=14,
        export_format="json",
    )
    assert exported_json["media_type"] == "application/json"
    assert exported_json["filename"] == "breakage-metrics.json"
    assert '"total": 1' in exported_json["content"].decode("utf-8")
    assert '"by_product_item": {' in exported_json["content"].decode("utf-8")
    exported_json_locale = service.export_metrics(
        product_item_id="p-exp-1",
        bom_line_item_id="bom-exp-1",
        responsibility="supplier-exp",
        trend_window_days=14,
        export_format="json",
        report_lang="zh_CN",
        report_type="breakage_metrics",
        locale_profile_id="rp-breakage-zh",
    )
    exported_json_locale_payload = json.loads(
        exported_json_locale["content"].decode("utf-8")
    )
    assert exported_json_locale_payload["locale"]["id"] == "rp-breakage-zh"
    assert exported_json_locale_payload["locale"]["lang"] == "zh_CN"

    exported_csv = service.export_metrics(
        product_item_id="p-exp-1",
        bom_line_item_id="bom-exp-1",
        responsibility="supplier-exp",
        trend_window_days=14,
        export_format="csv",
    )
    csv_text = exported_csv["content"].decode("utf-8")
    assert exported_csv["media_type"] == "text/csv"
    assert exported_csv["filename"] == "breakage-metrics.csv"
    assert "date,count,total,repeated_event_count,repeated_failure_rate" in csv_text
    assert "bom_line_item_id_filter" in csv_text
    assert "bom-exp-1" in csv_text
    assert "supplier-exp" in csv_text

    exported_md = service.export_metrics(
        product_item_id="p-exp-1",
        bom_line_item_id="bom-exp-1",
        responsibility="supplier-exp",
        trend_window_days=14,
        export_format="md",
    )
    md_text = exported_md["content"].decode("utf-8")
    assert exported_md["media_type"] == "text/markdown"
    assert exported_md["filename"] == "breakage-metrics.md"
    assert md_text.startswith("# Breakage Metrics")
    assert "| Date | Count |" in md_text
    assert "top_product_items" in md_text
    assert "top_batch_codes" in md_text
    assert "top_bom_line_items" in md_text
    assert "top_bom_ids" in md_text
    assert "top_mbom_ids" in md_text
    assert "top_routing_ids" in md_text
    exported_md_locale = service.export_metrics(
        product_item_id="p-exp-1",
        bom_line_item_id="bom-exp-1",
        responsibility="supplier-exp",
        trend_window_days=14,
        export_format="md",
        report_lang="zh_CN",
        report_type="breakage_metrics",
        locale_profile_id="rp-breakage-zh",
    )
    md_locale_text = exported_md_locale["content"].decode("utf-8")
    assert "## Locale" in md_locale_text
    assert "rp-breakage-zh" in md_locale_text


def test_breakage_metrics_export_rejects_invalid_format(session):
    service = BreakageIncidentService(session)
    with pytest.raises(ValueError, match="export_format must be json, csv or md"):
        service.export_metrics(export_format="xlsx")


def test_breakage_metrics_groups_export_json_csv_md(session):
    service = BreakageIncidentService(session)
    service.create_incident(
        description="group-export-a",
        product_item_id="p-g-exp-1",
        bom_line_item_id="bom-g-exp-1",
        batch_code="b-g-exp-1",
        responsibility="supplier-g-exp",
    )
    service.create_incident(
        description="group-export-b",
        product_item_id="p-g-exp-1",
        bom_line_item_id="bom-g-exp-1",
        batch_code="b-g-exp-1",
        responsibility="supplier-g-exp",
    )
    session.add(
        ReportLocaleProfile(
            id="rp-breakage-groups-zh",
            name="Breakage Groups ZH",
            lang="zh_CN",
            timezone="Asia/Shanghai",
            report_type="breakage_metrics_groups",
            is_default=True,
        )
    )
    session.commit()

    exported_json = service.export_metrics_groups(
        group_by="product_item_id",
        responsibility="supplier-g-exp",
        trend_window_days=14,
        export_format="json",
    )
    assert exported_json["media_type"] == "application/json"
    assert exported_json["filename"] == "breakage-metrics-groups.json"
    assert '"group_by": "product_item_id"' in exported_json["content"].decode("utf-8")
    assert '"total_groups": 1' in exported_json["content"].decode("utf-8")
    exported_json_locale = service.export_metrics_groups(
        group_by="product_item_id",
        responsibility="supplier-g-exp",
        trend_window_days=14,
        export_format="json",
        report_lang="zh_CN",
        report_type="breakage_metrics_groups",
        locale_profile_id="rp-breakage-groups-zh",
    )
    exported_groups_json_payload = json.loads(
        exported_json_locale["content"].decode("utf-8")
    )
    assert exported_groups_json_payload["locale"]["id"] == "rp-breakage-groups-zh"
    assert exported_groups_json_payload["locale"]["lang"] == "zh_CN"

    exported_csv = service.export_metrics_groups(
        group_by="product_item_id",
        responsibility="supplier-g-exp",
        trend_window_days=14,
        export_format="csv",
    )
    csv_text = exported_csv["content"].decode("utf-8")
    assert exported_csv["media_type"] == "text/csv"
    assert exported_csv["filename"] == "breakage-metrics-groups.csv"
    assert "group_by,group_value,count,total_groups,trend_window_days" in csv_text
    assert "p-g-exp-1,2,1,14" in csv_text

    exported_md = service.export_metrics_groups(
        group_by="product_item_id",
        responsibility="supplier-g-exp",
        trend_window_days=14,
        export_format="md",
    )
    md_text = exported_md["content"].decode("utf-8")
    assert exported_md["media_type"] == "text/markdown"
    assert exported_md["filename"] == "breakage-metrics-groups.md"
    assert md_text.startswith("# Breakage Metrics Groups")
    assert "| Group By | Group Value | Count |" in md_text
    assert "product_item_id" in md_text
    assert "p-g-exp-1" in md_text
    exported_md_locale = service.export_metrics_groups(
        group_by="product_item_id",
        responsibility="supplier-g-exp",
        trend_window_days=14,
        export_format="md",
        report_lang="zh_CN",
        report_type="breakage_metrics_groups",
        locale_profile_id="rp-breakage-groups-zh",
    )
    md_groups_locale_text = exported_md_locale["content"].decode("utf-8")
    assert "## Locale" in md_groups_locale_text
    assert "rp-breakage-groups-zh" in md_groups_locale_text

    exported_bom_line_json = service.export_metrics_groups(
        group_by="bom_line_item_id",
        responsibility="supplier-g-exp",
        trend_window_days=14,
        export_format="json",
    )
    assert '"group_by": "bom_line_item_id"' in exported_bom_line_json["content"].decode(
        "utf-8"
    )


def test_breakage_metrics_groups_export_rejects_invalid_format(session):
    service = BreakageIncidentService(session)
    with pytest.raises(ValueError, match="export_format must be json, csv or md"):
        service.export_metrics_groups(export_format="xlsx")


def test_breakage_incidents_export_supports_bom_line_filter_and_formats(session):
    service = BreakageIncidentService(session)
    incident_a = service.create_incident(
        description="incident-a",
        product_item_id="p-list-1",
        bom_id="bom-root-list-1",
        mbom_id="mbom-list-1",
        routing_id="routing-list-1",
        bom_line_item_id="bom-list-1",
        batch_code="batch-list-1",
        responsibility="supplier-list",
    )
    service.create_incident(
        description="incident-b",
        product_item_id="p-list-2",
        bom_line_item_id="bom-list-2",
        batch_code="batch-list-2",
        responsibility="supplier-list",
    )
    job = service.enqueue_helpdesk_stub_sync(
        incident_a.id,
        user_id=7,
        provider="jira",
    )
    session.commit()
    service.record_helpdesk_sync_result(
        incident_a.id,
        sync_status="completed",
        job_id=job.id,
        external_ticket_id="HD-LIST-1",
        user_id=7,
    )
    service.apply_helpdesk_ticket_update(
        incident_a.id,
        provider_ticket_status="assigned",
        job_id=job.id,
        provider_assignee="qa-list-owner",
        user_id=7,
    )
    session.add(
        ReportLocaleProfile(
            id="rp-breakage-incidents-zh",
            name="Breakage Incidents ZH",
            lang="zh_CN",
            timezone="Asia/Shanghai",
            report_type="breakage_incidents",
            is_default=True,
        )
    )
    session.commit()

    listed = service.list_incidents(bom_line_item_id="bom-list-1")
    assert len(listed) == 1
    assert listed[0].id == incident_a.id

    exported_json = service.export_incidents(
        bom_line_item_id="bom-list-1",
        page=1,
        page_size=10,
        export_format="json",
    )
    assert exported_json["media_type"] == "application/json"
    assert exported_json["filename"] == "breakage-incidents.json"
    assert '"bom_line_item_id": "bom-list-1"' in exported_json["content"].decode("utf-8")
    assert '"incident_code": "BRK-000001"' in exported_json["content"].decode("utf-8")
    assert '"bom_id": "bom-root-list-1"' in exported_json["content"].decode("utf-8")
    assert '"mbom_id": "mbom-list-1"' in exported_json["content"].decode("utf-8")
    assert '"routing_id": "routing-list-1"' in exported_json["content"].decode("utf-8")
    assert '"helpdesk_ticket_summary"' in exported_json["content"].decode("utf-8")
    assert '"external_ticket_id": "HD-LIST-1"' in exported_json["content"].decode(
        "utf-8"
    )
    assert '"provider_assignee": "qa-list-owner"' in exported_json["content"].decode(
        "utf-8"
    )
    exported_json_locale = service.export_incidents(
        bom_line_item_id="bom-list-1",
        page=1,
        page_size=10,
        export_format="json",
        report_lang="zh_CN",
        report_type="breakage_incidents",
        locale_profile_id="rp-breakage-incidents-zh",
    )
    exported_json_locale_payload = json.loads(
        exported_json_locale["content"].decode("utf-8")
    )
    assert exported_json_locale_payload["locale"]["id"] == "rp-breakage-incidents-zh"
    assert exported_json_locale_payload["locale"]["lang"] == "zh_CN"

    exported_csv = service.export_incidents(
        bom_line_item_id="bom-list-1",
        page=1,
        page_size=10,
        export_format="csv",
    )
    csv_text = exported_csv["content"].decode("utf-8")
    assert exported_csv["media_type"] == "text/csv"
    assert exported_csv["filename"] == "breakage-incidents.csv"
    assert "bom_line_item_id_filter" in csv_text
    assert "incident_code" in csv_text
    assert "bom_id" in csv_text
    assert "mbom_id" in csv_text
    assert "routing_id" in csv_text
    assert "helpdesk_external_ticket_id" in csv_text
    assert "helpdesk_provider_assignee" in csv_text
    assert "HD-LIST-1" in csv_text
    assert "qa-list-owner" in csv_text
    assert "bom-list-1" in csv_text

    exported_md = service.export_incidents(
        bom_line_item_id="bom-list-1",
        page=1,
        page_size=10,
        export_format="md",
    )
    md_text = exported_md["content"].decode("utf-8")
    assert exported_md["media_type"] == "text/markdown"
    assert exported_md["filename"] == "breakage-incidents.md"
    assert md_text.startswith("# Breakage Incidents")
    assert "BRK-000001" in md_text
    assert "bom-root-list-1" in md_text
    assert "bom-list-1" in md_text
    assert "HD-LIST-1" in md_text
    assert "qa-list-owner" in md_text
    exported_md_locale = service.export_incidents(
        bom_line_item_id="bom-list-1",
        page=1,
        page_size=10,
        export_format="md",
        report_lang="zh_CN",
        report_type="breakage_incidents",
        locale_profile_id="rp-breakage-incidents-zh",
    )
    md_locale_text = exported_md_locale["content"].decode("utf-8")
    assert "## Locale" in md_locale_text
    assert "rp-breakage-incidents-zh" in md_locale_text


def test_breakage_incidents_export_rejects_invalid_format(session):
    service = BreakageIncidentService(session)
    with pytest.raises(ValueError, match="export_format must be json, csv or md"):
        service.export_incidents(export_format="xlsx")


def test_breakage_helpdesk_stub_sync_enqueue(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="sensor drift",
        product_item_id="p-3",
        bom_line_item_id="bom-3",
        severity="low",
    )
    session.commit()

    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=11,
        metadata_json={"channel": "qa"},
    )
    session.commit()

    assert job.task_type == "breakage_helpdesk_sync_stub"
    assert job.payload["incident_id"] == incident.id
    assert job.payload["metadata"]["channel"] == "qa"


def test_breakage_helpdesk_stub_sync_enqueue_supports_provider_idempotency_retry(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="sensor drift idempotent",
        product_item_id="p-3",
        bom_line_item_id="bom-3",
        severity="low",
    )
    session.commit()

    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=11,
        metadata_json={"channel": "qa"},
        provider="zendesk",
        idempotency_key="idem-qa-1",
        retry_max_attempts=5,
    )
    session.commit()
    duplicate = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=11,
        metadata_json={"channel": "qa"},
        provider="zendesk",
        idempotency_key="idem-qa-1",
        retry_max_attempts=5,
    )
    session.commit()

    assert job.id == duplicate.id
    assert job.task_type == "breakage_helpdesk_sync_stub"
    assert job.max_attempts == 5
    assert job.payload["integration"]["provider"] == "zendesk"
    assert job.payload["integration"]["idempotency_key"] == "idem-qa-1"
    assert job.payload["helpdesk_sync"]["provider"] == "zendesk"
    assert job.payload["helpdesk_sync"]["idempotency_key"] == "idem-qa-1"


def test_breakage_helpdesk_sync_status_and_result_flow(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="connector melt",
        product_item_id="p-hd-1",
        bom_line_item_id="bom-hd-1",
    )
    session.commit()

    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=15,
        metadata_json={"channel": "ops"},
    )
    session.commit()

    pending = service.get_helpdesk_sync_status(incident.id)
    assert pending["incident_id"] == incident.id
    assert pending["sync_status"] in {"queued", "pending"}
    assert pending["last_job"]["id"] == job.id
    assert pending["last_job"]["retry_budget"]["max_attempts"] >= 1

    updated = service.record_helpdesk_sync_result(
        incident.id,
        sync_status="completed",
        job_id=job.id,
        external_ticket_id="HD-1001",
        metadata_json={"channel": "ops"},
        user_id=15,
    )
    session.commit()
    assert updated["sync_status"] == "completed"
    assert updated["external_ticket_id"] == "HD-1001"
    assert updated["last_job"]["status"] == "completed"

    completed = service.get_helpdesk_sync_status(incident.id)
    assert completed["sync_status"] == "completed"
    assert completed["external_ticket_id"] == "HD-1001"


def test_breakage_helpdesk_execute_supports_failure_category_and_retry(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="connector melt execute",
        product_item_id="p-hd-exec-1",
        bom_line_item_id="bom-hd-exec-1",
    )
    session.commit()
    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=15,
        metadata_json={"channel": "ops"},
        provider="jira",
        idempotency_key="hd-exec-1",
    )
    session.commit()

    failed = service.execute_helpdesk_sync(
        incident.id,
        simulate_status="failed",
        job_id=job.id,
        error_code="timeout",
        error_message="upstream timeout",
        user_id=15,
    )
    session.commit()
    assert failed["sync_status"] == "failed"
    assert failed["last_job"]["failure_category"] == "transient"
    assert failed["last_job"]["status"] == "failed"
    assert failed["last_job"]["attempt_count"] == 1

    completed = service.execute_helpdesk_sync(
        incident.id,
        simulate_status="completed",
        job_id=job.id,
        external_ticket_id="HD-RETRY-1",
        user_id=15,
    )
    session.commit()
    assert completed["sync_status"] == "completed"
    assert completed["external_ticket_id"] == "HD-RETRY-1"
    assert completed["last_job"]["provider"] == "jira"
    assert completed["last_job"]["idempotency_key"] == "hd-exec-1"
    assert completed["last_job"]["attempt_count"] == 2


def test_breakage_helpdesk_ticket_update_maps_provider_status_to_incident_and_job(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="ticket-update-map",
        product_item_id="p-hd-ticket-map",
        bom_line_item_id="bom-hd-ticket-map",
    )
    session.commit()
    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=15,
        provider="jira",
        idempotency_key="hd-ticket-map-1",
    )
    session.commit()

    updated = service.apply_helpdesk_ticket_update(
        incident.id,
        provider_ticket_status="done",
        job_id=job.id,
        external_ticket_id="HD-MAP-1",
        provider_assignee="qa-owner",
        provider_payload={"source": "jira-webhook"},
        user_id=15,
    )
    session.commit()

    assert updated["incident_status"] == "resolved"
    assert updated["incident_responsibility"] == "qa-owner"
    assert updated["sync_status"] == "completed"
    assert updated["external_ticket_id"] == "HD-MAP-1"
    assert updated["last_job"]["status"] == "completed"
    assert updated["last_job"]["provider"] == "jira"
    assert updated["last_job"]["provider_ticket_status"] == "resolved"
    assert updated["last_job"]["provider_assignee"] == "qa-owner"
    assert updated["last_job"]["provider_payload"] == {"source": "jira-webhook"}


def test_breakage_helpdesk_ticket_update_uses_existing_provider_and_in_progress_state(
    session,
):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="ticket-update-in-progress",
        product_item_id="p-hd-ticket-progress",
        bom_line_item_id="bom-hd-ticket-progress",
    )
    session.commit()
    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=15,
        provider="zendesk",
        idempotency_key="hd-ticket-progress-1",
    )
    session.commit()

    updated = service.apply_helpdesk_ticket_update(
        incident.id,
        provider_ticket_status="working",
        job_id=job.id,
        provider_updated_at=datetime(2026, 3, 6, 9, 0, 0),
        incident_responsibility="line-team",
        user_id=15,
    )
    session.commit()

    assert updated["incident_status"] == "in_progress"
    assert updated["incident_responsibility"] == "line-team"
    assert updated["sync_status"] == "in_progress"
    assert updated["last_job"]["provider"] == "zendesk"
    assert updated["last_job"]["status"] == "processing"
    assert updated["last_job"]["provider_ticket_status"] == "in_progress"
    assert updated["last_job"]["provider_ticket_updated_at"] == "2026-03-06T09:00:00"


def test_breakage_helpdesk_ticket_update_event_id_replay_is_idempotent(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="ticket-update-idempotent",
        product_item_id="p-hd-ticket-idempotent",
        bom_line_item_id="bom-hd-ticket-idempotent",
    )
    session.commit()
    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=15,
        provider="jira",
        idempotency_key="hd-ticket-idempotent-1",
    )
    session.commit()

    first = service.apply_helpdesk_ticket_update(
        incident.id,
        provider_ticket_status="assigned",
        job_id=job.id,
        event_id="evt-1",
        provider_assignee="ops-a",
        user_id=15,
    )
    session.commit()
    assert first["idempotent_replay"] is False
    assert first["event_id"] == "evt-1"
    assert first["incident_status"] == "in_progress"
    assert first["sync_status"] == "in_progress"

    replay = service.apply_helpdesk_ticket_update(
        incident.id,
        provider_ticket_status="closed",
        job_id=job.id,
        event_id="evt-1",
        provider_assignee="ops-b",
        user_id=15,
    )
    session.commit()
    assert replay["idempotent_replay"] is True
    assert replay["event_id"] == "evt-1"
    assert replay["incident_status"] == "in_progress"
    assert replay["sync_status"] == "in_progress"
    assert replay["last_job"]["provider_last_event_id"] == "evt-1"
    assert replay["last_job"]["provider_event_ids_count"] == 1
    assert replay["last_job"]["provider_ticket_status"] == "in_progress"

    second = service.apply_helpdesk_ticket_update(
        incident.id,
        provider_ticket_status="closed",
        job_id=job.id,
        event_id="evt-2",
        user_id=15,
    )
    session.commit()
    assert second["idempotent_replay"] is False
    assert second["event_id"] == "evt-2"
    assert second["incident_status"] == "closed"
    assert second["sync_status"] == "completed"
    assert second["last_job"]["provider_last_event_id"] == "evt-2"
    assert second["last_job"]["provider_event_ids_count"] == 2


def test_breakage_run_helpdesk_sync_job_provider_dispatch(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="provider-dispatch",
        product_item_id="p-provider-1",
        bom_line_item_id="bom-provider-1",
    )
    session.commit()
    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=8,
        provider="jira",
        metadata_json={"channel": "worker"},
    )
    session.commit()

    result = service.run_helpdesk_sync_job(job.id, user_id=8)
    session.commit()

    assert result["incident_id"] == incident.id
    assert result["sync_status"] == "completed"
    assert str(result["external_ticket_id"]).startswith("JIRA-")
    assert result["last_job"]["provider"] == "jira"


def test_breakage_run_helpdesk_sync_job_maps_provider_errors(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="provider-error-map",
        product_item_id="p-provider-2",
        bom_line_item_id="bom-provider-2",
    )
    session.commit()
    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=8,
        provider="zendesk",
        metadata_json={"force_error_code": "timeout"},
    )
    session.commit()

    result = service.run_helpdesk_sync_job(job.id, user_id=8)
    session.commit()

    assert result["incident_id"] == incident.id
    assert result["sync_status"] == "failed"
    assert result["last_job"]["failure_category"] == "transient"
    assert "timeout" in str(result["last_job"]["last_error"]).lower()


def test_breakage_run_helpdesk_sync_job_http_dispatch_jira(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="provider-http-jira",
        product_item_id="p-provider-http-jira",
        bom_line_item_id="bom-provider-http-jira",
    )
    session.commit()
    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=8,
        provider="jira",
        idempotency_key="jira-http-idemp-1",
        integration_json={
            "mode": "http",
            "base_url": "https://jira.example.test",
            "auth_type": "bearer",
            "token": "jira-token",
            "jira_project_key": "OPS",
            "jira_issue_type": "Task",
            "timeout_s": 9,
        },
    )
    session.commit()

    with patch("yuantus.meta_engine.services.parallel_tasks_service.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        response = MagicMock()
        response.status_code = 201
        response.text = '{"key":"OPS-1001"}'
        response.json.return_value = {"key": "OPS-1001"}
        client.post.return_value = response

        result = service.run_helpdesk_sync_job(job.id, user_id=8)
        session.commit()

    assert result["sync_status"] == "completed"
    assert result["external_ticket_id"] == "OPS-1001"
    assert result["last_job"]["provider"] == "jira"
    assert result["last_job"]["integration_mode"] == "http"
    assert result["last_job"]["integration_base_url"] == "https://jira.example.test"

    args, kwargs = client.post.call_args
    assert args[0] == "https://jira.example.test/rest/api/2/issue"
    assert kwargs["headers"]["Authorization"] == "Bearer jira-token"
    assert kwargs["headers"]["X-Idempotency-Key"] == "jira-http-idemp-1"
    assert kwargs["json"]["fields"]["project"]["key"] == "OPS"


def test_breakage_run_helpdesk_sync_job_http_error_mapping_and_integration_validation(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="provider-http-zendesk",
        product_item_id="p-provider-http-zd",
        bom_line_item_id="bom-provider-http-zd",
    )
    session.commit()

    with pytest.raises(ValueError, match="integration.base_url is required when mode=http"):
        service.enqueue_helpdesk_stub_sync(
            incident.id,
            user_id=8,
            provider="zendesk",
            integration_json={"mode": "http"},
        )

    job = service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=8,
        provider="zendesk",
        idempotency_key="zd-http-idemp-1",
        integration_json={
            "mode": "http",
            "base_url": "https://zendesk.example.test",
            "auth_type": "basic",
            "username": "api-user",
            "api_key": "api-key",
            "zendesk_requester_email": "ops@example.test",
            "zendesk_priority": "high",
        },
    )
    session.commit()

    with patch("yuantus.meta_engine.services.parallel_tasks_service.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        response = MagicMock()
        response.status_code = 429
        response.text = "Too Many Requests"
        response.json.side_effect = ValueError("not json")
        client.post.return_value = response

        result = service.run_helpdesk_sync_job(job.id, user_id=8)
        session.commit()

    assert result["sync_status"] == "failed"
    assert result["last_job"]["provider"] == "zendesk"
    assert result["last_job"]["integration_mode"] == "http"
    assert result["last_job"]["error_code"] == "provider_rate_limited"
    assert result["last_job"]["failure_category"] == "transient"
    assert "429" in str(result["last_job"]["error_message"])


def test_breakage_helpdesk_sync_result_rejects_invalid_sync_status(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(description="invalid-sync-status")
    session.commit()
    service.enqueue_helpdesk_stub_sync(incident.id, user_id=1)
    session.commit()

    with pytest.raises(ValueError, match="sync_status must be one of: completed, failed"):
        service.record_helpdesk_sync_result(
            incident.id,
            sync_status="queued",
        )


def test_breakage_incidents_export_job_lifecycle_and_download(session):
    service = BreakageIncidentService(session)
    service.create_incident(
        description="incident-export-job-a",
        product_item_id="p-export-1",
        bom_line_item_id="bom-export-1",
        batch_code="batch-export-1",
        responsibility="supplier-export",
    )
    session.commit()

    enqueued = service.enqueue_incidents_export_job(
        bom_line_item_id="bom-export-1",
        page=1,
        page_size=20,
        export_format="csv",
        execute_immediately=False,
        user_id=9,
    )
    assert enqueued["job_id"]
    assert enqueued["status"] in {"pending", "processing"}
    assert enqueued["download_ready"] is False

    executed = service.execute_incidents_export_job(enqueued["job_id"], user_id=9)
    session.commit()
    assert executed["status"] == "completed"
    assert executed["download_ready"] is True
    assert executed["filename"] == "breakage-incidents.csv"

    status = service.get_incidents_export_job(enqueued["job_id"])
    assert status["status"] == "completed"
    assert status["download_ready"] is True

    downloaded = service.download_incidents_export_job(enqueued["job_id"])
    assert downloaded["media_type"] == "text/csv"
    csv_text = downloaded["content"].decode("utf-8")
    assert "bom_line_item_id_filter" in csv_text
    assert "bom-export-1" in csv_text


def test_breakage_incidents_export_job_cleanup_expires_download_payload(session):
    service = BreakageIncidentService(session)
    service.create_incident(
        description="incident-export-cleanup",
        product_item_id="p-export-cleanup",
        bom_line_item_id="bom-export-cleanup",
    )
    session.commit()
    enqueued = service.enqueue_incidents_export_job(
        bom_line_item_id="bom-export-cleanup",
        page=1,
        page_size=20,
        export_format="json",
        execute_immediately=True,
        user_id=9,
    )
    session.commit()
    job_id = str(enqueued["job_id"])
    job = session.get(ConversionJob, job_id)
    assert job is not None
    job.completed_at = datetime.utcnow() - timedelta(hours=25)
    session.add(job)
    session.commit()

    cleanup = service.cleanup_expired_incidents_export_results(
        ttl_hours=24,
        limit=50,
        user_id=9,
    )
    session.commit()
    assert cleanup["expired_jobs"] >= 1
    assert job_id in set(cleanup["job_ids"])

    status = service.get_incidents_export_job(job_id)
    assert status["download_ready"] is False
    assert status["sync_status"] == "expired"
    with pytest.raises(ValueError, match="Export content missing for job"):
        service.download_incidents_export_job(job_id)


def test_breakage_cockpit_and_export_supports_formats(session):
    service = BreakageIncidentService(session)
    incident_a = service.create_incident(
        description="cockpit-a",
        severity="high",
        status="open",
        product_item_id="p-cockpit-1",
        bom_id="bom-root-cockpit-1",
        bom_line_item_id="bom-cockpit-1",
        routing_id="routing-cockpit-1",
        mbom_id="mbom-cockpit-1",
        batch_code="batch-cockpit-1",
        responsibility="supplier-cockpit",
    )
    incident_b = service.create_incident(
        description="cockpit-b",
        severity="medium",
        status="closed",
        product_item_id="p-cockpit-2",
        bom_id="bom-root-cockpit-2",
        bom_line_item_id="bom-cockpit-2",
        routing_id="routing-cockpit-2",
        mbom_id="mbom-cockpit-2",
        batch_code="batch-cockpit-2",
        responsibility="supplier-cockpit",
    )
    session.commit()
    job_a = service.enqueue_helpdesk_stub_sync(incident_a.id, user_id=7)
    job_b = service.enqueue_helpdesk_stub_sync(incident_b.id, user_id=7)
    session.commit()
    service.record_helpdesk_sync_result(
        incident_a.id,
        sync_status="completed",
        job_id=job_a.id,
        external_ticket_id="HD-CP-1",
        user_id=7,
    )
    service.record_helpdesk_sync_result(
        incident_b.id,
        sync_status="failed",
        job_id=job_b.id,
        error_code="validation_error",
        error_message="invalid payload",
        user_id=7,
    )
    service.apply_helpdesk_ticket_update(
        incident_a.id,
        provider_ticket_status="resolved",
        job_id=job_a.id,
        provider_assignee="supplier-cockpit",
        user_id=7,
    )
    service.apply_helpdesk_ticket_update(
        incident_b.id,
        provider_ticket_status="failed",
        job_id=job_b.id,
        provider_assignee="supplier-cockpit",
        user_id=7,
    )
    session.commit()

    cockpit = service.cockpit(
        responsibility="supplier-cockpit",
        trend_window_days=14,
        page=1,
        page_size=20,
    )
    assert cockpit["total"] == 2
    assert cockpit["metrics"]["by_responsibility"]["supplier-cockpit"] == 2
    assert cockpit["metrics"]["by_bom_id"]["bom-root-cockpit-1"] == 1
    assert cockpit["metrics"]["by_bom_id"]["bom-root-cockpit-2"] == 1
    assert cockpit["metrics"]["by_mbom_id"]["mbom-cockpit-1"] == 1
    assert cockpit["metrics"]["by_mbom_id"]["mbom-cockpit-2"] == 1
    assert cockpit["metrics"]["by_routing_id"]["routing-cockpit-1"] == 1
    assert cockpit["metrics"]["by_routing_id"]["routing-cockpit-2"] == 1
    assert any(
        row.get("bom_id") == "bom-root-cockpit-1"
        for row in cockpit["metrics"]["top_bom_ids"]
    )
    assert any(
        row.get("mbom_id") == "mbom-cockpit-1"
        for row in cockpit["metrics"]["top_mbom_ids"]
    )
    assert any(
        row.get("routing_id") == "routing-cockpit-2"
        for row in cockpit["metrics"]["top_routing_ids"]
    )
    assert cockpit["helpdesk_sync_summary"]["total_jobs"] == 2
    assert cockpit["helpdesk_sync_summary"]["failed_jobs"] == 1
    assert cockpit["helpdesk_sync_summary"]["providers_total"] == 1
    assert cockpit["helpdesk_sync_summary"]["by_provider"]["stub"] == 2
    assert cockpit["helpdesk_sync_summary"]["by_provider_ticket_status"]["resolved"] == 1
    assert cockpit["helpdesk_sync_summary"]["by_provider_ticket_status"]["failed"] == 1
    assert cockpit["helpdesk_sync_summary"]["with_provider_ticket_status"] == 2
    assert any(
        row.get("helpdesk_ticket_summary", {}).get("external_ticket_id") == "HD-CP-1"
        for row in cockpit["incidents"]
        if isinstance(row.get("helpdesk_ticket_summary"), dict)
    )
    assert any(
        row.get("helpdesk_ticket_summary", {}).get("provider_assignee")
        == "supplier-cockpit"
        for row in cockpit["incidents"]
        if isinstance(row.get("helpdesk_ticket_summary"), dict)
    )

    exported_json = service.export_cockpit(
        responsibility="supplier-cockpit",
        trend_window_days=14,
        export_format="json",
    )
    assert exported_json["media_type"] == "application/json"
    assert exported_json["filename"] == "breakage-cockpit.json"
    assert '"helpdesk_sync_summary"' in exported_json["content"].decode("utf-8")
    assert '"helpdesk_ticket_summary"' in exported_json["content"].decode("utf-8")
    assert '"external_ticket_id": "HD-CP-1"' in exported_json["content"].decode(
        "utf-8"
    )

    exported_csv = service.export_cockpit(
        responsibility="supplier-cockpit",
        trend_window_days=14,
        export_format="csv",
    )
    assert exported_csv["media_type"] == "text/csv"
    assert exported_csv["filename"] == "breakage-cockpit.csv"
    csv_text = exported_csv["content"].decode("utf-8")
    assert "helpdesk_failed_jobs" in csv_text
    assert "cockpit-a" in csv_text

    exported_md = service.export_cockpit(
        responsibility="supplier-cockpit",
        trend_window_days=14,
        export_format="md",
    )
    assert exported_md["media_type"] == "text/markdown"
    assert exported_md["filename"] == "breakage-cockpit.md"
    md_text = exported_md["content"].decode("utf-8")
    assert md_text.startswith("# Breakage Cockpit")
    assert "helpdesk_sync_summary" in md_text


def test_breakage_incident_identity_and_dimensions_normalize_aliases(session):
    service = BreakageIncidentService(session)
    incident = service.create_incident(
        description="identity-check",
        bom_id="bom-root-identity-1",
        mbom_id="mbom-identity-1",
        routing_id="routing-identity-1",
        product_item_id="p-identity-1",
        bom_line_item_id="bom-line-identity-1",
    )
    session.commit()

    serialized = service.export_incidents(export_format="json")
    payload = json.loads(serialized["content"].decode("utf-8"))
    row = payload["incidents"][0]

    assert incident.incident_code == "BRK-000001"
    assert incident.version_id == "mbom-identity-1"
    assert incident.production_order_id == "routing-identity-1"
    assert row["incident_code"] == "BRK-000001"
    assert row["bom_id"] == "bom-root-identity-1"
    assert row["mbom_id"] == "mbom-identity-1"
    assert row["routing_id"] == "routing-identity-1"
    assert row["version_id"] == "mbom-identity-1"
    assert row["production_order_id"] == "routing-identity-1"


def test_workorder_doc_pack_supports_inherited_links_and_zip_export(session):
    service = WorkorderDocumentPackService(session)
    service.upsert_link(routing_id="r-1", document_item_id="doc-routing")
    service.upsert_link(
        routing_id="r-1",
        operation_id="op-10",
        document_item_id="doc-op",
        inherit_to_children=False,
    )
    session.commit()

    docs = service.list_links(
        routing_id="r-1",
        operation_id="op-10",
        include_inherited=True,
    )
    assert {doc.document_item_id for doc in docs} == {"doc-routing", "doc-op"}

    pack = service.export_pack(
        routing_id="r-1",
        operation_id="op-10",
        export_meta={
            "job_no": "wo-20260228-001",
            "operator_id": 7,
            "operator_name": "Alice",
            "exported_by": "alice@example.com",
        },
    )
    assert pack["manifest"]["count"] == 2
    assert pack["manifest"]["scope_summary"]["routing"] == 1
    assert pack["manifest"]["scope_summary"]["operation"] == 1
    assert pack["manifest"]["export_meta"]["job_no"] == "wo-20260228-001"
    assert pack["manifest"]["export_meta"]["operator_id"] == 7
    zf = ZipFile(io.BytesIO(pack["zip_bytes"]))
    names = set(zf.namelist())
    assert "manifest.json" in names
    assert "documents.csv" in names


def test_workorder_doc_pack_includes_locale_profile_context(session):
    service = WorkorderDocumentPackService(session)
    service.upsert_link(routing_id="r-locale", document_item_id="doc-1")
    session.add(
        ReportLocaleProfile(
            id="rp-workorder-zh",
            name="Workorder ZH",
            lang="zh_CN",
            timezone="Asia/Shanghai",
            report_type="workorder_doc_pack",
            is_default=True,
        )
    )
    session.commit()

    pack = service.export_pack(
        routing_id="r-locale",
        export_meta={
            "report_lang": "zh_CN",
            "report_type": "workorder_doc_pack",
        },
    )

    locale = pack["manifest"]["locale"]
    assert locale["id"] == "rp-workorder-zh"
    assert locale["lang"] == "zh_CN"
    assert locale["requested_lang"] == "zh_CN"
    zf = ZipFile(io.BytesIO(pack["zip_bytes"]))
    names = set(zf.namelist())
    assert "locale.json" in names


def _seed_item_version(
    session,
    *,
    version_id: str,
    item_id: str,
    label: str = "1.A",
    is_current: bool = True,
    is_released: bool = False,
) -> ItemVersion:
    version = ItemVersion(
        id=version_id,
        item_id=item_id,
        version_label=label,
        is_current=is_current,
        is_released=is_released,
    )
    session.add(version)
    session.flush()
    return version


def test_workorder_doc_pack_upsert_persists_version_lock_metadata(session):
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-1", item_id="doc-1", is_current=True)

    link = service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-1",
        document_version_id="v-1",
        version_lock_source="manual",
    )
    session.commit()

    assert link.document_version_id == "v-1"
    assert link.version_locked_at is not None
    assert link.version_lock_source == "manual"

    serialized = service.serialize_link(link)
    assert serialized["version_locked"] is True
    assert serialized["version_lock_source"] == "manual"
    assert serialized["version_label"] == "1.A"
    assert serialized["version_is_current"] is True
    assert serialized["version_belongs_to_item"] is True


def test_workorder_doc_pack_upsert_legacy_payload_preserves_existing_lock(session):
    """Old callers that never pass document_version_id must not unlock rows."""
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-1", item_id="doc-1")
    service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-1",
        document_version_id="v-1",
    )
    session.commit()

    # Legacy signature — no document_version_id passed. The lock must persist.
    service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-1",
        inherit_to_children=False,
        visible_in_production=True,
    )
    session.commit()

    link = (
        session.query(WorkorderDocumentLink)
        .filter(WorkorderDocumentLink.document_item_id == "doc-1")
        .one()
    )
    assert link.document_version_id == "v-1"
    assert link.version_lock_source == "manual"
    assert link.version_locked_at is not None
    assert link.inherit_to_children is False


def test_workorder_doc_pack_upsert_explicit_none_clears_lock(session):
    """Explicit None for document_version_id clears the lock columns."""
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-1", item_id="doc-1")
    service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-1",
        document_version_id="v-1",
    )
    session.commit()

    service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-1",
        document_version_id=None,
    )
    session.commit()

    link = (
        session.query(WorkorderDocumentLink)
        .filter(WorkorderDocumentLink.document_item_id == "doc-1")
        .one()
    )
    assert link.document_version_id is None
    assert link.version_locked_at is None
    assert link.version_lock_source is None


def test_workorder_doc_pack_upsert_rejects_missing_version(session):
    service = WorkorderDocumentPackService(session)
    with pytest.raises(ValueError, match="not found"):
        service.upsert_link(
            routing_id="r-1",
            document_item_id="doc-1",
            document_version_id="v-missing",
        )


def test_workorder_doc_pack_upsert_rejects_cross_item_version(session):
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-A", item_id="doc-A")
    with pytest.raises(ValueError, match="does not belong"):
        service.upsert_link(
            routing_id="r-1",
            document_item_id="doc-B",
            document_version_id="v-A",
        )


def test_workorder_doc_pack_upsert_rejects_unknown_lock_source(session):
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-1", item_id="doc-1")
    with pytest.raises(ValueError, match="version_lock_source"):
        service.upsert_link(
            routing_id="r-1",
            document_item_id="doc-1",
            document_version_id="v-1",
            version_lock_source="bogus",
        )


def test_workorder_doc_pack_list_serializes_version_metadata(session):
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-routing", item_id="doc-routing")
    _seed_item_version(session, version_id="v-op", item_id="doc-op")
    service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-routing",
        document_version_id="v-routing",
    )
    service.upsert_link(
        routing_id="r-1",
        operation_id="op-10",
        document_item_id="doc-op",
        document_version_id="v-op",
        version_lock_source="eco_apply",
    )
    session.commit()

    links = service.list_links(routing_id="r-1", operation_id="op-10")
    serialized = [service.serialize_link(link) for link in links]
    by_doc = {row["document_item_id"]: row for row in serialized}
    assert by_doc["doc-routing"]["version_locked"] is True
    assert by_doc["doc-routing"]["version_lock_source"] == "manual"
    assert by_doc["doc-op"]["version_lock_source"] == "eco_apply"


def test_workorder_doc_pack_export_manifest_includes_lock_summary(session):
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-locked", item_id="doc-locked")
    service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-locked",
        document_version_id="v-locked",
    )
    service.upsert_link(routing_id="r-1", document_item_id="doc-unlocked")
    session.commit()

    pack = service.export_pack(routing_id="r-1")
    manifest = pack["manifest"]
    assert manifest["version_lock_summary"] == {
        "locked": 1,
        "unlocked": 1,
        "mismatched": 0,
        "stale": 0,
        "requires_lock": False,
    }
    docs_by_item = {row["document_item_id"]: row for row in manifest["documents"]}
    assert docs_by_item["doc-locked"]["version_locked"] is True
    assert docs_by_item["doc-unlocked"]["version_locked"] is False
    zf = ZipFile(io.BytesIO(pack["zip_bytes"]))
    csv_body = zf.read("documents.csv").decode("utf-8")
    assert "document_version_id" in csv_body
    assert "version_locked" in csv_body


def test_workorder_doc_pack_export_require_locked_versions_fail_closed(session):
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-locked", item_id="doc-locked")
    service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-locked",
        document_version_id="v-locked",
    )
    service.upsert_link(routing_id="r-1", document_item_id="doc-unlocked")
    session.commit()

    with pytest.raises(ValueError, match="require_locked_versions"):
        service.export_pack(routing_id="r-1", require_locked_versions=True)


def test_workorder_doc_pack_export_require_locked_versions_accepts_all_locked(session):
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-routing", item_id="doc-routing")
    _seed_item_version(session, version_id="v-op", item_id="doc-op")
    service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-routing",
        document_version_id="v-routing",
    )
    service.upsert_link(
        routing_id="r-1",
        operation_id="op-10",
        document_item_id="doc-op",
        document_version_id="v-op",
        inherit_to_children=False,
    )
    session.commit()

    pack = service.export_pack(
        routing_id="r-1",
        operation_id="op-10",
        require_locked_versions=True,
    )
    summary = pack["manifest"]["version_lock_summary"]
    assert summary["locked"] == 2
    assert summary["unlocked"] == 0
    assert summary["requires_lock"] is True


def test_workorder_doc_pack_export_marks_stale_without_failing_closed(session):
    """Locked-but-stale rows surface in version_lock_summary.stale and DO NOT raise.

    Freshness rejection is deferred per taskbook §5.3 — the ECO apply hook is
    the authoritative refresh path in R1. This test locks the contract so a
    future contributor cannot silently flip stale rejection on.
    """
    service = WorkorderDocumentPackService(session)
    _seed_item_version(
        session,
        version_id="v-old",
        item_id="doc-1",
        label="1.A",
        is_current=False,
        is_released=True,
    )
    service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-1",
        document_version_id="v-old",
    )
    session.commit()

    pack = service.export_pack(routing_id="r-1", require_locked_versions=True)
    summary = pack["manifest"]["version_lock_summary"]
    assert summary["locked"] == 1
    assert summary["unlocked"] == 0
    assert summary["mismatched"] == 0
    assert summary["stale"] == 1
    assert summary["requires_lock"] is True


def test_workorder_doc_pack_refresh_updates_only_matching_item(session):
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-A1", item_id="doc-A")
    _seed_item_version(session, version_id="v-A2", item_id="doc-A")
    _seed_item_version(session, version_id="v-B1", item_id="doc-B")
    link_a_routing = service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-A",
        document_version_id="v-A1",
    )
    link_a_op = service.upsert_link(
        routing_id="r-1",
        operation_id="op-10",
        document_item_id="doc-A",
        document_version_id="v-A1",
    )
    link_b = service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-B",
        document_version_id="v-B1",
    )
    link_unlocked = service.upsert_link(
        routing_id="r-1",
        document_item_id="doc-A",
        operation_id="op-20",
    )
    session.commit()

    updated = service.refresh_document_version_locks_for_item(
        document_item_id="doc-A",
        document_version_id="v-A2",
    )
    session.commit()
    assert updated == 3

    session.refresh(link_a_routing)
    session.refresh(link_a_op)
    session.refresh(link_unlocked)
    session.refresh(link_b)
    assert link_a_routing.document_version_id == "v-A2"
    assert link_a_routing.version_lock_source == "eco_apply"
    assert link_a_op.document_version_id == "v-A2"
    assert link_unlocked.document_version_id == "v-A2"
    assert link_b.document_version_id == "v-B1"


def test_workorder_doc_pack_refresh_rejects_cross_item_version(session):
    service = WorkorderDocumentPackService(session)
    _seed_item_version(session, version_id="v-X", item_id="doc-X")
    with pytest.raises(ValueError, match="does not belong"):
        service.refresh_document_version_locks_for_item(
            document_item_id="doc-Y",
            document_version_id="v-X",
        )


def test_3d_overlay_role_gate_and_component_lookup(session):
    service = ThreeDOverlayService(session)
    service.reset_cache_for_tests()
    service.upsert_overlay(
        document_item_id="doc-3d-1",
        status="released",
        visibility_role="engineer",
        part_refs=[
            {"component_ref": "C-001", "item_id": "item-1", "name": "motor"},
            {"component_ref": "C-002", "item_id": "item-2", "name": "housing"},
        ],
    )
    session.commit()

    with pytest.raises(PermissionError):
        service.get_overlay(document_item_id="doc-3d-1", user_roles=["viewer"])

    overlay = service.get_overlay(document_item_id="doc-3d-1", user_roles=["engineer"])
    assert overlay is not None

    hit = service.resolve_component(
        document_item_id="doc-3d-1",
        component_ref="C-002",
        user_roles=["engineer"],
    )
    assert hit["hit"]["item_id"] == "item-2"

    with pytest.raises(ValueError, match="Component not found"):
        service.resolve_component(
            document_item_id="doc-3d-1",
            component_ref="C-999",
            user_roles=["engineer"],
        )


def test_3d_explode_upsert_get_and_update(session):
    service = ThreeDOverlayService(session)
    service.reset_cache_for_tests()
    config = {
        "factor": 1.5,
        "mode": "radial",
        "offsets": [
            {"component_ref": "C-001", "offset": [1.0, 0.0, 0.0]},
            {"component_ref": "C-002", "offset": [0.0, 2.0, 0.0]},
        ],
    }
    returned = service.upsert_explode(document_item_id="doc-ex-1", explode_config=config)
    session.commit()
    assert returned == config

    service.reset_cache_for_tests()  # force a DB reload
    assert service.get_explode(document_item_id="doc-ex-1") == config

    # update replaces the config in place
    service.upsert_explode(
        document_item_id="doc-ex-1",
        explode_config={"factor": 3.0, "mode": "axial", "offsets": []},
    )
    session.commit()
    service.reset_cache_for_tests()
    got = service.get_explode(document_item_id="doc-ex-1")
    assert got["factor"] == 3.0 and got["offsets"] == []


def test_3d_explode_none_when_absent(session):
    service = ThreeDOverlayService(session)
    service.reset_cache_for_tests()
    # no overlay row at all
    assert service.get_explode(document_item_id="doc-ex-missing") is None
    # overlay exists but carries no explode key
    service.upsert_overlay(document_item_id="doc-ex-2", part_refs=[])
    session.commit()
    service.reset_cache_for_tests()
    assert service.get_explode(document_item_id="doc-ex-2") is None


def test_3d_explode_inherits_overlay_role_gate(session):
    service = ThreeDOverlayService(session)
    service.reset_cache_for_tests()
    service.upsert_overlay(
        document_item_id="doc-ex-3", visibility_role="engineer", part_refs=[]
    )
    service.upsert_explode(
        document_item_id="doc-ex-3",
        explode_config={"factor": 1.0, "mode": "radial", "offsets": []},
    )
    session.commit()
    service.reset_cache_for_tests()

    with pytest.raises(PermissionError):
        service.get_explode(document_item_id="doc-ex-3", user_roles=["viewer"])
    got = service.get_explode(document_item_id="doc-ex-3", user_roles=["engineer"])
    assert got["factor"] == 1.0


def test_3d_overlay_batch_resolve_and_cache_stats(session):
    service = ThreeDOverlayService(session)
    service.reset_cache_for_tests()
    service.upsert_overlay(
        document_item_id="doc-3d-cache",
        status="released",
        visibility_role="engineer",
        part_refs=[
            {"component_ref": "C-001", "item_id": "item-1", "name": "motor"},
            {"component_ref": "C-002", "item_id": "item-2", "name": "housing"},
        ],
    )
    session.commit()

    service.reset_cache_for_tests()
    overlay = service.get_overlay(document_item_id="doc-3d-cache", user_roles=["engineer"])
    assert overlay is not None
    stats_after_miss = service.cache_stats()
    assert stats_after_miss["misses"] == 1
    assert stats_after_miss["hits"] == 0
    assert stats_after_miss["entries"] == 1

    _ = service.get_overlay(document_item_id="doc-3d-cache", user_roles=["engineer"])
    stats_after_hit = service.cache_stats()
    assert stats_after_hit["hits"] == 1
    assert stats_after_hit["entries"] == 1

    batch = service.resolve_components(
        document_item_id="doc-3d-cache",
        component_refs=["C-002", "C-404"],
        user_roles=["engineer"],
        include_missing=True,
    )
    assert batch["requested"] == 2
    assert batch["hits"] == 1
    assert batch["misses"] == 1
    assert batch["results"][0]["component_ref"] == "C-002"
    assert batch["results"][0]["found"] is True
    assert batch["results"][0]["hit"]["item_id"] == "item-2"
    assert batch["results"][1]["component_ref"] == "C-404"
    assert batch["results"][1]["found"] is False

    found_only = service.resolve_components(
        document_item_id="doc-3d-cache",
        component_refs=["C-002", "C-404"],
        user_roles=["engineer"],
        include_missing=False,
    )
    assert found_only["returned"] == 1
    assert found_only["hits"] == 1

    with pytest.raises(PermissionError):
        service.resolve_components(
            document_item_id="doc-3d-cache",
            component_refs=["C-001"],
            user_roles=["viewer"],
        )


def test_parallel_ops_overview_summary_and_window_validation(session):
    ThreeDOverlayService.reset_cache_for_tests()
    overlay_service = ThreeDOverlayService(session)
    overlay_service.upsert_overlay(
        document_item_id="doc-ops-1",
        visibility_role="engineer",
        part_refs=[{"component_ref": "X-1", "item_id": "i-1"}],
    )
    session.commit()

    _ = overlay_service.get_overlay(document_item_id="doc-ops-1", user_roles=["engineer"])
    _ = overlay_service.get_overlay(document_item_id="doc-ops-1", user_roles=["engineer"])

    now = datetime.utcnow()
    session.add_all(
        [
            ConversionJob(
                id="job-sync-ok",
                task_type="document_sync_push",
                status="completed",
                payload={"site_id": "site-1"},
                attempt_count=1,
                max_attempts=3,
                created_at=now - timedelta(days=1),
            ),
            ConversionJob(
                id="job-sync-dead",
                task_type="document_sync_pull",
                status="failed",
                payload={"site_id": "site-1"},
                attempt_count=3,
                max_attempts=3,
                created_at=now - timedelta(days=1),
            ),
            ConversionJob(
                id="job-sync-other-site",
                task_type="document_sync_push",
                status="completed",
                payload={"site_id": "site-2"},
                attempt_count=1,
                max_attempts=3,
                created_at=now - timedelta(days=1),
            ),
        ]
    )
    session.add_all(
        [
            WorkflowCustomActionRun(
                id="wf-run-ok",
                rule_id="r-1",
                object_id="eco-1",
                target_object="ECO",
                from_state="draft",
                to_state="progress",
                trigger_phase="before",
                status="completed",
                attempts=1,
                result={"result_code": "OK"},
                created_at=now - timedelta(days=1),
            ),
            WorkflowCustomActionRun(
                id="wf-run-failed",
                rule_id="r-2",
                object_id="eco-2",
                target_object="ECO",
                from_state="draft",
                to_state="progress",
                trigger_phase="before",
                status="failed",
                attempts=3,
                result={"result_code": "RETRY_EXHAUSTED"},
                created_at=now - timedelta(days=1),
            ),
        ]
    )

    breakage_service = BreakageIncidentService(session)
    breakage_a = breakage_service.create_incident(
        description="bearing crack",
        severity="high",
        status="open",
        responsibility="supplier-a",
    )
    breakage_b = breakage_service.create_incident(
        description="bearing crack",
        severity="high",
        status="open",
        responsibility="supplier-a",
    )
    breakage_job = breakage_service.enqueue_helpdesk_stub_sync(
        breakage_a.id,
        user_id=7,
        provider="jira",
        idempotency_key="ops-summary-hd-1",
    )
    breakage_service.record_helpdesk_sync_result(
        breakage_a.id,
        sync_status="completed",
        job_id=breakage_job.id,
        external_ticket_id="OPS-1001",
        user_id=7,
    )
    breakage_service.apply_helpdesk_ticket_update(
        breakage_a.id,
        provider_ticket_status="resolved",
        job_id=breakage_job.id,
        event_id="evt-ops-summary-1",
        incident_status="open",
        user_id=7,
    )
    breakage_failed_job = breakage_service.enqueue_helpdesk_stub_sync(
        breakage_b.id,
        user_id=7,
        provider="zendesk",
        idempotency_key="ops-summary-hd-failed-1",
    )
    breakage_service.record_helpdesk_sync_result(
        breakage_b.id,
        sync_status="failed",
        job_id=breakage_failed_job.id,
        error_code="provider_timeout",
        error_message="provider timeout",
        user_id=7,
    )
    failed_job_row = session.get(ConversionJob, breakage_failed_job.id)
    assert failed_job_row is not None
    failed_payload = dict(failed_job_row.payload or {})
    failed_helpdesk_sync = (
        dict(failed_payload.get("helpdesk_sync"))
        if isinstance(failed_payload.get("helpdesk_sync"), dict)
        else {}
    )
    failed_result = (
        dict(failed_payload.get("result"))
        if isinstance(failed_payload.get("result"), dict)
        else {}
    )
    failed_helpdesk_sync["provider_ticket_status"] = "on_hold"
    failed_result["provider_ticket_status"] = "on_hold"
    failed_payload["helpdesk_sync"] = failed_helpdesk_sync
    failed_payload["result"] = failed_result
    failed_payload["provider_ticket_status"] = "on_hold"
    failed_job_row.payload = failed_payload
    session.add(failed_job_row)

    consumption_service = ConsumptionPlanService(session)
    _ = consumption_service.create_template_version(
        template_key="tpl-ops",
        name="ops-v1",
        planned_quantity=10.0,
        version_label="v1",
        activate=True,
    )
    _ = consumption_service.create_template_version(
        template_key="tpl-ops",
        name="ops-v2",
        planned_quantity=12.0,
        version_label="v2",
        activate=True,
    )
    session.add(
        ReportLocaleProfile(
            id="rp-ops-zh",
            name="Parallel Ops ZH",
            lang="zh_CN",
            timezone="Asia/Shanghai",
            report_type="parallel_ops_summary",
            is_default=True,
        )
    )
    session.add(
        ReportLocaleProfile(
            id="rp-ops-trends-zh",
            name="Parallel Ops Trends ZH",
            lang="zh_CN",
            timezone="Asia/Shanghai",
            report_type="parallel_ops_trends",
            is_default=True,
        )
    )
    session.commit()

    ops = ParallelOpsOverviewService(session)
    result = ops.summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
    )
    assert result["window_days"] == 7
    assert result["doc_sync"]["total"] == 2
    assert result["doc_sync"]["dead_letter_total"] == 1
    assert result["doc_sync"]["by_direction"]["push"] == 1
    assert result["doc_sync"]["by_direction"]["pull"] == 1
    assert result["doc_sync"]["checkout_gate"]["enabled"] is False
    assert result["doc_sync"]["checkout_gate"]["threshold_hits_total"] == 0
    assert result["doc_sync"]["dead_letter_trend"]["bucket_days"] == 1
    assert (
        result["doc_sync"]["dead_letter_trend"]["aggregates"]["delta_dead_letter_total"]
        >= 0
    )
    assert result["workflow_actions"]["total"] == 2
    assert result["workflow_actions"]["by_result_code"]["OK"] == 1
    assert result["workflow_actions"]["by_result_code"]["RETRY_EXHAUSTED"] == 1
    assert result["breakages"]["total"] == 2
    assert result["breakages"]["open_total"] == 2
    assert result["breakages"]["helpdesk"]["total_jobs"] == 2
    assert result["breakages"]["helpdesk"]["providers_total"] == 2
    assert result["breakages"]["helpdesk"]["failed_jobs"] == 1
    assert result["breakages"]["helpdesk"]["failed_rate"] == pytest.approx(0.5)
    assert result["breakages"]["helpdesk"]["replay_jobs_total"] == 0
    assert result["breakages"]["helpdesk"]["replay_batches_total"] == 0
    assert result["breakages"]["helpdesk"]["replay_failed_jobs"] == 0
    assert result["breakages"]["helpdesk"]["by_provider"]["jira"] == 1
    assert result["breakages"]["helpdesk"]["by_provider"]["zendesk"] == 1
    assert result["breakages"]["helpdesk"]["by_provider_ticket_status"]["resolved"] == 1
    assert result["consumption_templates"]["versions_total"] == 2
    assert result["consumption_templates"]["templates_total"] == 1
    assert result["overlay_cache"]["requests"] >= 2

    hint_codes = {row["code"] for row in (result.get("slo_hints") or [])}
    assert "doc_sync_dead_letter_rate_high" in hint_codes
    assert "workflow_action_failed_rate_high" in hint_codes
    assert "breakage_open_rate_high" in hint_codes

    gate_triggered = ops.summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        doc_sync_checkout_gate_max_failed_warn=0,
        doc_sync_checkout_gate_max_dead_letter_warn=0,
    )
    gate_hint_codes = {row["code"] for row in (gate_triggered.get("slo_hints") or [])}
    assert "doc_sync_checkout_gate_threshold_hit" in gate_hint_codes
    assert gate_triggered["doc_sync"]["checkout_gate"]["enabled"] is True
    assert gate_triggered["doc_sync"]["checkout_gate"]["is_blocking"] is True
    hit_statuses = {
        row["status"]
        for row in (gate_triggered["doc_sync"]["checkout_gate"]["threshold_hits"] or [])
    }
    assert "failed" in hit_statuses
    assert "dead_letter" in hit_statuses

    trend_triggered = ops.summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        doc_sync_dead_letter_trend_delta_warn=0,
    )
    trend_hint_codes = {row["code"] for row in (trend_triggered.get("slo_hints") or [])}
    assert "doc_sync_dead_letter_trend_up" in trend_hint_codes

    relaxed = ops.summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        overlay_cache_hit_rate_warn=0.1,
        overlay_cache_min_requests_warn=999,
        doc_sync_dead_letter_rate_warn=1.0,
        workflow_failed_rate_warn=1.0,
        breakage_open_rate_warn=1.0,
        breakage_helpdesk_failed_rate_warn=0.9,
        breakage_helpdesk_failed_total_warn=99,
        breakage_helpdesk_triage_coverage_warn=0.0,
        breakage_helpdesk_export_failed_total_warn=99,
        breakage_helpdesk_provider_failed_rate_warn=1.0,
        breakage_helpdesk_provider_failed_min_jobs_warn=99,
        breakage_helpdesk_provider_failed_rate_critical=1.0,
        breakage_helpdesk_provider_failed_min_jobs_critical=999,
    )
    assert relaxed["slo_hints"] == []
    assert relaxed["slo_thresholds"]["doc_sync_dead_letter_rate_warn"] == 1.0
    assert relaxed["slo_thresholds"]["workflow_failed_rate_warn"] == 1.0
    assert relaxed["slo_thresholds"]["breakage_open_rate_warn"] == 1.0
    assert relaxed["slo_thresholds"]["breakage_helpdesk_failed_rate_warn"] == 0.9
    assert relaxed["slo_thresholds"]["breakage_helpdesk_failed_total_warn"] == 99
    assert relaxed["slo_thresholds"]["breakage_helpdesk_triage_coverage_warn"] == 0.0
    assert relaxed["slo_thresholds"]["breakage_helpdesk_export_failed_total_warn"] == 99
    assert relaxed["slo_thresholds"]["breakage_helpdesk_provider_failed_rate_warn"] == 1.0
    assert relaxed["slo_thresholds"]["breakage_helpdesk_provider_failed_min_jobs_warn"] == 99
    assert relaxed["slo_thresholds"]["breakage_helpdesk_provider_failed_rate_critical"] == 1.0
    assert relaxed["slo_thresholds"]["breakage_helpdesk_provider_failed_min_jobs_critical"] == 999
    assert relaxed["slo_thresholds"]["breakage_helpdesk_replay_failed_rate_warn"] == 0.5
    assert relaxed["slo_thresholds"]["breakage_helpdesk_replay_failed_total_warn"] == 3
    assert relaxed["slo_thresholds"]["breakage_helpdesk_replay_pending_total_warn"] == 10
    assert relaxed["slo_thresholds"]["doc_sync_checkout_gate_max_failed_warn"] is None
    assert relaxed["slo_thresholds"]["doc_sync_dead_letter_trend_delta_warn"] is None

    strict = ops.summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        overlay_cache_hit_rate_warn=0.1,
        overlay_cache_min_requests_warn=999,
        doc_sync_dead_letter_rate_warn=1.0,
        workflow_failed_rate_warn=1.0,
        breakage_open_rate_warn=1.0,
        breakage_helpdesk_failed_rate_warn=0.1,
        breakage_helpdesk_failed_total_warn=0,
        breakage_helpdesk_triage_coverage_warn=1.0,
        breakage_helpdesk_export_failed_total_warn=0,
        breakage_helpdesk_provider_failed_rate_warn=0.5,
        breakage_helpdesk_provider_failed_min_jobs_warn=1,
    )
    strict_codes = {row["code"] for row in (strict.get("slo_hints") or [])}
    assert "breakage_helpdesk_failed_rate_high" in strict_codes
    assert "breakage_helpdesk_failed_total_high" in strict_codes
    assert "breakage_helpdesk_triage_coverage_low" in strict_codes
    assert "breakage_helpdesk_provider_failed_rate_high" in strict_codes
    assert any(
        row.get("code") == "breakage_helpdesk_provider_failed_rate_high"
        and row.get("provider") == "zendesk"
        for row in (strict.get("slo_hints") or [])
    )
    critical = ops.summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        overlay_cache_hit_rate_warn=0.1,
        overlay_cache_min_requests_warn=999,
        doc_sync_dead_letter_rate_warn=1.0,
        workflow_failed_rate_warn=1.0,
        breakage_open_rate_warn=1.0,
        breakage_helpdesk_failed_rate_warn=0.9,
        breakage_helpdesk_failed_total_warn=99,
        breakage_helpdesk_triage_coverage_warn=0.0,
        breakage_helpdesk_export_failed_total_warn=99,
        breakage_helpdesk_provider_failed_rate_warn=0.99,
        breakage_helpdesk_provider_failed_min_jobs_warn=99,
        breakage_helpdesk_provider_failed_rate_critical=0.5,
        breakage_helpdesk_provider_failed_min_jobs_critical=1,
    )
    assert any(
        row.get("code") == "breakage_helpdesk_provider_failed_rate_critical"
        and row.get("provider") == "zendesk"
        and row.get("level") == "critical"
        for row in (critical.get("slo_hints") or [])
    )

    trends = ops.trends(
        window_days=7,
        bucket_days=1,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
    )
    assert trends["window_days"] == 7
    assert trends["bucket_days"] == 1
    assert len(trends["points"]) >= 1
    assert trends["aggregates"]["doc_sync_total"] == 2
    assert trends["aggregates"]["doc_sync_failed_total"] == 1
    assert trends["aggregates"]["doc_sync_dead_letter_total"] == 1
    assert trends["aggregates"]["doc_sync_push_total"] == 1
    assert trends["aggregates"]["doc_sync_pull_total"] == 1
    assert trends["aggregates"]["doc_sync_dead_letter_pull_total"] == 1
    assert trends["aggregates"]["workflow_total"] == 2
    assert trends["aggregates"]["workflow_failed_total"] == 1
    assert trends["aggregates"]["breakages_total"] == 2
    assert trends["aggregates"]["breakages_open_total"] == 2
    assert trends["consumption_templates"]["versions_total"] == 2

    trends_export_json = ops.export_trends(
        window_days=7,
        bucket_days=1,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        export_format="json",
    )
    assert trends_export_json["media_type"] == "application/json"
    assert trends_export_json["filename"] == "parallel-ops-trends.json"
    assert b'"bucket_days": 1' in trends_export_json["content"]
    trends_export_json_locale = ops.export_trends(
        window_days=7,
        bucket_days=1,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        export_format="json",
        report_lang="zh_CN",
        report_type="parallel_ops_trends",
        locale_profile_id="rp-ops-trends-zh",
    )
    trends_export_json_payload = json.loads(
        trends_export_json_locale["content"].decode("utf-8")
    )
    assert trends_export_json_payload["locale"]["id"] == "rp-ops-trends-zh"
    assert trends_export_json_payload["locale"]["lang"] == "zh_CN"

    trends_export_csv = ops.export_trends(
        window_days=7,
        bucket_days=1,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        export_format="csv",
    )
    assert trends_export_csv["media_type"] == "text/csv"
    assert trends_export_csv["filename"] == "parallel-ops-trends.csv"
    trends_csv_text = trends_export_csv["content"].decode("utf-8")
    assert "bucket_start,bucket_end,doc_sync_total" in trends_csv_text
    assert "doc_sync_push_total" in trends_csv_text

    trends_export_md = ops.export_trends(
        window_days=7,
        bucket_days=1,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        export_format="md",
    )
    assert trends_export_md["media_type"] == "text/markdown"
    assert trends_export_md["filename"] == "parallel-ops-trends.md"
    trends_md_text = trends_export_md["content"].decode("utf-8")
    assert trends_md_text.startswith("# Parallel Ops Trends")
    trends_export_md_locale = ops.export_trends(
        window_days=7,
        bucket_days=1,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        export_format="md",
        report_lang="zh_CN",
        report_type="parallel_ops_trends",
        locale_profile_id="rp-ops-trends-zh",
    )
    trends_md_locale_text = trends_export_md_locale["content"].decode("utf-8")
    assert "## Locale" in trends_md_locale_text
    assert "rp-ops-trends-zh" in trends_md_locale_text

    doc_sync_failures = ops.doc_sync_failures(
        window_days=7,
        site_id="site-1",
        page=1,
        page_size=1,
    )
    assert doc_sync_failures["total"] == 1
    assert doc_sync_failures["by_direction"]["pull"] == 1
    assert doc_sync_failures["pagination"]["pages"] == 1
    assert len(doc_sync_failures["jobs"]) == 1
    assert doc_sync_failures["jobs"][0]["status"] == "failed"
    assert doc_sync_failures["jobs"][0]["site_id"] == "site-1"
    assert doc_sync_failures["jobs"][0]["direction"] == "pull"

    workflow_failures = ops.workflow_failures(
        window_days=7,
        target_object="ECO",
        page=1,
        page_size=10,
    )
    assert workflow_failures["total"] == 1
    assert workflow_failures["runs"][0]["result_code"] == "RETRY_EXHAUSTED"

    breakage_helpdesk_failures = ops.breakage_helpdesk_failures(
        window_days=7,
        provider="zendesk",
        failure_category="transient",
        provider_ticket_status="on_hold",
        page=1,
        page_size=10,
    )
    assert breakage_helpdesk_failures["total"] == 1
    assert breakage_helpdesk_failures["by_provider"]["zendesk"] == 1
    assert breakage_helpdesk_failures["by_failure_category"]["transient"] == 1
    assert breakage_helpdesk_failures["by_provider_ticket_status"]["on_hold"] == 1
    assert breakage_helpdesk_failures["jobs"][0]["status"] == "failed"
    assert breakage_helpdesk_failures["jobs"][0]["error_code"] == "provider_timeout"
    assert breakage_helpdesk_failures["jobs"][0]["provider_ticket_status"] == "on_hold"

    breakage_helpdesk_failure_trends = ops.breakage_helpdesk_failure_trends(
        window_days=7,
        bucket_days=1,
        provider="zendesk",
        failure_category="transient",
        provider_ticket_status="on_hold",
    )
    assert breakage_helpdesk_failure_trends["aggregates"]["total_jobs"] == 1
    assert breakage_helpdesk_failure_trends["aggregates"]["failed_jobs"] == 1
    assert breakage_helpdesk_failure_trends["aggregates"]["failed_rate"] == pytest.approx(1.0)
    assert breakage_helpdesk_failure_trends["by_failure_category"]["transient"] == 1
    assert (
        breakage_helpdesk_failure_trends["filters"]["provider_ticket_status"]
        == "on_hold"
    )

    breakage_helpdesk_failure_triage = ops.breakage_helpdesk_failure_triage(
        window_days=7,
        provider="zendesk",
        failure_category="transient",
        provider_ticket_status="on_hold",
        top_n=5,
    )
    assert breakage_helpdesk_failure_triage["total_failed_jobs"] == 1
    assert breakage_helpdesk_failure_triage["replay_candidates_total"] == 1
    assert breakage_helpdesk_failure_triage["triaged_jobs"] == 0
    assert breakage_helpdesk_failure_triage["triage_rate"] == pytest.approx(0.0)
    assert breakage_helpdesk_failure_triage["hotspots"]["failure_categories"][0]["key"] == (
        "transient"
    )
    assert any(
        row.get("code") == "retry_with_backoff"
        for row in (breakage_helpdesk_failure_triage.get("triage_actions") or [])
    )

    breakage_helpdesk_failures_export_csv = ops.export_breakage_helpdesk_failures(
        window_days=7,
        provider="zendesk",
        failure_category="transient",
        provider_ticket_status="on_hold",
        export_format="csv",
    )
    assert breakage_helpdesk_failures_export_csv["media_type"] == "text/csv"
    assert (
        breakage_helpdesk_failures_export_csv["filename"]
        == "parallel-ops-breakage-helpdesk-failures.csv"
    )
    breakage_helpdesk_failures_csv_text = breakage_helpdesk_failures_export_csv[
        "content"
    ].decode("utf-8")
    assert "failure_category" in breakage_helpdesk_failures_csv_text
    assert "provider_timeout" in breakage_helpdesk_failures_csv_text

    breakage_helpdesk_failures_export_md = ops.export_breakage_helpdesk_failures(
        window_days=7,
        provider="zendesk",
        failure_category="transient",
        provider_ticket_status="on_hold",
        export_format="md",
    )
    assert breakage_helpdesk_failures_export_md["media_type"] == "text/markdown"
    assert breakage_helpdesk_failures_export_md["content"].decode("utf-8").startswith(
        "# Parallel Ops Breakage Helpdesk Failures"
    )
    breakage_helpdesk_failures_export_zip = ops.export_breakage_helpdesk_failures(
        window_days=7,
        provider="zendesk",
        failure_category="transient",
        provider_ticket_status="on_hold",
        export_format="zip",
    )
    assert breakage_helpdesk_failures_export_zip["media_type"] == "application/zip"
    assert (
        breakage_helpdesk_failures_export_zip["filename"]
        == "parallel-ops-breakage-helpdesk-failures.zip"
    )
    with ZipFile(io.BytesIO(breakage_helpdesk_failures_export_zip["content"])) as zf:
        names = set(zf.namelist())
        assert "failures.json" in names
        assert "failures.csv" in names
        assert "summary.md" in names

    metrics = ops.prometheus_metrics(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
    )
    assert "yuantus_parallel_doc_sync_jobs_total" in metrics
    assert "yuantus_parallel_workflow_runs_total" in metrics
    assert "yuantus_parallel_breakage_helpdesk_jobs_total" in metrics
    assert "yuantus_parallel_breakage_helpdesk_failed_rate" in metrics
    assert "yuantus_parallel_breakage_helpdesk_triage_rate" in metrics
    assert "yuantus_parallel_breakage_helpdesk_export_jobs_total" in metrics
    assert "yuantus_parallel_breakage_helpdesk_provider_failed_total" in metrics
    assert "yuantus_parallel_breakage_helpdesk_provider_failed_rate" in metrics
    assert "yuantus_parallel_breakage_helpdesk_replay_jobs_total" in metrics
    assert "yuantus_parallel_breakage_helpdesk_replay_batches_total" in metrics
    assert "yuantus_parallel_breakage_helpdesk_replay_failed_rate" in metrics
    assert "yuantus_parallel_breakage_helpdesk_replay_pending_total" in metrics
    assert "yuantus_parallel_breakage_helpdesk_by_provider" in metrics
    assert "yuantus_parallel_breakage_helpdesk_failed_by_failure_category" in metrics
    assert "yuantus_parallel_breakage_helpdesk_failure_trend_failed_total" in metrics
    assert "yuantus_parallel_doc_sync_by_direction" in metrics
    assert "yuantus_parallel_doc_sync_checkout_gate_threshold_hits_total" in metrics
    assert "yuantus_parallel_doc_sync_dead_letter_trend_delta" in metrics
    assert "yuantus_parallel_slo_hints_total" in metrics
    assert 'site_id="site-1"' in metrics
    assert 'provider="jira"' in metrics

    alerts = ops.alerts(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        level="warn",
    )
    assert alerts["status"] == "warning"
    assert alerts["total"] >= 1
    assert all(row.get("level") == "warn" for row in (alerts.get("hints") or []))
    assert alerts["by_code"].get("doc_sync_dead_letter_rate_high", 0) >= 1

    relaxed_alerts = ops.alerts(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        level="warn",
        overlay_cache_hit_rate_warn=0.1,
        overlay_cache_min_requests_warn=999,
        doc_sync_dead_letter_rate_warn=1.0,
        workflow_failed_rate_warn=1.0,
        breakage_open_rate_warn=1.0,
        breakage_helpdesk_failed_rate_warn=0.9,
        breakage_helpdesk_failed_total_warn=99,
        breakage_helpdesk_triage_coverage_warn=0.0,
        breakage_helpdesk_export_failed_total_warn=99,
        breakage_helpdesk_provider_failed_rate_warn=1.0,
        breakage_helpdesk_provider_failed_min_jobs_warn=99,
    )
    assert relaxed_alerts["status"] == "ok"
    assert relaxed_alerts["total"] == 0
    critical_alerts = ops.alerts(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        level="critical",
        overlay_cache_hit_rate_warn=0.1,
        overlay_cache_min_requests_warn=999,
        doc_sync_dead_letter_rate_warn=1.0,
        workflow_failed_rate_warn=1.0,
        breakage_open_rate_warn=1.0,
        breakage_helpdesk_failed_rate_warn=0.9,
        breakage_helpdesk_failed_total_warn=99,
        breakage_helpdesk_triage_coverage_warn=0.0,
        breakage_helpdesk_export_failed_total_warn=99,
        breakage_helpdesk_provider_failed_rate_warn=0.99,
        breakage_helpdesk_provider_failed_min_jobs_warn=99,
        breakage_helpdesk_provider_failed_rate_critical=0.5,
        breakage_helpdesk_provider_failed_min_jobs_critical=1,
    )
    assert critical_alerts["status"] == "warning"
    assert critical_alerts["by_code"].get("breakage_helpdesk_provider_failed_rate_critical", 0) >= 1

    export_json = ops.export_summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        export_format="json",
    )
    assert export_json["media_type"] == "application/json"
    assert export_json["filename"] == "parallel-ops-summary.json"
    assert b'"window_days": 7' in export_json["content"]
    export_json_locale = ops.export_summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        export_format="json",
        report_lang="zh_CN",
        report_type="parallel_ops_summary",
        locale_profile_id="rp-ops-zh",
    )
    export_json_locale_payload = json.loads(export_json_locale["content"].decode("utf-8"))
    assert export_json_locale_payload["locale"]["id"] == "rp-ops-zh"
    assert export_json_locale_payload["locale"]["lang"] == "zh_CN"

    export_csv = ops.export_summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        export_format="csv",
    )
    assert export_csv["media_type"] == "text/csv"
    assert export_csv["filename"] == "parallel-ops-summary.csv"
    csv_text = export_csv["content"].decode("utf-8")
    assert "metric,value" in csv_text
    assert "doc_sync.total,2" in csv_text
    assert "breakages.helpdesk.total_jobs,2" in csv_text
    assert "breakages.helpdesk.replay_pending_jobs" in csv_text

    export_md = ops.export_summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        export_format="md",
    )
    assert export_md["media_type"] == "text/markdown"
    assert export_md["filename"] == "parallel-ops-summary.md"
    md_text = export_md["content"].decode("utf-8")
    assert md_text.startswith("# Parallel Ops Summary")
    assert "| doc_sync.total | 2 |" in md_text
    export_md_locale = ops.export_summary(
        window_days=7,
        site_id="site-1",
        target_object="ECO",
        template_key="tpl-ops",
        export_format="md",
        report_lang="zh_CN",
        report_type="parallel_ops_summary",
        locale_profile_id="rp-ops-zh",
    )
    md_locale_text = export_md_locale["content"].decode("utf-8")
    assert "## Locale" in md_locale_text
    assert "rp-ops-zh" in md_locale_text

    with pytest.raises(ValueError, match="window_days"):
        ops.summary(window_days=10)
    with pytest.raises(ValueError, match="bucket_days must be one of"):
        ops.trends(window_days=7, bucket_days=2)
    with pytest.raises(ValueError, match="bucket_days must be <= window_days"):
        ops.trends(window_days=7, bucket_days=14)
    with pytest.raises(ValueError, match="doc_sync_dead_letter_rate_warn must be between 0 and 1"):
        ops.summary(window_days=7, doc_sync_dead_letter_rate_warn=1.2)
    with pytest.raises(ValueError, match="breakage_helpdesk_failed_rate_warn must be between 0 and 1"):
        ops.summary(window_days=7, breakage_helpdesk_failed_rate_warn=1.2)
    with pytest.raises(ValueError, match="breakage_helpdesk_failed_total_warn must be >= 0"):
        ops.summary(window_days=7, breakage_helpdesk_failed_total_warn=-1)
    with pytest.raises(ValueError, match="breakage_helpdesk_triage_coverage_warn must be between 0 and 1"):
        ops.summary(window_days=7, breakage_helpdesk_triage_coverage_warn=1.2)
    with pytest.raises(ValueError, match="breakage_helpdesk_export_failed_total_warn must be >= 0"):
        ops.summary(window_days=7, breakage_helpdesk_export_failed_total_warn=-1)
    with pytest.raises(ValueError, match="breakage_helpdesk_provider_failed_rate_warn must be between 0 and 1"):
        ops.summary(window_days=7, breakage_helpdesk_provider_failed_rate_warn=1.2)
    with pytest.raises(ValueError, match="breakage_helpdesk_provider_failed_min_jobs_warn must be >= 0"):
        ops.summary(window_days=7, breakage_helpdesk_provider_failed_min_jobs_warn=-1)
    with pytest.raises(ValueError, match="breakage_helpdesk_provider_failed_rate_critical must be between 0 and 1"):
        ops.summary(window_days=7, breakage_helpdesk_provider_failed_rate_critical=1.2)
    with pytest.raises(ValueError, match="breakage_helpdesk_provider_failed_min_jobs_critical must be >= 0"):
        ops.summary(window_days=7, breakage_helpdesk_provider_failed_min_jobs_critical=-1)
    with pytest.raises(ValueError, match="breakage_helpdesk_replay_failed_rate_warn must be between 0 and 1"):
        ops.summary(window_days=7, breakage_helpdesk_replay_failed_rate_warn=1.2)
    with pytest.raises(ValueError, match="breakage_helpdesk_replay_failed_total_warn must be >= 0"):
        ops.summary(window_days=7, breakage_helpdesk_replay_failed_total_warn=-1)
    with pytest.raises(ValueError, match="breakage_helpdesk_replay_pending_total_warn must be >= 0"):
        ops.summary(window_days=7, breakage_helpdesk_replay_pending_total_warn=-1)
    with pytest.raises(ValueError, match="doc_sync_checkout_gate_max_failed_warn must be >= 0"):
        ops.summary(window_days=7, doc_sync_checkout_gate_max_failed_warn=-1)
    with pytest.raises(ValueError, match="doc_sync_dead_letter_trend_delta_warn must be >= 0"):
        ops.summary(window_days=7, doc_sync_dead_letter_trend_delta_warn=-1)
    with pytest.raises(
        ValueError, match="doc_sync_checkout_gate_block_on_dead_letter_only must be a boolean"
    ):
        ops.summary(window_days=7, doc_sync_checkout_gate_block_on_dead_letter_only="oops")
    with pytest.raises(ValueError, match="overlay_cache_min_requests_warn must be >= 0"):
        ops.summary(window_days=7, overlay_cache_min_requests_warn=-1)
    with pytest.raises(ValueError, match="page_size"):
        ops.doc_sync_failures(window_days=7, page_size=500)
    with pytest.raises(ValueError, match="page_size"):
        ops.breakage_helpdesk_failures(window_days=7, page_size=500)
    with pytest.raises(ValueError, match="bucket_days must be one of"):
        ops.breakage_helpdesk_failure_trends(window_days=7, bucket_days=2)
    with pytest.raises(ValueError, match="top_n must be between 1 and 50"):
        ops.breakage_helpdesk_failure_triage(window_days=7, top_n=0)
    with pytest.raises(ValueError, match="level must be one of"):
        ops.alerts(window_days=7, level="oops")
    with pytest.raises(ValueError, match="export_format must be json, csv or md"):
        ops.export_summary(window_days=7, export_format="xlsx")
    with pytest.raises(ValueError, match="export_format must be json, csv or md"):
        ops.export_trends(window_days=7, bucket_days=1, export_format="xlsx")
    with pytest.raises(ValueError, match="export_format must be json, csv, md or zip"):
        ops.export_breakage_helpdesk_failures(window_days=7, export_format="xlsx")


def test_parallel_ops_breakage_helpdesk_failures_export_job_lifecycle_and_cleanup(session):
    breakage_service = BreakageIncidentService(session)
    incident = breakage_service.create_incident(
        description="parallel-ops-helpdesk-export-job",
        severity="high",
        status="open",
        product_item_id="p-bh-job-1",
        bom_line_item_id="bom-bh-job-1",
    )
    session.commit()
    job = breakage_service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=8,
        provider="zendesk",
        idempotency_key="ops-helpdesk-export-job-1",
    )
    session.commit()
    breakage_service.record_helpdesk_sync_result(
        incident.id,
        sync_status="failed",
        job_id=job.id,
        error_code="provider_timeout",
        error_message="timeout",
        user_id=8,
    )
    failed_job = session.get(ConversionJob, job.id)
    assert failed_job is not None
    payload = dict(failed_job.payload or {})
    helpdesk_sync = (
        dict(payload.get("helpdesk_sync"))
        if isinstance(payload.get("helpdesk_sync"), dict)
        else {}
    )
    helpdesk_sync["provider_ticket_status"] = "on_hold"
    payload["helpdesk_sync"] = helpdesk_sync
    payload["provider_ticket_status"] = "on_hold"
    failed_job.payload = payload
    session.add(failed_job)
    session.commit()

    ops = ParallelOpsOverviewService(session)
    enqueued = ops.enqueue_breakage_helpdesk_failures_export_job(
        window_days=7,
        provider="zendesk",
        failure_category="transient",
        provider_ticket_status="on_hold",
        export_format="csv",
        execute_immediately=False,
        user_id=8,
    )
    assert enqueued["job_id"]
    assert enqueued["status"] in {"pending", "processing"}
    assert enqueued["download_ready"] is False

    executed = ops.execute_breakage_helpdesk_failures_export_job(enqueued["job_id"], user_id=8)
    session.commit()
    assert executed["status"] == "completed"
    assert executed["download_ready"] is True
    assert executed["filename"] == "parallel-ops-breakage-helpdesk-failures.csv"

    status = ops.get_breakage_helpdesk_failures_export_job(enqueued["job_id"])
    assert status["status"] == "completed"
    assert status["download_ready"] is True

    downloaded = ops.download_breakage_helpdesk_failures_export_job(enqueued["job_id"])
    assert downloaded["media_type"] == "text/csv"
    csv_text = downloaded["content"].decode("utf-8")
    assert "provider_timeout" in csv_text

    job_id = str(enqueued["job_id"])
    row = session.get(ConversionJob, job_id)
    assert row is not None
    row.completed_at = datetime.utcnow() - timedelta(hours=25)
    session.add(row)
    session.commit()

    cleanup = ops.cleanup_expired_breakage_helpdesk_failures_export_results(
        ttl_hours=24,
        limit=50,
        user_id=8,
    )
    session.commit()
    assert cleanup["expired_jobs"] >= 1
    assert job_id in set(cleanup["job_ids"])

    expired_status = ops.get_breakage_helpdesk_failures_export_job(job_id)
    assert expired_status["download_ready"] is False
    assert expired_status["sync_status"] == "expired"
    with pytest.raises(ValueError, match="Export content missing for job"):
        ops.download_breakage_helpdesk_failures_export_job(job_id)


def test_parallel_ops_breakage_helpdesk_failure_triage_apply_persists_payload(session):
    breakage_service = BreakageIncidentService(session)
    incident = breakage_service.create_incident(
        description="parallel-ops-helpdesk-triage-apply",
        severity="high",
        status="open",
        product_item_id="p-bh-triage-1",
        bom_line_item_id="bom-bh-triage-1",
    )
    session.commit()
    job = breakage_service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=8,
        provider="jira",
        idempotency_key="ops-helpdesk-triage-apply-1",
    )
    session.commit()
    breakage_service.record_helpdesk_sync_result(
        incident.id,
        sync_status="failed",
        job_id=job.id,
        error_code="provider_timeout",
        error_message="timeout",
        user_id=8,
    )
    session.commit()

    ops = ParallelOpsOverviewService(session)
    applied = ops.apply_breakage_helpdesk_failure_triage(
        triage_status="in_progress",
        job_ids=[job.id],
        triage_owner="ops-l2",
        root_cause="provider_rate_limit",
        resolution="retry_with_backoff",
        note="triage note",
        tags=["hot", "provider", "hot"],
        user_id=8,
    )
    session.commit()
    assert applied["updated_total"] == 1
    assert applied["skipped_not_found_total"] == 0
    assert applied["updated_jobs"][0]["id"] == job.id
    assert applied["updated_jobs"][0]["triage_status"] == "in_progress"

    row = session.get(ConversionJob, job.id)
    assert row is not None
    payload = row.payload if isinstance(row.payload, dict) else {}
    triage = payload.get("triage") if isinstance(payload.get("triage"), dict) else {}
    assert triage.get("status") == "in_progress"
    assert triage.get("owner") == "ops-l2"
    assert triage.get("root_cause") == "provider_rate_limit"
    assert triage.get("resolution") == "retry_with_backoff"
    assert triage.get("note") == "triage note"
    assert triage.get("tags") == ["hot", "provider"]

    triage_summary = ops.breakage_helpdesk_failure_triage(
        window_days=7,
        provider="jira",
        top_n=5,
    )
    assert triage_summary["total_failed_jobs"] == 1
    assert triage_summary["triaged_jobs"] == 1
    assert triage_summary["triage_rate"] == pytest.approx(1.0)
    assert triage_summary["by_triage_status"]["in_progress"] == 1


def test_parallel_ops_breakage_helpdesk_failure_replay_enqueue_creates_jobs(session):
    breakage_service = BreakageIncidentService(session)
    incident = breakage_service.create_incident(
        description="parallel-ops-helpdesk-replay-enqueue",
        severity="high",
        status="open",
        product_item_id="p-bh-replay-1",
        bom_line_item_id="bom-bh-replay-1",
    )
    session.commit()
    job = breakage_service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=9,
        provider="zendesk",
        idempotency_key="ops-helpdesk-replay-src-1",
    )
    session.commit()
    breakage_service.record_helpdesk_sync_result(
        incident.id,
        sync_status="failed",
        job_id=job.id,
        error_code="provider_timeout",
        error_message="timeout",
        user_id=9,
    )
    session.commit()

    ops = ParallelOpsOverviewService(session)
    replay = ops.enqueue_breakage_helpdesk_failure_replay_jobs(
        job_ids=[job.id],
        limit=10,
        user_id=9,
    )
    session.commit()

    assert replay["batch_id"]
    assert replay["created_jobs_total"] == 1
    assert replay["errors_total"] == 0
    assert replay["created_jobs"][0]["batch_id"] == replay["batch_id"]
    assert replay["created_jobs"][0]["source_job_id"] == str(job.id)
    replay_job_id = str(replay["created_jobs"][0]["job_id"])
    replay_job = session.get(ConversionJob, replay_job_id)
    assert replay_job is not None
    assert replay_job.task_type == "breakage_helpdesk_sync_stub"
    replay_payload = replay_job.payload if isinstance(replay_job.payload, dict) else {}
    replay_metadata = (
        replay_payload.get("metadata")
        if isinstance(replay_payload.get("metadata"), dict)
        else {}
    )
    replay_info = (
        replay_metadata.get("replay")
        if isinstance(replay_metadata.get("replay"), dict)
        else {}
    )
    assert replay_info.get("source_job_id") == str(job.id)
    assert replay_info.get("batch_id") == replay["batch_id"]
    assert replay_info.get("requested_by_id") == 9
    replay_sync = (
        replay_payload.get("helpdesk_sync")
        if isinstance(replay_payload.get("helpdesk_sync"), dict)
        else {}
    )
    assert replay_sync.get("provider") == "zendesk"
    assert replay_sync.get("sync_status") == "queued"

    replay_batch = ops.get_breakage_helpdesk_failure_replay_batch(
        replay["batch_id"],
        page=1,
        page_size=10,
    )
    assert replay_batch["batch_id"] == replay["batch_id"]
    assert replay_batch["total"] >= 1
    assert replay_batch["by_provider"]["zendesk"] >= 1
    assert replay_batch["jobs"][0]["source_job_id"] == str(job.id)
    assert replay_batch["jobs"][0]["batch_id"] == replay["batch_id"]

    replay_batches = ops.list_breakage_helpdesk_failure_replay_batches(
        window_days=7,
        provider="zendesk",
        page=1,
        page_size=20,
    )
    assert replay_batches["total_batches"] >= 1
    assert replay_batches["by_provider"]["zendesk"] >= 1
    assert replay_batches["batches"][0]["batch_id"] == replay["batch_id"]

    replay_export_json = ops.export_breakage_helpdesk_failure_replay_batch(
        replay["batch_id"],
        export_format="json",
    )
    assert replay_export_json["media_type"] == "application/json"
    assert b'"batch_id"' in replay_export_json["content"]
    replay_export_csv = ops.export_breakage_helpdesk_failure_replay_batch(
        replay["batch_id"],
        export_format="csv",
    )
    assert replay_export_csv["media_type"] == "text/csv"
    assert "batch_id,job_id,source_job_id" in replay_export_csv["content"].decode("utf-8")
    replay_export_md = ops.export_breakage_helpdesk_failure_replay_batch(
        replay["batch_id"],
        export_format="md",
    )
    assert replay_export_md["media_type"] == "text/markdown"
    assert replay_export_md["content"].decode("utf-8").startswith(
        "# Parallel Ops Breakage Helpdesk Replay Batch"
    )
    with pytest.raises(ValueError, match="export_format must be json, csv or md"):
        ops.export_breakage_helpdesk_failure_replay_batch(
            replay["batch_id"],
            export_format="xlsx",
        )

    replay_payload_failed = dict(replay_job.payload or {})
    replay_sync_failed = (
        dict(replay_payload_failed.get("helpdesk_sync"))
        if isinstance(replay_payload_failed.get("helpdesk_sync"), dict)
        else {}
    )
    replay_sync_failed["sync_status"] = "failed"
    replay_payload_failed["helpdesk_sync"] = replay_sync_failed
    replay_job.status = "failed"
    replay_job.payload = replay_payload_failed
    session.add(replay_job)
    session.commit()

    replay_second = ops.enqueue_breakage_helpdesk_failure_replay_jobs(
        job_ids=[job.id],
        limit=10,
        user_id=9,
    )
    session.commit()
    assert replay_second["created_jobs_total"] == 1
    replay_second_job_id = str(replay_second["created_jobs"][0]["job_id"])
    replay_second_job = session.get(ConversionJob, replay_second_job_id)
    assert replay_second_job is not None

    replay_trends = ops.breakage_helpdesk_replay_trends(
        window_days=7,
        bucket_days=1,
        provider="zendesk",
    )
    assert replay_trends["aggregates"]["total_jobs"] >= 2
    assert replay_trends["aggregates"]["failed_jobs"] >= 1
    assert replay_trends["aggregates"]["total_batches"] >= 2
    assert replay_trends["by_provider"]["zendesk"] >= 2

    replay_trends_export_json = ops.export_breakage_helpdesk_replay_trends(
        window_days=7,
        bucket_days=1,
        provider="zendesk",
        export_format="json",
    )
    assert replay_trends_export_json["media_type"] == "application/json"
    assert b'"total_batches"' in replay_trends_export_json["content"]
    replay_trends_export_csv = ops.export_breakage_helpdesk_replay_trends(
        window_days=7,
        bucket_days=1,
        provider="zendesk",
        export_format="csv",
    )
    assert replay_trends_export_csv["media_type"] == "text/csv"
    assert "bucket_start,bucket_end,total_jobs,failed_jobs" in replay_trends_export_csv[
        "content"
    ].decode("utf-8")
    replay_trends_export_md = ops.export_breakage_helpdesk_replay_trends(
        window_days=7,
        bucket_days=1,
        provider="zendesk",
        export_format="md",
    )
    assert replay_trends_export_md["media_type"] == "text/markdown"
    assert replay_trends_export_md["content"].decode("utf-8").startswith(
        "# Parallel Ops Breakage Helpdesk Replay Trends"
    )

    replay_strict = ops.summary(
        window_days=7,
        breakage_helpdesk_replay_failed_rate_warn=0.1,
        breakage_helpdesk_replay_failed_total_warn=0,
        breakage_helpdesk_replay_pending_total_warn=0,
    )
    replay_hint_codes = {row.get("code") for row in (replay_strict.get("slo_hints") or [])}
    assert "breakage_helpdesk_replay_failed_rate_high" in replay_hint_codes
    assert "breakage_helpdesk_replay_failed_total_high" in replay_hint_codes
    assert "breakage_helpdesk_replay_pending_total_high" in replay_hint_codes

    replay_alerts = ops.alerts(
        window_days=7,
        level="warn",
        breakage_helpdesk_replay_failed_rate_warn=0.1,
        breakage_helpdesk_replay_failed_total_warn=0,
        breakage_helpdesk_replay_pending_total_warn=0,
    )
    assert replay_alerts["status"] == "warning"
    assert replay_alerts["by_code"].get("breakage_helpdesk_replay_failed_rate_high", 0) >= 1
    assert replay_alerts["by_code"].get("breakage_helpdesk_replay_failed_total_high", 0) >= 1
    assert replay_alerts["by_code"].get("breakage_helpdesk_replay_pending_total_high", 0) >= 1

    replay_job.created_at = datetime.utcnow() - timedelta(hours=200)
    replay_second_payload = dict(replay_second_job.payload or {})
    replay_second_sync = (
        dict(replay_second_payload.get("helpdesk_sync"))
        if isinstance(replay_second_payload.get("helpdesk_sync"), dict)
        else {}
    )
    replay_second_sync["sync_status"] = "completed"
    replay_second_payload["helpdesk_sync"] = replay_second_sync
    replay_second_job.status = "completed"
    replay_second_job.payload = replay_second_payload
    replay_second_job.created_at = datetime.utcnow() - timedelta(hours=200)
    session.add_all([replay_job, replay_second_job])
    session.commit()

    replay_cleanup_dry_run = ops.cleanup_breakage_helpdesk_failure_replay_batches(
        ttl_hours=24,
        limit=20,
        dry_run=True,
    )
    assert replay_cleanup_dry_run["dry_run"] is True
    assert replay_cleanup_dry_run["archived_jobs"] >= 2
    replay_batches_after_dry_run = ops.list_breakage_helpdesk_failure_replay_batches(
        window_days=90,
        provider="zendesk",
        page=1,
        page_size=20,
    )
    assert replay_batches_after_dry_run["total_batches"] >= 2

    replay_cleanup = ops.cleanup_breakage_helpdesk_failure_replay_batches(
        ttl_hours=24,
        limit=20,
    )
    session.commit()
    assert replay_cleanup["dry_run"] is False
    assert replay_cleanup["archived_jobs"] >= 2
    assert replay["batch_id"] in set(replay_cleanup["batch_ids"])
    assert replay_second["batch_id"] in set(replay_cleanup["batch_ids"])

    replay_batches_after_cleanup = ops.list_breakage_helpdesk_failure_replay_batches(
        window_days=90,
        provider="zendesk",
        page=1,
        page_size=20,
    )
    assert replay_batches_after_cleanup["total_batches"] == 0
    replay_trends_after_cleanup = ops.breakage_helpdesk_replay_trends(
        window_days=90,
        bucket_days=1,
        provider="zendesk",
    )
    assert replay_trends_after_cleanup["aggregates"]["total_jobs"] == 0


def test_parallel_ops_breakage_helpdesk_failure_replay_batch_not_found(session):
    ops = ParallelOpsOverviewService(session)
    with pytest.raises(ValueError, match="Replay batch not found"):
        ops.get_breakage_helpdesk_failure_replay_batch("bh-replay-missing")
    with pytest.raises(ValueError, match="Replay batch not found"):
        ops.export_breakage_helpdesk_failure_replay_batch("bh-replay-missing")
    with pytest.raises(ValueError, match="job_status must be one of"):
        ops.list_breakage_helpdesk_failure_replay_batches(window_days=7, job_status="oops")
    with pytest.raises(ValueError, match="job_status must be one of"):
        ops.breakage_helpdesk_replay_trends(window_days=7, bucket_days=1, job_status="oops")
    with pytest.raises(ValueError, match="sync_status must be one of"):
        ops.breakage_helpdesk_replay_trends(window_days=7, bucket_days=1, sync_status="oops")
    with pytest.raises(ValueError, match="bucket_days must be <= window_days"):
        ops.breakage_helpdesk_replay_trends(window_days=7, bucket_days=14)
    with pytest.raises(ValueError, match="export_format must be json, csv or md"):
        ops.export_breakage_helpdesk_replay_trends(
            window_days=7,
            bucket_days=1,
            export_format="xlsx",
        )
    with pytest.raises(ValueError, match="ttl_hours"):
        ops.cleanup_breakage_helpdesk_failure_replay_batches(ttl_hours=0)


def test_parallel_ops_breakage_helpdesk_export_jobs_overview_returns_aggregates(session):
    breakage_service = BreakageIncidentService(session)
    incident = breakage_service.create_incident(
        description="parallel-ops-helpdesk-export-overview",
        severity="high",
        status="open",
        product_item_id="p-bh-overview-1",
        bom_line_item_id="bom-bh-overview-1",
    )
    session.commit()
    job = breakage_service.enqueue_helpdesk_stub_sync(
        incident.id,
        user_id=10,
        provider="zendesk",
        idempotency_key="ops-helpdesk-overview-src-1",
    )
    session.commit()
    breakage_service.record_helpdesk_sync_result(
        incident.id,
        sync_status="failed",
        job_id=job.id,
        error_code="provider_timeout",
        error_message="timeout",
        user_id=10,
    )
    session.commit()

    ops = ParallelOpsOverviewService(session)
    export_job = ops.enqueue_breakage_helpdesk_failures_export_job(
        window_days=7,
        provider="zendesk",
        failure_category="transient",
        export_format="csv",
        execute_immediately=False,
        user_id=10,
    )
    ops.execute_breakage_helpdesk_failures_export_job(export_job["job_id"], user_id=10)
    session.commit()

    overview = ops.breakage_helpdesk_failures_export_jobs_overview(
        window_days=7,
        provider="zendesk",
        failure_category="transient",
        export_format="csv",
        page=1,
        page_size=20,
    )
    assert overview["total"] >= 1
    assert overview["by_provider"]["zendesk"] >= 1
    assert overview["by_failure_category"]["transient"] >= 1
    assert overview["by_export_format"]["csv"] >= 1
    assert overview["by_job_status"]["completed"] >= 1
    assert overview["duration_seconds"]["count"] >= 1
    assert overview["jobs"][0]["provider"] == "zendesk"
    assert overview["jobs"][0]["failure_category"] == "transient"
    assert overview["jobs"][0]["export_format"] == "csv"

    with pytest.raises(ValueError, match="export_format must be json, csv, md or zip"):
        ops.breakage_helpdesk_failures_export_jobs_overview(window_days=7, export_format="xlsx")
