from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from yuantus.api.app import create_app
from yuantus.api.dependencies.auth import get_current_user
from yuantus.config import get_settings
from yuantus.database import get_db


@pytest.fixture(autouse=True)
def _optional_auth_mode_for_router_tests():
    settings = get_settings()
    previous = settings.AUTH_MODE
    settings.AUTH_MODE = "optional"
    try:
        yield
    finally:
        settings.AUTH_MODE = previous


def _client_with_user(user):
    mock_db_session = MagicMock()

    def override_get_db():
        try:
            yield mock_db_session
        finally:
            pass

    def override_get_current_user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    return TestClient(app), mock_db_session


def test_workorder_doc_export_pdf_returns_pdf_payload():
    user = SimpleNamespace(id=1, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_workorder_docs_router.WorkorderDocumentPackService"
    ) as service_cls:
        service_cls.return_value.export_pack.return_value = {
            "manifest": {
                "generated_at": "2026-02-28T00:00:00Z",
                "routing_id": "r-1",
                "operation_id": "op-1",
                "count": 1,
                "scope_summary": {"operation": 1, "routing": 0},
                "export_meta": {
                    "job_no": "wo-1",
                    "operator_id": 1,
                    "operator_name": "Alice",
                    "exported_by": "alice@example.com",
                },
                "documents": [
                    {
                        "document_item_id": "doc-1",
                        "operation_id": "op-1",
                        "document_scope": "operation",
                        "inherit_to_children": True,
                        "visible_in_production": True,
                    }
                ],
            },
            "zip_bytes": b"PK\x03\x04",
        }
        resp = client.get(
            "/api/v1/workorder-docs/export?routing_id=r-1&operation_id=op-1&export_format=pdf"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/pdf")
    assert 'filename="workorder-doc-pack.pdf"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.content.startswith(b"%PDF")


def test_workorder_doc_export_rejects_unknown_format():
    user = SimpleNamespace(id=1, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_workorder_docs_router.WorkorderDocumentPackService"
    ) as service_cls:
        service_cls.return_value.export_pack.return_value = {
            "manifest": {"routing_id": "r-1", "documents": [], "count": 0},
            "zip_bytes": b"PK\x03\x04",
        }
        resp = client.get(
            "/api/v1/workorder-docs/export?routing_id=r-1&export_format=xlsx"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "workorder_export_invalid_format"
    assert detail.get("context", {}).get("export_format") == "xlsx"


def test_workorder_doc_export_json_includes_export_meta():
    user = SimpleNamespace(id=2, roles=["admin"], is_superuser=False, email="u2@example.com")
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_workorder_docs_router.WorkorderDocumentPackService"
    ) as service_cls:
        service_cls.return_value.export_pack.return_value = {
            "manifest": {
                "generated_at": "2026-02-28T00:00:00Z",
                "routing_id": "r-1",
                "operation_id": "op-1",
                "count": 1,
                "scope_summary": {"operation": 1, "routing": 0},
                "export_meta": {
                    "job_no": "wo-1",
                    "operator_id": 2,
                    "operator_name": "Bob",
                    "exported_by": "u2@example.com",
                },
                "documents": [],
            },
            "zip_bytes": b"PK\x03\x04",
        }
        resp = client.get(
            "/api/v1/workorder-docs/export?routing_id=r-1&operation_id=op-1&export_format=json&job_no=wo-1&operator_name=Bob"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["export_meta"]["job_no"] == "wo-1"
    assert data["scope_summary"]["operation"] == 1


def test_workorder_doc_export_passes_locale_query_contract():
    user = SimpleNamespace(id=2, roles=["admin"], is_superuser=False, email="u2@example.com")
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_workorder_docs_router.WorkorderDocumentPackService"
    ) as service_cls:
        service_cls.return_value.export_pack.return_value = {
            "manifest": {
                "routing_id": "r-1",
                "documents": [],
                "count": 0,
                "scope_summary": {"routing": 0, "operation": 0},
                "export_meta": {},
                "locale": {"id": "rp-1", "lang": "zh_CN"},
            },
            "zip_bytes": b"PK\x03\x04",
        }
        resp = client.get(
            "/api/v1/workorder-docs/export"
            "?routing_id=r-1"
            "&export_format=json"
            "&report_lang=zh_CN"
            "&report_type=workorder_doc_pack"
            "&locale_profile_id=rp-1"
        )

    assert resp.status_code == 200
    kwargs = service_cls.return_value.export_pack.call_args.kwargs
    assert kwargs["export_meta"]["report_lang"] == "zh_CN"
    assert kwargs["export_meta"]["report_type"] == "workorder_doc_pack"
    assert kwargs["export_meta"]["locale_profile_id"] == "rp-1"
    assert resp.json()["locale"]["id"] == "rp-1"


def test_breakage_helpdesk_sync_endpoint_returns_job():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.enqueue_helpdesk_stub_sync.return_value = SimpleNamespace(
            id="job-1",
            task_type="breakage_helpdesk_sync_stub",
            status="pending",
            created_at=None,
        )
        resp = client.post(
            "/api/v1/breakages/inc-1/helpdesk-sync",
            json={
                "metadata_json": {"channel": "qa"},
                "provider": "jira",
                "integration_json": {
                    "mode": "http",
                    "base_url": "https://jira.example.test",
                    "auth_type": "bearer",
                    "token": "jira-token",
                    "jira_project_key": "OPS",
                },
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["incident_id"] == "inc-1"
    assert data["job_id"] == "job-1"
    assert data["task_type"] == "breakage_helpdesk_sync_stub"
    assert db.commit.called
    service_cls.return_value.enqueue_helpdesk_stub_sync.assert_called_once_with(
        "inc-1",
        user_id=3,
        metadata_json={"channel": "qa"},
        provider="jira",
        integration_json={
            "mode": "http",
            "base_url": "https://jira.example.test",
            "auth_type": "bearer",
            "token": "jira-token",
            "jira_project_key": "OPS",
        },
        idempotency_key=None,
        retry_max_attempts=None,
    )


def test_breakage_helpdesk_sync_status_endpoint_returns_payload():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.get_helpdesk_sync_status.return_value = {
            "incident_id": "inc-1",
            "sync_status": "pending",
            "external_ticket_id": None,
            "last_job": None,
            "jobs": [],
        }
        resp = client.get("/api/v1/breakages/inc-1/helpdesk-sync/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == "inc-1"
    assert body["sync_status"] == "pending"
    assert body["operator_id"] == 3


def test_breakage_helpdesk_sync_execute_endpoint_returns_payload():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.execute_helpdesk_sync.return_value = {
            "incident_id": "inc-1",
            "sync_status": "completed",
            "external_ticket_id": "HD-2001",
            "last_job": {"id": "job-1", "status": "completed"},
            "jobs": [{"id": "job-1"}],
        }
        resp = client.post(
            "/api/v1/breakages/inc-1/helpdesk-sync/execute",
            json={"job_id": "job-1", "simulate_status": "completed"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == "inc-1"
    assert body["sync_status"] == "completed"
    assert body["external_ticket_id"] == "HD-2001"
    assert body["operator_id"] == 3
    assert db.commit.called
    service_cls.return_value.execute_helpdesk_sync.assert_called_once_with(
        "inc-1",
        simulate_status="completed",
        job_id="job-1",
        external_ticket_id=None,
        error_code=None,
        error_message=None,
        metadata_json=None,
        user_id=3,
    )


def test_breakage_helpdesk_sync_execute_invalid_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.execute_helpdesk_sync.side_effect = ValueError(
            "simulate_status must be one of: completed, failed"
        )
        resp = client.post(
            "/api/v1/breakages/inc-1/helpdesk-sync/execute",
            json={"simulate_status": "queued"},
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_helpdesk_sync_invalid"
    assert detail.get("context", {}).get("incident_id") == "inc-1"
    assert detail.get("context", {}).get("simulate_status") == "queued"


def test_breakage_helpdesk_sync_result_endpoint_returns_payload():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.record_helpdesk_sync_result.return_value = {
            "incident_id": "inc-1",
            "sync_status": "completed",
            "external_ticket_id": "HD-1",
            "last_job": {"id": "job-1", "status": "completed"},
            "jobs": [{"id": "job-1"}],
        }
        resp = client.post(
            "/api/v1/breakages/inc-1/helpdesk-sync/result",
            json={
                "job_id": "job-1",
                "sync_status": "completed",
                "external_ticket_id": "HD-1",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == "inc-1"
    assert body["sync_status"] == "completed"
    assert body["external_ticket_id"] == "HD-1"
    assert body["operator_id"] == 3
    assert db.commit.called
    service_cls.return_value.record_helpdesk_sync_result.assert_called_once_with(
        "inc-1",
        sync_status="completed",
        job_id="job-1",
        external_ticket_id="HD-1",
        error_code=None,
        error_message=None,
        metadata_json=None,
        user_id=3,
    )


def test_breakage_helpdesk_sync_result_invalid_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.record_helpdesk_sync_result.side_effect = ValueError(
            "sync_status must be one of: completed, failed"
        )
        resp = client.post(
            "/api/v1/breakages/inc-1/helpdesk-sync/result",
            json={"sync_status": "queued"},
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_helpdesk_sync_invalid"
    assert detail.get("context", {}).get("incident_id") == "inc-1"
    assert detail.get("context", {}).get("sync_status") == "queued"


def test_breakage_helpdesk_ticket_update_endpoint_returns_payload():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.apply_helpdesk_ticket_update.return_value = {
            "incident_id": "inc-1",
            "incident_status": "in_progress",
            "incident_responsibility": "ops-team",
            "sync_status": "in_progress",
            "external_ticket_id": "HD-1",
            "event_id": "evt-1",
            "idempotent_replay": False,
            "last_job": {"id": "job-1", "status": "processing"},
            "jobs": [{"id": "job-1"}],
        }
        resp = client.post(
            "/api/v1/breakages/inc-1/helpdesk-sync/ticket-update",
            json={
                "job_id": "job-1",
                "event_id": "evt-1",
                "provider_ticket_status": "working",
                "provider_updated_at": "2026-03-06T09:00:00+08:00",
                "provider_assignee": "ops-team",
                "provider_payload": {"source": "jira-webhook"},
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == "inc-1"
    assert body["incident_status"] == "in_progress"
    assert body["sync_status"] == "in_progress"
    assert body["event_id"] == "evt-1"
    assert body["idempotent_replay"] is False
    assert body["operator_id"] == 3
    assert db.commit.called
    service_cls.return_value.apply_helpdesk_ticket_update.assert_called_once_with(
        "inc-1",
        provider_ticket_status="working",
        job_id="job-1",
        event_id="evt-1",
        external_ticket_id=None,
        provider=None,
        provider_updated_at=datetime(2026, 3, 6, 1, 0, 0),
        provider_assignee="ops-team",
        provider_payload={"source": "jira-webhook"},
        incident_status=None,
        incident_responsibility=None,
        user_id=3,
        # Tier-B #3 §3.4: the route now also forwards the
        # default-OFF auto-loopback opt-in + the loopback actor.
        auto_loopback=False,
        loopback_user_id=3,
    )


def test_breakage_helpdesk_ticket_update_invalid_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.apply_helpdesk_ticket_update.side_effect = ValueError(
            "provider_ticket_status must not be empty"
        )
        resp = client.post(
            "/api/v1/breakages/inc-1/helpdesk-sync/ticket-update",
            json={"provider_ticket_status": "  "},
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_helpdesk_sync_invalid"
    assert detail.get("context", {}).get("incident_id") == "inc-1"
    assert detail.get("context", {}).get("provider_ticket_status") == "  "


def test_breakage_helpdesk_ticket_update_invalid_datetime_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    resp = client.post(
        "/api/v1/breakages/inc-1/helpdesk-sync/ticket-update",
        json={
            "provider_ticket_status": "in_progress",
            "provider_updated_at": "not-a-datetime",
        },
    )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "invalid_datetime"
    assert detail.get("context", {}).get("field") == "provider_updated_at"


def test_breakage_cockpit_endpoint_returns_payload():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.cockpit.return_value = {
            "total": 1,
            "filters": {"bom_line_item_id": "bom-1"},
            "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
            "kpis": {"incidents_total": 1, "open_incidents": 1},
            "incidents": [
                {
                    "id": "inc-1",
                    "status": "open",
                    "severity": "high",
                    "product_item_id": "p-1",
                    "bom_line_item_id": "bom-1",
                }
            ],
            "metrics": {
                "total": 1,
                "by_mbom_id": {"mbom-1": 1},
                "by_routing_id": {"routing-1": 1},
                "top_mbom_ids": [{"mbom_id": "mbom-1", "count": 1}],
                "top_routing_ids": [{"routing_id": "routing-1", "count": 1}],
            },
            "helpdesk_sync_summary": {"total_jobs": 1, "failed_jobs": 0},
        }
        resp = client.get(
            "/api/v1/breakages/cockpit?trend_window_days=14&bom_line_item_id=bom-1&page=1&page_size=20"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["filters"]["bom_line_item_id"] == "bom-1"
    assert body["metrics"]["by_mbom_id"]["mbom-1"] == 1
    assert body["metrics"]["by_routing_id"]["routing-1"] == 1
    assert body["metrics"]["top_mbom_ids"][0]["mbom_id"] == "mbom-1"
    assert body["metrics"]["top_routing_ids"][0]["routing_id"] == "routing-1"
    assert body["operator_id"] == 3
    service_cls.return_value.cockpit.assert_called_once_with(
        status=None,
        severity=None,
        product_item_id=None,
        bom_line_item_id="bom-1",
        batch_code=None,
        responsibility=None,
        trend_window_days=14,
        page=1,
        page_size=20,
    )


def test_breakage_cockpit_export_returns_download_response():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.export_cockpit.return_value = {
            "content": b"id,status\ninc-1,open\n",
            "media_type": "text/csv",
            "filename": "breakage-cockpit.csv",
        }
        resp = client.get(
            "/api/v1/breakages/cockpit/export?bom_line_item_id=bom-1&trend_window_days=14&export_format=csv&page=1&page_size=20"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="breakage-cockpit.csv"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.headers.get("x-operator-id") == "3"
    assert "inc-1" in resp.text
    service_cls.return_value.export_cockpit.assert_called_once_with(
        status=None,
        severity=None,
        product_item_id=None,
        bom_line_item_id="bom-1",
        batch_code=None,
        responsibility=None,
        trend_window_days=14,
        page=1,
        page_size=20,
        export_format="csv",
    )


def test_breakage_export_job_create_status_and_download_endpoints():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.enqueue_incidents_export_job.return_value = {
            "job_id": "job-exp-1",
            "status": "completed",
            "download_ready": True,
        }
        create_resp = client.post(
            "/api/v1/breakages/export/jobs",
            json={
                "bom_line_item_id": "bom-1",
                "page": 1,
                "page_size": 20,
                "export_format": "csv",
                "execute_immediately": True,
            },
        )
        service_cls.return_value.get_incidents_export_job.return_value = {
            "job_id": "job-exp-1",
            "status": "completed",
            "download_ready": True,
        }
        status_resp = client.get("/api/v1/breakages/export/jobs/job-exp-1")
        service_cls.return_value.download_incidents_export_job.return_value = {
            "content": b"id,status\ninc-1,open\n",
            "media_type": "text/csv",
            "filename": "breakage-incidents.csv",
        }
        download_resp = client.get("/api/v1/breakages/export/jobs/job-exp-1/download")

    assert create_resp.status_code == 200
    assert create_resp.json()["job_id"] == "job-exp-1"
    assert create_resp.json()["operator_id"] == 3
    assert status_resp.status_code == 200
    assert status_resp.json()["job_id"] == "job-exp-1"
    assert status_resp.json()["operator_id"] == 3
    assert download_resp.status_code == 200
    assert download_resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="breakage-incidents.csv"' in (
        download_resp.headers.get("content-disposition", "")
    )
    assert download_resp.headers.get("x-operator-id") == "3"
    assert "inc-1" in download_resp.text
    assert db.commit.called


def test_breakage_export_job_not_found_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.get_incidents_export_job.side_effect = ValueError(
            "Breakage incidents export job not found: job-missing"
        )
        resp = client.get("/api/v1/breakages/export/jobs/job-missing")

    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_export_job_not_found"
    assert detail.get("context", {}).get("job_id") == "job-missing"


def test_breakage_export_job_cleanup_endpoint_returns_payload():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.cleanup_expired_incidents_export_results.return_value = {
            "ttl_hours": 24,
            "limit": 200,
            "expired_jobs": 2,
            "job_ids": ["job-exp-1", "job-exp-2"],
        }
        resp = client.post(
            "/api/v1/breakages/export/jobs/cleanup",
            json={"ttl_hours": 24, "limit": 200},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ttl_hours"] == 24
    assert body["expired_jobs"] == 2
    assert body["operator_id"] == 3
    assert db.commit.called
    service_cls.return_value.cleanup_expired_incidents_export_results.assert_called_once_with(
        ttl_hours=24,
        limit=200,
        user_id=3,
    )


def test_breakage_export_job_cleanup_invalid_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.cleanup_expired_incidents_export_results.side_effect = (
            ValueError("ttl_hours must be between 1 and 720")
        )
        resp = client.post(
            "/api/v1/breakages/export/jobs/cleanup",
            json={"ttl_hours": 24, "limit": 200},
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_export_job_invalid"
    assert detail.get("context", {}).get("ttl_hours") == 24


def test_breakage_metrics_invalid_window_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.metrics.side_effect = ValueError(
            "trend_window_days must be one of: 7, 14, 30"
        )
        resp = client.get(
            "/api/v1/breakages/metrics?trend_window_days=10&bom_line_item_id=bom-x"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_metrics_invalid_request"
    assert detail.get("context", {}).get("trend_window_days") == 10
    assert detail.get("context", {}).get("bom_line_item_id") == "bom-x"


def test_breakage_metrics_returns_dimension_aggregates():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.metrics.return_value = {
            "total": 2,
            "repeated_failure_rate": 0.5,
            "repeated_event_count": 1,
            "by_status": {"open": 2},
            "by_severity": {"high": 2},
            "by_responsibility": {"supplier-a": 2},
            "by_product_item": {"p-1": 2},
            "by_batch_code": {"b-1": 2},
            "by_bom_line_item": {"bom-1": 2},
            "by_mbom_id": {"mbom-1": 2},
            "by_routing_id": {"routing-1": 2},
            "top_product_items": [{"product_item_id": "p-1", "count": 2}],
            "top_batch_codes": [{"batch_code": "b-1", "count": 2}],
            "top_bom_line_items": [{"bom_line_item_id": "bom-1", "count": 2}],
            "top_mbom_ids": [{"mbom_id": "mbom-1", "count": 2}],
            "top_routing_ids": [{"routing_id": "routing-1", "count": 2}],
            "hotspot_components": [{"bom_line_item_id": "bom-1", "count": 2}],
            "trend_window_days": 14,
            "trend": [{"date": "2026-03-01", "count": 2}],
            "filters": {"product_item_id": "p-1", "batch_code": "b-1"},
            "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 2},
            "incidents": [],
        }
        resp = client.get(
            "/api/v1/breakages/metrics?trend_window_days=14&bom_line_item_id=bom-1"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["by_product_item"]["p-1"] == 2
    assert body["by_batch_code"]["b-1"] == 2
    assert body["by_bom_line_item"]["bom-1"] == 2
    assert body["by_mbom_id"]["mbom-1"] == 2
    assert body["by_routing_id"]["routing-1"] == 2
    assert body["top_product_items"][0]["product_item_id"] == "p-1"
    assert body["top_batch_codes"][0]["batch_code"] == "b-1"
    assert body["top_bom_line_items"][0]["bom_line_item_id"] == "bom-1"
    assert body["top_mbom_ids"][0]["mbom_id"] == "mbom-1"
    assert body["top_routing_ids"][0]["routing_id"] == "routing-1"
    assert body["operator_id"] == 3
    service_cls.return_value.metrics.assert_called_once_with(
        status=None,
        severity=None,
        product_item_id=None,
        bom_line_item_id="bom-1",
        batch_code=None,
        responsibility=None,
        trend_window_days=14,
        page=1,
        page_size=20,
    )


def test_breakage_metrics_groups_returns_payload():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.metrics_groups.return_value = {
            "group_by": "product_item_id",
            "total_groups": 2,
            "groups": [
                {"group_by": "product_item_id", "group_value": "p-1", "count": 2},
                {"group_by": "product_item_id", "group_value": "p-2", "count": 1},
            ],
            "trend_window_days": 14,
            "filters": {},
            "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 2},
        }
        resp = client.get(
            "/api/v1/breakages/metrics/groups?group_by=product_item_id&trend_window_days=14&bom_line_item_id=bom-1"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["group_by"] == "product_item_id"
    assert body["total_groups"] == 2
    assert body["groups"][0]["group_value"] == "p-1"
    assert body["operator_id"] == 3
    service_cls.return_value.metrics_groups.assert_called_once_with(
        group_by="product_item_id",
        status=None,
        severity=None,
        product_item_id=None,
        bom_line_item_id="bom-1",
        batch_code=None,
        responsibility=None,
        trend_window_days=14,
        page=1,
        page_size=20,
    )


def test_breakage_metrics_groups_supports_bom_line_item_dimension():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.metrics_groups.return_value = {
            "group_by": "bom_line_item_id",
            "total_groups": 1,
            "groups": [
                {"group_by": "bom_line_item_id", "group_value": "bom-1", "count": 2},
            ],
            "trend_window_days": 14,
            "filters": {},
            "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
        }
        resp = client.get(
            "/api/v1/breakages/metrics/groups?group_by=bom_line_item_id&trend_window_days=14"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["group_by"] == "bom_line_item_id"
    assert body["groups"][0]["group_value"] == "bom-1"
    assert body["operator_id"] == 3


def test_breakage_metrics_groups_supports_mbom_and_routing_dimensions():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.metrics_groups.side_effect = [
            {
                "group_by": "bom_id",
                "total_groups": 1,
                "groups": [{"group_by": "bom_id", "group_value": "bom-root-1", "count": 2}],
                "trend_window_days": 14,
                "filters": {},
                "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
            },
            {
                "group_by": "mbom_id",
                "total_groups": 1,
                "groups": [{"group_by": "mbom_id", "group_value": "mbom-1", "count": 2}],
                "trend_window_days": 14,
                "filters": {},
                "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
            },
            {
                "group_by": "routing_id",
                "total_groups": 1,
                "groups": [
                    {"group_by": "routing_id", "group_value": "routing-1", "count": 2}
                ],
                "trend_window_days": 14,
                "filters": {},
                "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
            },
        ]
        bom_resp = client.get(
            "/api/v1/breakages/metrics/groups?group_by=bom_id&trend_window_days=14"
        )
        mbom_resp = client.get(
            "/api/v1/breakages/metrics/groups?group_by=mbom_id&trend_window_days=14"
        )
        routing_resp = client.get(
            "/api/v1/breakages/metrics/groups?group_by=routing_id&trend_window_days=14"
        )

    assert bom_resp.status_code == 200
    assert bom_resp.json()["group_by"] == "bom_id"
    assert bom_resp.json()["groups"][0]["group_value"] == "bom-root-1"
    assert mbom_resp.status_code == 200
    assert mbom_resp.json()["group_by"] == "mbom_id"
    assert mbom_resp.json()["groups"][0]["group_value"] == "mbom-1"
    assert routing_resp.status_code == 200
    assert routing_resp.json()["group_by"] == "routing_id"
    assert routing_resp.json()["groups"][0]["group_value"] == "routing-1"
    assert service_cls.return_value.metrics_groups.call_count == 3
    assert (
        service_cls.return_value.metrics_groups.call_args_list[0].kwargs["group_by"]
        == "bom_id"
    )
    assert (
        service_cls.return_value.metrics_groups.call_args_list[1].kwargs["group_by"]
        == "mbom_id"
    )
    assert (
        service_cls.return_value.metrics_groups.call_args_list[2].kwargs["group_by"]
        == "routing_id"
    )


def test_breakage_metrics_groups_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.metrics_groups.side_effect = ValueError(
            "group_by must be one of: batch_code, bom_id, bom_line_item_id, mbom_id, product_item_id, responsibility, routing_id"
        )
        resp = client.get(
            "/api/v1/breakages/metrics/groups?group_by=oops&trend_window_days=14"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_metrics_invalid_request"
    assert "bom_id" in (detail.get("message") or "")
    assert "mbom_id" in (detail.get("message") or "")
    assert "routing_id" in (detail.get("message") or "")
    assert detail.get("context", {}).get("group_by") == "oops"


def test_breakage_metrics_groups_export_returns_download_response():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.export_metrics_groups.return_value = {
            "content": b"group_by,group_value,count\nproduct_item_id,p-1,2\n",
            "media_type": "text/csv",
            "filename": "breakage-metrics-groups.csv",
        }
        resp = client.get(
            "/api/v1/breakages/metrics/groups/export?group_by=product_item_id&trend_window_days=14&bom_line_item_id=bom-1&export_format=csv"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="breakage-metrics-groups.csv"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.headers.get("x-operator-id") == "3"
    assert "group_by,group_value,count" in resp.text
    service_cls.return_value.export_metrics_groups.assert_called_once_with(
        group_by="product_item_id",
        status=None,
        severity=None,
        product_item_id=None,
        bom_line_item_id="bom-1",
        batch_code=None,
        responsibility=None,
        trend_window_days=14,
        page=1,
        page_size=20,
        export_format="csv",
        report_lang=None,
        report_type=None,
        locale_profile_id=None,
    )


def test_breakage_metrics_groups_export_passes_locale_query_contract():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.export_metrics_groups.return_value = {
            "content": b'{"group_by": "product_item_id", "locale": {"id": "rp-breakage-groups-zh"}}',
            "media_type": "application/json",
            "filename": "breakage-metrics-groups.json",
        }
        resp = client.get(
            "/api/v1/breakages/metrics/groups/export"
            "?group_by=product_item_id"
            "&trend_window_days=14"
            "&export_format=json"
            "&report_lang=zh_CN"
            "&report_type=breakage_metrics_groups"
            "&locale_profile_id=rp-breakage-groups-zh"
        )

    assert resp.status_code == 200
    kwargs = service_cls.return_value.export_metrics_groups.call_args.kwargs
    assert kwargs["report_lang"] == "zh_CN"
    assert kwargs["report_type"] == "breakage_metrics_groups"
    assert kwargs["locale_profile_id"] == "rp-breakage-groups-zh"
    assert resp.json()["locale"]["id"] == "rp-breakage-groups-zh"


def test_breakage_metrics_groups_export_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.export_metrics_groups.side_effect = ValueError(
            "export_format must be json, csv or md"
        )
        resp = client.get(
            "/api/v1/breakages/metrics/groups/export?group_by=product_item_id&trend_window_days=14&export_format=xlsx"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_metrics_invalid_request"
    assert detail.get("context", {}).get("group_by") == "product_item_id"
    assert detail.get("context", {}).get("export_format") == "xlsx"


def test_breakage_list_supports_bom_line_filter():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.list_incidents.return_value = [
            SimpleNamespace(
                id="inc-1",
                incident_code="BRK-000101",
                description="bearing crack",
                severity="high",
                status="open",
                product_item_id="p-1",
                bom_id="bom-root-1",
                bom_line_item_id="bom-1",
                production_order_id=None,
                version_id=None,
                routing_id="routing-1",
                mbom_id="mbom-1",
                batch_code="batch-1",
                customer_name=None,
                responsibility="supplier-a",
                created_at=None,
                updated_at=None,
            )
        ]
        service_cls.return_value.build_latest_helpdesk_summary_map.return_value = {
            "inc-1": {
                "job_id": "job-hd-1",
                "sync_status": "completed",
                "provider": "jira",
                "external_ticket_id": "HD-101",
                "provider_ticket_status": "assigned",
                "provider_assignee": "owner-a",
            }
        }
        resp = client.get("/api/v1/breakages?bom_line_item_id=bom-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["incidents"][0]["incident_code"] == "BRK-000101"
    assert body["incidents"][0]["bom_id"] == "bom-root-1"
    assert body["incidents"][0]["mbom_id"] == "mbom-1"
    assert body["incidents"][0]["routing_id"] == "routing-1"
    assert body["incidents"][0]["bom_line_item_id"] == "bom-1"
    assert body["incidents"][0]["helpdesk_ticket_summary"]["external_ticket_id"] == "HD-101"
    assert body["incidents"][0]["helpdesk_ticket_summary"]["provider_assignee"] == "owner-a"
    assert body["operator_id"] == 3
    service_cls.return_value.list_incidents.assert_called_once_with(
        status=None,
        severity=None,
        product_item_id=None,
        bom_line_item_id="bom-1",
        batch_code=None,
        responsibility=None,
    )
    service_cls.return_value.build_latest_helpdesk_summary_map.assert_called_once_with(
        ["inc-1"]
    )


def test_breakage_create_supports_identity_and_dimension_fields():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.create_incident.return_value = SimpleNamespace(
            id="inc-1",
            incident_code="BRK-000001",
            description="bearing crack",
            severity="high",
            status="open",
            product_item_id="p-1",
            bom_id="bom-root-1",
            bom_line_item_id="bom-1",
            routing_id="routing-1",
            production_order_id="routing-1",
            mbom_id="mbom-1",
            version_id="mbom-1",
            created_at=None,
        )
        resp = client.post(
            "/api/v1/breakages",
            json={
                "description": "bearing crack",
                "severity": "high",
                "product_item_id": "p-1",
                "bom_id": "bom-root-1",
                "bom_line_item_id": "bom-1",
                "routing_id": "routing-1",
                "mbom_id": "mbom-1",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_code"] == "BRK-000001"
    assert body["bom_id"] == "bom-root-1"
    assert body["mbom_id"] == "mbom-1"
    assert body["routing_id"] == "routing-1"
    assert body["production_order_id"] == "routing-1"
    assert body["version_id"] == "mbom-1"
    assert db.commit.called
    service_cls.return_value.create_incident.assert_called_once_with(
        description="bearing crack",
        severity="high",
        status="open",
        product_item_id="p-1",
        bom_id="bom-root-1",
        bom_line_item_id="bom-1",
        routing_id="routing-1",
        mbom_id="mbom-1",
        production_order_id=None,
        version_id=None,
        batch_code=None,
        customer_name=None,
        responsibility=None,
        created_by_id=3,
    )


def test_breakage_export_returns_download_response():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.export_incidents.return_value = {
            "content": b"id,description,bom_line_item_id_filter\ninc-1,bearing crack,bom-1\n",
            "media_type": "text/csv",
            "filename": "breakage-incidents.csv",
        }
        resp = client.get(
            "/api/v1/breakages/export?bom_line_item_id=bom-1&page=1&page_size=20&export_format=csv"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="breakage-incidents.csv"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.headers.get("x-operator-id") == "3"
    assert "bom_line_item_id_filter" in resp.text
    service_cls.return_value.export_incidents.assert_called_once_with(
        status=None,
        severity=None,
        product_item_id=None,
        bom_line_item_id="bom-1",
        batch_code=None,
        responsibility=None,
        page=1,
        page_size=20,
        export_format="csv",
        report_lang=None,
        report_type=None,
        locale_profile_id=None,
    )


def test_breakage_export_passes_locale_query_contract():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.export_incidents.return_value = {
            "content": b'{"total": 1, "locale": {"id": "rp-breakage-incidents-zh"}}',
            "media_type": "application/json",
            "filename": "breakage-incidents.json",
        }
        resp = client.get(
            "/api/v1/breakages/export"
            "?bom_line_item_id=bom-1"
            "&export_format=json"
            "&report_lang=zh_CN"
            "&report_type=breakage_incidents"
            "&locale_profile_id=rp-breakage-incidents-zh"
        )

    assert resp.status_code == 200
    kwargs = service_cls.return_value.export_incidents.call_args.kwargs
    assert kwargs["report_lang"] == "zh_CN"
    assert kwargs["report_type"] == "breakage_incidents"
    assert kwargs["locale_profile_id"] == "rp-breakage-incidents-zh"
    assert resp.json()["locale"]["id"] == "rp-breakage-incidents-zh"


def test_breakage_export_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.export_incidents.side_effect = ValueError(
            "export_format must be json, csv or md"
        )
        resp = client.get(
            "/api/v1/breakages/export?bom_line_item_id=bom-1&export_format=xlsx"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_invalid_request"
    assert detail.get("context", {}).get("bom_line_item_id") == "bom-1"
    assert detail.get("context", {}).get("export_format") == "xlsx"


def test_breakage_metrics_export_returns_download_response():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.export_metrics.return_value = {
            "content": b"date,count,total\n2026-02-28,1,1\n",
            "media_type": "text/csv",
            "filename": "breakage-metrics.csv",
        }
        resp = client.get(
            "/api/v1/breakages/metrics/export?trend_window_days=14&bom_line_item_id=bom-1&export_format=csv"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="breakage-metrics.csv"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.headers.get("x-operator-id") == "3"
    assert "date,count,total" in resp.text
    service_cls.return_value.export_metrics.assert_called_once_with(
        status=None,
        severity=None,
        product_item_id=None,
        bom_line_item_id="bom-1",
        batch_code=None,
        responsibility=None,
        trend_window_days=14,
        page=1,
        page_size=20,
        export_format="csv",
        report_lang=None,
        report_type=None,
        locale_profile_id=None,
    )


def test_breakage_metrics_export_passes_locale_query_contract():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.export_metrics.return_value = {
            "content": b'{"total": 1, "locale": {"id": "rp-breakage-zh"}}',
            "media_type": "application/json",
            "filename": "breakage-metrics.json",
        }
        resp = client.get(
            "/api/v1/breakages/metrics/export"
            "?trend_window_days=14"
            "&export_format=json"
            "&report_lang=zh_CN"
            "&report_type=breakage_metrics"
            "&locale_profile_id=rp-breakage-zh"
        )

    assert resp.status_code == 200
    kwargs = service_cls.return_value.export_metrics.call_args.kwargs
    assert kwargs["report_lang"] == "zh_CN"
    assert kwargs["report_type"] == "breakage_metrics"
    assert kwargs["locale_profile_id"] == "rp-breakage-zh"
    assert resp.json()["locale"]["id"] == "rp-breakage-zh"


def test_breakage_metrics_export_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.export_metrics.side_effect = ValueError(
            "export_format must be json, csv or md"
        )
        resp = client.get(
            "/api/v1/breakages/metrics/export?trend_window_days=14&export_format=xlsx"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_metrics_invalid_request"
    assert detail.get("context", {}).get("export_format") == "xlsx"


def test_doc_sync_create_job_maps_missing_site_to_404():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_doc_sync_router.DocumentMultiSiteService"
    ) as service_cls:
        service_cls.return_value.enqueue_sync.side_effect = ValueError(
            "Remote site not found: s-404"
        )
        resp = client.post(
            "/api/v1/doc-sync/jobs",
            json={
                "site_id": "s-404",
                "direction": "push",
                "document_ids": ["d-1"],
            },
        )

    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "remote_site_not_found"
    assert detail.get("context", {}).get("site_id") == "s-404"


def test_doc_sync_list_jobs_invalid_datetime_maps_contract_error():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)
    resp = client.get(
        "/api/v1/doc-sync/jobs?created_from=not-a-datetime"
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "invalid_datetime"
    assert detail.get("context", {}).get("field") == "created_from"


def test_doc_sync_list_jobs_includes_reliability_view_fields():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_doc_sync_router.DocumentMultiSiteService"
    ) as service_cls:
        service_cls.return_value.list_sync_jobs.return_value = [SimpleNamespace(id="job-1")]
        service_cls.return_value.build_sync_job_view.return_value = {
            "id": "job-1",
            "task_type": "document_sync_push",
            "status": "failed",
            "attempt_count": 3,
            "max_attempts": 3,
            "retry_budget": {"attempt_count": 3, "max_attempts": 3, "remaining_attempts": 0},
            "is_dead_letter": True,
            "sync_trace": {"trace_id": "t-1", "origin_site": "A", "payload_hash": "h-1"},
        }
        resp = client.get("/api/v1/doc-sync/jobs?status=failed")

    assert resp.status_code == 200
    rows = resp.json().get("jobs") or []
    assert len(rows) == 1
    assert rows[0]["is_dead_letter"] is True
    assert rows[0]["retry_budget"]["remaining_attempts"] == 0
    assert rows[0]["sync_trace"]["trace_id"] == "t-1"


def test_doc_sync_summary_returns_operator_id_and_payload():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_doc_sync_router.DocumentMultiSiteService"
    ) as service_cls:
        service_cls.return_value.sync_summary.return_value = {
            "window_days": 7,
            "since": "2026-03-01T00:00:00",
            "site_filter": None,
            "total_jobs": 3,
            "total_sites": 2,
            "overall_by_status": {"completed": 1, "failed": 1, "processing": 1},
            "overall_dead_letter_total": 1,
            "sites": [
                {
                    "site_id": "site-1",
                    "total": 2,
                    "by_status": {"completed": 1, "failed": 1},
                    "dead_letter_total": 1,
                    "directions": {"push": 1, "pull": 1},
                    "success_rate": 0.5,
                    "failure_rate": 0.5,
                    "last_job_at": "2026-03-05T12:00:00",
                }
            ],
        }
        resp = client.get("/api/v1/doc-sync/summary?window_days=7")

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_id"] == 5
    assert body["total_jobs"] == 3
    assert body["overall_dead_letter_total"] == 1
    assert body["sites"][0]["site_id"] == "site-1"
    service_cls.return_value.sync_summary.assert_called_once_with(
        site_id=None,
        window_days=7,
    )


def test_doc_sync_dead_letter_list_returns_operator_id_and_jobs():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_doc_sync_router.DocumentMultiSiteService"
    ) as service_cls:
        service_cls.return_value.list_dead_letter_sync_jobs.return_value = [
            SimpleNamespace(id="job-dead-1")
        ]
        service_cls.return_value.build_sync_job_view.return_value = {
            "id": "job-dead-1",
            "status": "failed",
            "is_dead_letter": True,
        }
        resp = client.get("/api/v1/doc-sync/jobs/dead-letter?window_days=7&limit=10")

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_id"] == 5
    assert body["total"] == 1
    assert body["jobs"][0]["id"] == "job-dead-1"
    service_cls.return_value.list_dead_letter_sync_jobs.assert_called_once_with(
        site_id=None,
        window_days=7,
        limit=10,
    )


def test_doc_sync_dead_letter_invalid_maps_contract_error():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_doc_sync_router.DocumentMultiSiteService"
    ) as service_cls:
        service_cls.return_value.list_dead_letter_sync_jobs.side_effect = ValueError(
            "window_days must be between 1 and 90"
        )
        resp = client.get("/api/v1/doc-sync/jobs/dead-letter?window_days=0")

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "doc_sync_dead_letter_invalid"
    assert detail.get("context", {}).get("window_days") == 0


def test_doc_sync_replay_batch_returns_operator_id_and_result():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_doc_sync_router.DocumentMultiSiteService"
    ) as service_cls:
        service_cls.return_value.replay_sync_jobs_batch.return_value = {
            "source": "dead_letter",
            "requested": 2,
            "replayed": 2,
            "failed": 0,
            "failures": [],
            "replayed_jobs": [
                {"source_job_id": "j1", "replayed_job_id": "r1"},
                {"source_job_id": "j2", "replayed_job_id": "r2"},
            ],
        }
        resp = client.post(
            "/api/v1/doc-sync/jobs/replay-batch",
            json={
                "site_id": "site-1",
                "only_dead_letter": True,
                "window_days": 7,
                "limit": 20,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_id"] == 5
    assert body["replayed"] == 2
    service_cls.return_value.replay_sync_jobs_batch.assert_called_once_with(
        job_ids=None,
        site_id="site-1",
        only_dead_letter=True,
        window_days=7,
        limit=20,
        user_id=5,
    )


def test_doc_sync_replay_batch_invalid_maps_contract_error():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_doc_sync_router.DocumentMultiSiteService"
    ) as service_cls:
        service_cls.return_value.replay_sync_jobs_batch.side_effect = ValueError(
            "limit must be between 1 and 500"
        )
        resp = client.post(
            "/api/v1/doc-sync/jobs/replay-batch",
            json={"site_id": "site-1", "limit": 0},
        )

    assert resp.status_code == 422


def test_doc_sync_summary_export_streams_payload():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_doc_sync_router.DocumentMultiSiteService"
    ) as service_cls:
        service_cls.return_value.export_sync_summary.return_value = {
            "content": b"scope,site_id,total\noverall,*,2\n",
            "media_type": "text/csv",
            "filename": "doc-sync-summary.csv",
        }
        resp = client.get("/api/v1/doc-sync/summary/export?window_days=7&export_format=csv")

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="doc-sync-summary.csv"' in (resp.headers.get("content-disposition", ""))
    assert resp.headers.get("x-operator-id") == "5"
    assert "scope,site_id,total" in resp.text
    service_cls.return_value.export_sync_summary.assert_called_once_with(
        site_id=None,
        window_days=7,
        export_format="csv",
    )


def test_doc_sync_summary_export_invalid_maps_contract_error():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_doc_sync_router.DocumentMultiSiteService"
    ) as service_cls:
        service_cls.return_value.export_sync_summary.side_effect = ValueError(
            "export_format must be json, csv or md"
        )
        resp = client.get("/api/v1/doc-sync/summary/export?export_format=xlsx")

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "doc_sync_summary_export_invalid"
    assert detail.get("context", {}).get("export_format") == "xlsx"


def test_doc_sync_summary_invalid_maps_contract_error():
    user = SimpleNamespace(id=5, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_doc_sync_router.DocumentMultiSiteService"
    ) as service_cls:
        service_cls.return_value.sync_summary.side_effect = ValueError(
            "window_days must be between 1 and 90"
        )
        resp = client.get("/api/v1/doc-sync/summary?window_days=0&site_id=site-x")

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "doc_sync_summary_invalid"
    assert detail.get("context", {}).get("window_days") == 0
    assert detail.get("context", {}).get("site_id") == "site-x"


def test_eco_activity_create_invalid_maps_contract_error():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.create_activity.side_effect = ValueError("eco not found")
        resp = client.post(
            "/api/v1/eco-activities",
            json={
                "eco_id": "eco-404",
                "name": "A1",
                "depends_on_activity_ids": [],
                "is_blocking": True,
            },
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "eco_activity_invalid_request"
    assert detail.get("context", {}).get("eco_id") == "eco-404"


def test_eco_activity_transition_blocked_maps_contract_error():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.transition_activity.side_effect = ValueError(
            "Blocking dependencies not satisfied"
        )
        resp = client.post(
            "/api/v1/eco-activities/activity/act-1/transition",
            json={"to_status": "done"},
        )

    assert resp.status_code == 409
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "eco_activity_blocked"
    assert detail.get("context", {}).get("activity_id") == "act-1"


def test_eco_activity_transition_check_returns_decision_with_operator_id():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.evaluate_transition.return_value = {
            "activity_id": "act-1",
            "eco_id": "eco-1",
            "name": "A1",
            "from_status": "pending",
            "to_status": "completed",
            "requested_status": "done",
            "allowed_targets": ["active", "completed", "pending"],
            "can_transition": True,
            "reason_code": "ok",
            "blockers": [],
        }
        resp = client.get(
            "/api/v1/eco-activities/activity/act-1/transition-check?to_status=done"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["activity_id"] == "act-1"
    assert body["to_status"] == "completed"
    assert body["can_transition"] is True
    assert body["operator_id"] == 6
    service_cls.return_value.evaluate_transition.assert_called_once_with(
        activity_id="act-1",
        to_status="done",
    )


def test_eco_activity_transition_check_invalid_maps_contract_error():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.evaluate_transition.side_effect = ValueError(
            "to_status must be one of: active, canceled, cancel, completed"
        )
        resp = client.get(
            "/api/v1/eco-activities/activity/act-1/transition-check?to_status=bad"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "eco_activity_transition_invalid"
    assert detail.get("context", {}).get("activity_id") == "act-1"
    assert detail.get("context", {}).get("to_status") == "bad"


def test_eco_activity_bulk_transition_check_returns_decisions_with_operator_id():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.evaluate_transitions_bulk.return_value = {
            "eco_id": "eco-1",
            "to_status": "active",
            "requested_status": "in_progress",
            "include_terminal": False,
            "include_non_blocking": True,
            "selected_total": 2,
            "total": 2,
            "ready_total": 1,
            "blocked_total": 1,
            "invalid_total": 0,
            "noop_total": 0,
            "missing_total": 0,
            "excluded_total": 0,
            "missing_activity_ids": [],
            "excluded_activity_ids": [],
            "truncated": False,
            "decisions": [
                {"activity_id": "a-1", "can_transition": True},
                {"activity_id": "a-2", "can_transition": False},
            ],
        }
        resp = client.post(
            "/api/v1/eco-activities/eco-1/transition-check/bulk",
            json={
                "to_status": "in_progress",
                "activity_ids": ["a-1", "a-2"],
                "include_terminal": False,
                "include_non_blocking": True,
                "limit": 50,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["eco_id"] == "eco-1"
    assert body["to_status"] == "active"
    assert body["ready_total"] == 1
    assert body["blocked_total"] == 1
    assert body["operator_id"] == 6
    service_cls.return_value.evaluate_transitions_bulk.assert_called_once_with(
        "eco-1",
        to_status="in_progress",
        activity_ids=["a-1", "a-2"],
        include_terminal=False,
        include_non_blocking=True,
        limit=50,
    )


def test_eco_activity_bulk_transition_check_invalid_maps_contract_error():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.evaluate_transitions_bulk.side_effect = ValueError(
            "to_status must be one of: active, canceled, cancel, completed"
        )
        resp = client.post(
            "/api/v1/eco-activities/eco-1/transition-check/bulk",
            json={"to_status": "bad", "limit": 200},
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "eco_activity_transition_invalid"
    assert detail.get("context", {}).get("eco_id") == "eco-1"
    assert detail.get("context", {}).get("to_status") == "bad"


def test_eco_activity_bulk_transition_endpoint_returns_decisions_with_operator_id():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.transition_activities_bulk.return_value = {
            "eco_id": "eco-1",
            "to_status": "completed",
            "requested_status": "done",
            "selected_total": 2,
            "total": 2,
            "executed_total": 2,
            "noop_total": 0,
            "blocked_total": 0,
            "invalid_total": 0,
            "missing_total": 0,
            "excluded_total": 0,
            "missing_activity_ids": [],
            "excluded_activity_ids": [],
            "decisions": [
                {"activity_id": "a-1", "action": "executed"},
                {"activity_id": "a-2", "action": "executed"},
            ],
        }
        resp = client.post(
            "/api/v1/eco-activities/eco-1/transition/bulk",
            json={
                "to_status": "done",
                "activity_ids": ["a-1", "a-2"],
                "include_terminal": False,
                "include_non_blocking": True,
                "limit": 50,
                "reason": "bulk-router-test",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["eco_id"] == "eco-1"
    assert body["to_status"] == "completed"
    assert body["executed_total"] == 2
    assert body["operator_id"] == 6
    assert db.commit.called
    service_cls.return_value.transition_activities_bulk.assert_called_once_with(
        "eco-1",
        to_status="done",
        activity_ids=["a-1", "a-2"],
        include_terminal=False,
        include_non_blocking=True,
        limit=50,
        user_id=6,
        reason="bulk-router-test",
    )


def test_eco_activity_bulk_transition_invalid_maps_contract_error():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.transition_activities_bulk.side_effect = ValueError(
            "bulk execution truncated by limit; increase limit and retry"
        )
        resp = client.post(
            "/api/v1/eco-activities/eco-1/transition/bulk",
            json={"to_status": "done", "limit": 1},
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "eco_activity_transition_invalid"
    assert detail.get("context", {}).get("eco_id") == "eco-1"
    assert detail.get("context", {}).get("to_status") == "done"
    assert detail.get("context", {}).get("limit") == 1


def test_eco_activity_sla_returns_overview_with_operator_id():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.activity_sla.return_value = {
            "eco_id": "eco-1",
            "evaluated_at": "2026-03-05T12:00:00",
            "due_soon_hours": 48,
            "total": 1,
            "overdue_total": 0,
            "due_soon_total": 1,
            "on_track_total": 0,
            "no_due_date_total": 0,
            "closed_total": 0,
            "status_counts": {"pending": 1},
            "truncated": False,
            "activities": [{"id": "a-1", "name": "review", "classification": "due_soon"}],
        }
        resp = client.get(
            "/api/v1/eco-activities/eco-1/sla"
            "?due_soon_hours=48&include_closed=true&assignee_id=12&limit=30"
            "&evaluated_at=2026-03-05T12:00:00Z"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_id"] == 6
    assert body["due_soon_hours"] == 48
    assert body["activities"][0]["classification"] == "due_soon"
    call = service_cls.return_value.activity_sla.call_args
    assert call.args[0] == "eco-1"
    assert call.kwargs["due_soon_hours"] == 48
    assert call.kwargs["include_closed"] is True
    assert call.kwargs["assignee_id"] == 12
    assert call.kwargs["limit"] == 30
    assert call.kwargs["now"] is not None


def test_eco_activity_sla_invalid_maps_contract_error():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.activity_sla.side_effect = ValueError(
            "due_soon_hours must be between 1 and 720"
        )
        resp = client.get("/api/v1/eco-activities/eco-1/sla?due_soon_hours=0")

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "eco_activity_sla_invalid"
    assert detail.get("context", {}).get("eco_id") == "eco-1"
    assert detail.get("context", {}).get("due_soon_hours") == 0


def test_eco_activity_sla_alerts_returns_overview_with_operator_id():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.activity_sla_alerts.return_value = {
            "eco_id": "eco-1",
            "status": "warning",
            "metrics": {"open_total": 3, "overdue_total": 1, "due_soon_total": 2},
            "alerts": [
                {
                    "code": "eco_activity_sla_due_soon_count_high",
                    "level": "warn",
                    "current": 2,
                    "threshold": 1,
                }
            ],
        }
        resp = client.get(
            "/api/v1/eco-activities/eco-1/sla/alerts"
            "?due_soon_hours=48&overdue_rate_warn=0.1&due_soon_count_warn=1&blocking_overdue_warn=0"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["operator_id"] == 6
    assert body["status"] == "warning"
    assert body["alerts"][0]["code"] == "eco_activity_sla_due_soon_count_high"
    service_cls.return_value.activity_sla_alerts.assert_called_once_with(
        "eco-1",
        now=None,
        due_soon_hours=48,
        include_closed=False,
        assignee_id=None,
        limit=100,
        overdue_rate_warn=0.1,
        due_soon_count_warn=1,
        blocking_overdue_warn=0,
    )


def test_eco_activity_sla_alerts_invalid_maps_contract_error():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.activity_sla_alerts.side_effect = ValueError(
            "overdue_rate_warn must be between 0 and 1"
        )
        resp = client.get("/api/v1/eco-activities/eco-1/sla/alerts?overdue_rate_warn=1.2")

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "eco_activity_sla_alerts_invalid"
    assert detail.get("context", {}).get("overdue_rate_warn") == 1.2


def test_eco_activity_sla_alerts_export_streams_payload():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.export_activity_sla_alerts.return_value = {
            "content": b"eco_id,status,alert_code\neco-1,warning,eco_activity_sla_overdue_rate_high\n",
            "media_type": "text/csv",
            "filename": "eco-activity-sla-alerts.csv",
        }
        resp = client.get(
            "/api/v1/eco-activities/eco-1/sla/alerts/export?export_format=csv"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="eco-activity-sla-alerts.csv"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.headers.get("x-operator-id") == "6"
    assert "alert_code" in resp.text


def test_eco_activity_sla_alerts_export_invalid_maps_contract_error():
    user = SimpleNamespace(id=6, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_eco_activities_router.ECOActivityValidationService"
    ) as service_cls:
        service_cls.return_value.export_activity_sla_alerts.side_effect = ValueError(
            "export_format must be json, csv or md"
        )
        resp = client.get(
            "/api/v1/eco-activities/eco-1/sla/alerts/export?export_format=xlsx"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "eco_activity_sla_alerts_export_invalid"
    assert detail.get("context", {}).get("export_format") == "xlsx"


def test_workflow_rule_invalid_payload_returns_contract_error():
    user = SimpleNamespace(id=9, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_workflow_actions_router.WorkflowCustomActionService"
    ) as service_cls:
        service_cls.return_value.create_rule.side_effect = ValueError(
            "max_retries must be between 1 and 5 for retry strategy"
        )
        resp = client.post(
            "/api/v1/workflow-actions/rules",
            json={
                "name": "r1",
                "target_object": "ECO",
                "trigger_phase": "before",
                "action_type": "emit_event",
                "fail_strategy": "retry",
                "action_params": {"max_retries": 9},
            },
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "invalid_workflow_rule"
    assert detail.get("context", {}).get("name") == "r1"


def test_workflow_rule_upsert_accepts_match_predicates():
    user = SimpleNamespace(id=9, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_workflow_actions_router.WorkflowCustomActionService"
    ) as service_cls:
        service_cls.return_value.create_rule.return_value = SimpleNamespace(
            id="rule-1",
            name="r1",
            target_object="ECO",
            workflow_map_id="wf-map-1",
            from_state="draft",
            to_state="progress",
            trigger_phase="before",
            action_type="emit_event",
            action_params={
                "priority": 25,
                "timeout_s": 5.0,
                "max_retries": 0,
                "match_predicates": {
                    "stage_id": "stage-2",
                    "eco_priority": "high",
                    "actor_roles": ["qa"],
                    "product_id": "item-1",
                    "eco_type": "bom",
                },
            },
            fail_strategy="warn",
            is_enabled=True,
        )
        resp = client.post(
            "/api/v1/workflow-actions/rules",
            json={
                "name": "r1",
                "target_object": "ECO",
                "workflow_map_id": "wf-map-1",
                "from_state": "draft",
                "to_state": "progress",
                "trigger_phase": "before",
                "action_type": "emit_event",
                "action_params": {"event": "eco.transition"},
                "match_predicates": {
                    "stage_id": "stage-2",
                    "eco_priority": "high",
                    "actor_roles": ["qa"],
                    "product_id": "item-1",
                    "eco_type": "bom",
                },
                "fail_strategy": "warn",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_map_id"] == "wf-map-1"
    assert body["match_predicates"] == {
        "stage_id": "stage-2",
        "eco_priority": "high",
        "actor_roles": ["qa"],
        "product_id": "item-1",
        "eco_type": "bom",
    }
    assert db.commit.called
    service_cls.return_value.create_rule.assert_called_once_with(
        name="r1",
        target_object="ECO",
        workflow_map_id="wf-map-1",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        action_type="emit_event",
        action_params={"event": "eco.transition"},
        match_predicates={
            "stage_id": "stage-2",
            "eco_priority": "high",
            "actor_roles": ["qa"],
            "product_id": "item-1",
            "eco_type": "bom",
        },
        fail_strategy="warn",
        is_enabled=True,
    )


def test_workflow_execute_injects_actor_roles_into_context():
    user = SimpleNamespace(id=9, roles=["planner", "qa"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_workflow_actions_router.WorkflowCustomActionService"
    ) as service_cls:
        service_cls.return_value.evaluate_transition.return_value = []
        resp = client.post(
            "/api/v1/workflow-actions/execute",
            json={
                "object_id": "eco-1",
                "target_object": "ECO",
                "from_state": "draft",
                "to_state": "progress",
                "trigger_phase": "before",
                "context": {"source": "manual"},
            },
        )

    assert resp.status_code == 200
    assert db.commit.called
    service_cls.return_value.evaluate_transition.assert_called_once_with(
        object_id="eco-1",
        target_object="ECO",
        from_state="draft",
        to_state="progress",
        trigger_phase="before",
        context={"source": "manual", "actor_roles": ["planner", "qa"]},
    )


def test_workflow_execute_failure_returns_contract_error():
    user = SimpleNamespace(id=9, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_workflow_actions_router.WorkflowCustomActionService"
    ) as service_cls:
        service_cls.return_value.evaluate_transition.side_effect = ValueError(
            "[BLOCK] workflow custom action failed"
        )
        resp = client.post(
            "/api/v1/workflow-actions/execute",
            json={
                "object_id": "eco-1",
                "target_object": "ECO",
                "from_state": "draft",
                "to_state": "progress",
                "trigger_phase": "before",
            },
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "workflow_action_execution_failed"
    assert detail.get("context", {}).get("object_id") == "eco-1"


def test_3d_overlay_component_permission_denied_maps_403():
    user = SimpleNamespace(id=8, roles=["viewer"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_cad_3d_router.ThreeDOverlayService"
    ) as service_cls:
        service_cls.return_value.resolve_component.side_effect = PermissionError(
            "Overlay is not visible for current roles"
        )
        resp = client.get(
            "/api/v1/cad-3d/overlays/doc-1/components/C-001"
        )

    assert resp.status_code == 403
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "overlay_access_denied"
    assert detail.get("context", {}).get("document_item_id") == "doc-1"
    assert detail.get("context", {}).get("component_ref") == "C-001"


def test_3d_overlay_get_not_found_maps_contract_error():
    user = SimpleNamespace(id=8, roles=["viewer"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_cad_3d_router.ThreeDOverlayService"
    ) as service_cls:
        service_cls.return_value.get_overlay.return_value = None
        resp = client.get("/api/v1/cad-3d/overlays/doc-404")

    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "overlay_not_found"
    assert detail.get("context", {}).get("document_item_id") == "doc-404"


def test_consumption_template_create_version_success():
    user = SimpleNamespace(id=12, roles=["planner"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_consumption_router.ConsumptionPlanService"
    ) as service_cls:
        service_cls.return_value.create_template_version.return_value = SimpleNamespace(
            id="plan-v1",
            name="template-v1",
            state="active",
            planned_quantity=10.0,
            uom="EA",
            period_unit="week",
            item_id="item-1",
            properties={
                "template": {
                    "key": "tpl-1",
                    "version": "v1",
                    "is_template_version": True,
                    "is_active": True,
                }
            },
        )
        resp = client.post(
            "/api/v1/consumption/templates/tpl-1/versions",
            json={
                "name": "template-v1",
                "planned_quantity": 10.0,
                "version_label": "v1",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "plan-v1"
    assert body["template"]["key"] == "tpl-1"
    assert body["template"]["is_active"] is True
    assert db.commit.called


def test_consumption_template_create_version_invalid_maps_contract_error():
    user = SimpleNamespace(id=12, roles=["planner"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_consumption_router.ConsumptionPlanService"
    ) as service_cls:
        service_cls.return_value.create_template_version.side_effect = ValueError(
            "template_key must not be empty"
        )
        resp = client.post(
            "/api/v1/consumption/templates/%20/versions",
            json={"name": "invalid", "planned_quantity": 10.0},
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "consumption_template_version_invalid"


def test_consumption_template_state_not_found_maps_404_contract_error():
    user = SimpleNamespace(id=12, roles=["planner"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_consumption_router.ConsumptionPlanService"
    ) as service_cls:
        service_cls.return_value.set_template_version_state.side_effect = ValueError(
            "Consumption plan not found: p-404"
        )
        resp = client.post(
            "/api/v1/consumption/templates/versions/p-404/state",
            json={"activate": True},
        )

    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "consumption_template_version_not_found"
    assert detail.get("context", {}).get("plan_id") == "p-404"


def test_consumption_template_impact_preview_returns_payload():
    user = SimpleNamespace(id=12, roles=["planner"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_consumption_router.ConsumptionPlanService"
    ) as service_cls:
        service_cls.return_value.preview_template_impact.return_value = {
            "template_key": "tpl-1",
            "candidate": {"planned_quantity": 20.0, "uom": "EA", "period_unit": "week"},
            "summary": {"versions_total": 2, "baseline_quantity": 10.0, "delta_quantity": 10.0},
            "impacts": [],
        }
        resp = client.post(
            "/api/v1/consumption/templates/tpl-1/impact-preview",
            json={"planned_quantity": 20.0},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["template_key"] == "tpl-1"
    assert body["summary"]["delta_quantity"] == 10.0


def test_consumption_actual_plan_not_found_maps_contract_error():
    user = SimpleNamespace(id=12, roles=["planner"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_consumption_router.ConsumptionPlanService"
    ) as service_cls:
        service_cls.return_value.add_actual.side_effect = ValueError(
            "Consumption plan not found: p-404"
        )
        resp = client.post(
            "/api/v1/consumption/plans/p-404/actuals",
            json={"actual_quantity": 1.0},
        )

    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "consumption_plan_not_found"
    assert detail.get("context", {}).get("plan_id") == "p-404"


def test_consumption_variance_plan_not_found_maps_contract_error():
    user = SimpleNamespace(id=12, roles=["planner"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_consumption_router.ConsumptionPlanService"
    ) as service_cls:
        service_cls.return_value.variance.side_effect = ValueError(
            "Consumption plan not found: p-404"
        )
        resp = client.get("/api/v1/consumption/plans/p-404/variance")

    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "consumption_plan_not_found"
    assert detail.get("context", {}).get("plan_id") == "p-404"


def test_breakage_status_not_found_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_breakage_router.BreakageIncidentService"
    ) as service_cls:
        service_cls.return_value.update_status.side_effect = ValueError(
            "Breakage incident not found: inc-404"
        )
        resp = client.post(
            "/api/v1/breakages/inc-404/status",
            json={"status": "closed"},
        )

    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "breakage_not_found"
    assert detail.get("context", {}).get("incident_id") == "inc-404"


def test_workorder_doc_link_invalid_maps_contract_error():
    user = SimpleNamespace(id=3, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_workorder_docs_router.WorkorderDocumentPackService"
    ) as service_cls:
        service_cls.return_value.upsert_link.side_effect = ValueError("document invalid")
        resp = client.post(
            "/api/v1/workorder-docs/links",
            json={
                "routing_id": "r-1",
                "operation_id": "op-1",
                "document_item_id": "doc-1",
            },
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "workorder_doc_link_invalid"
    assert detail.get("context", {}).get("routing_id") == "r-1"


def test_3d_overlay_batch_resolve_endpoint_returns_rows():
    user = SimpleNamespace(id=13, roles=["engineer"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_cad_3d_router.ThreeDOverlayService"
    ) as service_cls:
        service_cls.return_value.resolve_components.return_value = {
            "document_item_id": "doc-1",
            "requested": 2,
            "returned": 2,
            "hits": 1,
            "misses": 1,
            "include_missing": True,
            "results": [
                {
                    "component_ref": "C-001",
                    "found": True,
                    "hit": {"component_ref": "C-001", "item_id": "item-1"},
                },
                {"component_ref": "C-999", "found": False, "hit": None},
            ],
            "cache": {"entries": 1, "hits": 10, "misses": 2, "evictions": 0},
        }
        resp = client.post(
            "/api/v1/cad-3d/overlays/doc-1/components/resolve-batch",
            json={"component_refs": ["C-001", "C-999"], "include_missing": True},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["requested"] == 2
    assert body["hits"] == 1
    assert body["results"][1]["found"] is False


def test_3d_overlay_cache_stats_endpoint_returns_payload():
    user = SimpleNamespace(id=14, roles=["engineer"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_cad_3d_router.ThreeDOverlayService"
    ) as service_cls:
        service_cls.return_value.cache_stats.return_value = {
            "entries": 2,
            "hits": 9,
            "misses": 3,
            "evictions": 1,
            "ttl_seconds": 60,
            "max_entries": 500,
        }
        resp = client.get("/api/v1/cad-3d/overlays/cache/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"] == 2
    assert body["hits"] == 9
    assert body["ttl_seconds"] == 60


def test_parallel_ops_summary_returns_payload():
    user = SimpleNamespace(id=15, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.summary.return_value = {
            "generated_at": "2026-02-28T00:00:00",
            "window_days": 7,
            "window_since": "2026-02-21T00:00:00",
            "doc_sync": {"total": 3, "dead_letter_total": 1},
            "workflow_actions": {"total": 5},
            "breakages": {"total": 2},
            "consumption_templates": {"versions_total": 4},
            "overlay_cache": {"hits": 5, "misses": 2, "requests": 7},
            "slo_hints": [],
        }
        resp = client.get(
            "/api/v1/parallel-ops/summary?window_days=7&site_id=site-1&target_object=ECO&template_key=tpl-1"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["window_days"] == 7
    assert body["doc_sync"]["dead_letter_total"] == 1
    assert body["overlay_cache"]["requests"] == 7
    assert body["operator_id"] == 15


def test_parallel_ops_summary_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=15, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.summary.side_effect = ValueError(
            "window_days must be one of: 1, 7, 14, 30, 90"
        )
        resp = client.get("/api/v1/parallel-ops/summary?window_days=10")

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("window_days") == 10


def test_parallel_ops_trends_returns_payload():
    user = SimpleNamespace(id=18, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.trends.return_value = {
            "generated_at": "2026-02-28T00:00:00",
            "window_days": 7,
            "window_since": "2026-02-21T00:00:00",
            "bucket_days": 1,
            "filters": {"site_id": "site-1", "target_object": "ECO", "template_key": "tpl-1"},
            "points": [
                {
                    "bucket_start": "2026-02-27T00:00:00",
                    "bucket_end": "2026-02-28T00:00:00",
                    "doc_sync": {
                        "total": 2,
                        "failed_total": 1,
                        "dead_letter_total": 1,
                        "directions": {"push": 1, "pull": 1, "unknown": 0},
                        "dead_letter_directions": {"push": 0, "pull": 1, "unknown": 0},
                        "success_rate": 0.5,
                        "dead_letter_rate": 0.5,
                    },
                    "workflow_actions": {"total": 1, "failed_total": 1, "failed_rate": 1.0},
                    "breakages": {"total": 1, "open_total": 1, "open_rate": 1.0},
                }
            ],
            "aggregates": {
                "doc_sync_total": 2,
                "doc_sync_failed_total": 1,
                "doc_sync_dead_letter_total": 1,
                "doc_sync_push_total": 1,
                "doc_sync_pull_total": 1,
                "doc_sync_dead_letter_pull_total": 1,
                "workflow_total": 1,
                "workflow_failed_total": 1,
                "breakages_total": 1,
                "breakages_open_total": 1,
            },
            "consumption_templates": {"versions_total": 1},
            "overlay_cache": {"requests": 2, "hit_rate": 0.5},
        }
        resp = client.get(
            "/api/v1/parallel-ops/trends?window_days=7&bucket_days=1&site_id=site-1&target_object=ECO&template_key=tpl-1"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["bucket_days"] == 1
    assert body["aggregates"]["doc_sync_total"] == 2
    assert body["aggregates"]["doc_sync_push_total"] == 1
    assert body["operator_id"] == 18


def test_parallel_ops_trends_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=18, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.trends.side_effect = ValueError(
            "bucket_days must be <= window_days"
        )
        resp = client.get("/api/v1/parallel-ops/trends?window_days=7&bucket_days=14")

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("bucket_days") == 14


def test_parallel_ops_trends_export_returns_download_response():
    user = SimpleNamespace(id=18, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_trends.return_value = {
            "content": b"bucket_start,bucket_end,doc_sync_total\n2026-02-27,2026-02-28,2\n",
            "media_type": "text/csv",
            "filename": "parallel-ops-trends.csv",
        }
        resp = client.get(
            "/api/v1/parallel-ops/trends/export?window_days=7&bucket_days=1&export_format=csv"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="parallel-ops-trends.csv"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.headers.get("x-operator-id") == "18"
    assert "doc_sync_total" in resp.text


def test_parallel_ops_trends_export_passes_locale_query_contract():
    user = SimpleNamespace(id=18, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_trends.return_value = {
            "content": b'{"bucket_days": 1, "locale": {"id": "rp-ops-trends-zh"}}',
            "media_type": "application/json",
            "filename": "parallel-ops-trends.json",
        }
        resp = client.get(
            "/api/v1/parallel-ops/trends/export"
            "?window_days=7"
            "&bucket_days=1"
            "&export_format=json"
            "&report_lang=zh_CN"
            "&report_type=parallel_ops_trends"
            "&locale_profile_id=rp-ops-trends-zh"
        )

    assert resp.status_code == 200
    kwargs = service_cls.return_value.export_trends.call_args.kwargs
    assert kwargs["report_lang"] == "zh_CN"
    assert kwargs["report_type"] == "parallel_ops_trends"
    assert kwargs["locale_profile_id"] == "rp-ops-trends-zh"
    assert resp.json()["locale"]["id"] == "rp-ops-trends-zh"


def test_parallel_ops_trends_export_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=18, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_trends.side_effect = ValueError(
            "export_format must be json, csv or md"
        )
        resp = client.get(
            "/api/v1/parallel-ops/trends/export?window_days=7&bucket_days=1&export_format=xlsx"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("export_format") == "xlsx"


def test_parallel_ops_alerts_returns_payload():
    user = SimpleNamespace(id=18, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.alerts.return_value = {
            "generated_at": "2026-02-28T00:00:00",
            "window_days": 7,
            "window_since": "2026-02-21T00:00:00",
            "filters": {
                "site_id": "site-1",
                "target_object": "ECO",
                "template_key": "tpl-1",
                "level": "warn",
            },
            "status": "warning",
            "total": 1,
            "by_code": {"doc_sync_dead_letter_rate_high": 1},
            "hints": [
                {
                    "level": "warn",
                    "code": "doc_sync_dead_letter_rate_high",
                    "message": "doc sync dead-letter rate high",
                }
            ],
        }
        resp = client.get(
            "/api/v1/parallel-ops/alerts?window_days=7&site_id=site-1&target_object=ECO&template_key=tpl-1&level=warn"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "warning"
    assert body["total"] == 1
    assert body["by_code"]["doc_sync_dead_letter_rate_high"] == 1
    assert body["operator_id"] == 18


def test_parallel_ops_alerts_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=18, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.alerts.side_effect = ValueError(
            "level must be one of: warn, critical, info"
        )
        resp = client.get("/api/v1/parallel-ops/alerts?window_days=7&level=oops")

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("level") == "oops"


def test_parallel_ops_summary_export_returns_download_response():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_summary.return_value = {
            "content": b'{"window_days": 7}',
            "media_type": "application/json",
            "filename": "parallel-ops-summary.json",
        }
        resp = client.get(
            "/api/v1/parallel-ops/summary/export?window_days=7&export_format=json"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/json")
    assert 'filename="parallel-ops-summary.json"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.headers.get("x-operator-id") == "19"
    assert '"window_days": 7' in resp.text


def test_parallel_ops_summary_export_passes_locale_query_contract():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_summary.return_value = {
            "content": b'{"window_days": 7, "locale": {"id": "rp-ops-zh"}}',
            "media_type": "application/json",
            "filename": "parallel-ops-summary.json",
        }
        resp = client.get(
            "/api/v1/parallel-ops/summary/export"
            "?window_days=7"
            "&export_format=json"
            "&report_lang=zh_CN"
            "&report_type=parallel_ops_summary"
            "&locale_profile_id=rp-ops-zh"
        )

    assert resp.status_code == 200
    kwargs = service_cls.return_value.export_summary.call_args.kwargs
    assert kwargs["report_lang"] == "zh_CN"
    assert kwargs["report_type"] == "parallel_ops_summary"
    assert kwargs["locale_profile_id"] == "rp-ops-zh"
    assert resp.json()["locale"]["id"] == "rp-ops-zh"


def test_parallel_ops_summary_export_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_summary.side_effect = ValueError(
            "export_format must be json, csv or md"
        )
        resp = client.get(
            "/api/v1/parallel-ops/summary/export?window_days=7&export_format=xlsx"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("export_format") == "xlsx"


def test_parallel_ops_summary_accepts_threshold_overrides():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.summary.return_value = {
            "generated_at": "2026-02-28T00:00:00",
            "window_days": 7,
            "window_since": "2026-02-21T00:00:00",
            "doc_sync": {"total": 2, "dead_letter_total": 1},
            "workflow_actions": {"total": 1},
            "breakages": {"total": 1},
            "consumption_templates": {"versions_total": 1},
            "overlay_cache": {"requests": 7, "hit_rate": 0.3},
            "slo_hints": [],
            "slo_thresholds": {
                "overlay_cache_hit_rate_warn": 0.2,
                "overlay_cache_min_requests_warn": 20,
                "doc_sync_dead_letter_rate_warn": 0.9,
                "doc_sync_checkout_gate_block_on_dead_letter_only": True,
                "doc_sync_checkout_gate_max_pending_warn": 5,
                "doc_sync_checkout_gate_max_processing_warn": 2,
                "doc_sync_checkout_gate_max_failed_warn": 1,
                "doc_sync_checkout_gate_max_dead_letter_warn": 0,
                "doc_sync_dead_letter_trend_delta_warn": 3,
                "workflow_failed_rate_warn": 0.9,
                "breakage_open_rate_warn": 0.9,
                "breakage_helpdesk_failed_rate_warn": 0.8,
                "breakage_helpdesk_failed_total_warn": 3,
                "breakage_helpdesk_triage_coverage_warn": 0.5,
                "breakage_helpdesk_export_failed_total_warn": 2,
                "breakage_helpdesk_provider_failed_rate_warn": 0.7,
                "breakage_helpdesk_provider_failed_min_jobs_warn": 4,
                "breakage_helpdesk_provider_failed_rate_critical": 0.95,
                "breakage_helpdesk_provider_failed_min_jobs_critical": 8,
                "breakage_helpdesk_replay_failed_rate_warn": 0.6,
                "breakage_helpdesk_replay_failed_total_warn": 2,
                "breakage_helpdesk_replay_pending_total_warn": 6,
            },
        }
        resp = client.get(
            "/api/v1/parallel-ops/summary?window_days=7&overlay_cache_hit_rate_warn=0.2&overlay_cache_min_requests_warn=20&doc_sync_dead_letter_rate_warn=0.9&doc_sync_checkout_gate_block_on_dead_letter_only=true&doc_sync_checkout_gate_max_pending_warn=5&doc_sync_checkout_gate_max_processing_warn=2&doc_sync_checkout_gate_max_failed_warn=1&doc_sync_checkout_gate_max_dead_letter_warn=0&doc_sync_dead_letter_trend_delta_warn=3&workflow_failed_rate_warn=0.9&breakage_open_rate_warn=0.9&breakage_helpdesk_failed_rate_warn=0.8&breakage_helpdesk_failed_total_warn=3&breakage_helpdesk_triage_coverage_warn=0.5&breakage_helpdesk_export_failed_total_warn=2&breakage_helpdesk_provider_failed_rate_warn=0.7&breakage_helpdesk_provider_failed_min_jobs_warn=4&breakage_helpdesk_provider_failed_rate_critical=0.95&breakage_helpdesk_provider_failed_min_jobs_critical=8&breakage_helpdesk_replay_failed_rate_warn=0.6&breakage_helpdesk_replay_failed_total_warn=2&breakage_helpdesk_replay_pending_total_warn=6"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["slo_thresholds"]["doc_sync_dead_letter_rate_warn"] == 0.9
    assert body["slo_thresholds"]["doc_sync_checkout_gate_block_on_dead_letter_only"] is True
    assert body["slo_thresholds"]["doc_sync_checkout_gate_max_pending_warn"] == 5
    assert body["slo_thresholds"]["doc_sync_checkout_gate_max_processing_warn"] == 2
    assert body["slo_thresholds"]["doc_sync_checkout_gate_max_failed_warn"] == 1
    assert body["slo_thresholds"]["doc_sync_checkout_gate_max_dead_letter_warn"] == 0
    assert body["slo_thresholds"]["doc_sync_dead_letter_trend_delta_warn"] == 3
    assert body["slo_thresholds"]["breakage_helpdesk_failed_total_warn"] == 3
    assert body["slo_thresholds"]["breakage_helpdesk_triage_coverage_warn"] == 0.5
    assert body["slo_thresholds"]["breakage_helpdesk_export_failed_total_warn"] == 2
    assert body["slo_thresholds"]["breakage_helpdesk_provider_failed_rate_warn"] == 0.7
    assert body["slo_thresholds"]["breakage_helpdesk_provider_failed_min_jobs_warn"] == 4
    assert body["slo_thresholds"]["breakage_helpdesk_provider_failed_rate_critical"] == 0.95
    assert body["slo_thresholds"]["breakage_helpdesk_provider_failed_min_jobs_critical"] == 8
    assert body["slo_thresholds"]["breakage_helpdesk_replay_failed_rate_warn"] == 0.6
    assert body["slo_thresholds"]["breakage_helpdesk_replay_failed_total_warn"] == 2
    assert body["slo_thresholds"]["breakage_helpdesk_replay_pending_total_warn"] == 6


def test_parallel_ops_summary_invalid_threshold_maps_contract_error():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.summary.side_effect = ValueError(
            "doc_sync_dead_letter_rate_warn must be between 0 and 1"
        )
        resp = client.get(
            "/api/v1/parallel-ops/summary?window_days=7&doc_sync_dead_letter_rate_warn=1.2"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("doc_sync_dead_letter_rate_warn") == 1.2


def test_parallel_ops_summary_invalid_checkout_gate_threshold_maps_contract_error():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.summary.side_effect = ValueError(
            "doc_sync_checkout_gate_max_failed_warn must be >= 0"
        )
        resp = client.get(
            "/api/v1/parallel-ops/summary?window_days=7&doc_sync_checkout_gate_max_failed_warn=-1"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("doc_sync_checkout_gate_max_failed_warn") == -1


def test_parallel_ops_doc_sync_failures_returns_payload():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.doc_sync_failures.return_value = {
            "window_days": 7,
            "window_since": "2026-02-21T00:00:00",
            "site_filter": "site-1",
            "total": 1,
            "by_direction": {"push": 1},
            "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
            "jobs": [
                {
                    "id": "job-1",
                    "task_type": "document_sync_push",
                    "status": "failed",
                    "site_id": "site-1",
                    "direction": "push",
                }
            ],
        }
        resp = client.get("/api/v1/parallel-ops/doc-sync/failures?window_days=7&site_id=site-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["by_direction"]["push"] == 1
    assert body["jobs"][0]["status"] == "failed"
    assert body["jobs"][0]["direction"] == "push"
    assert body["operator_id"] == 16


def test_parallel_ops_workflow_failures_returns_payload():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.workflow_failures.return_value = {
            "window_days": 7,
            "window_since": "2026-02-21T00:00:00",
            "target_object_filter": "ECO",
            "total": 1,
            "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
            "runs": [
                {"id": "run-1", "status": "failed", "result_code": "RETRY_EXHAUSTED"}
            ],
        }
        resp = client.get(
            "/api/v1/parallel-ops/workflow/failures?window_days=7&target_object=ECO"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["runs"][0]["result_code"] == "RETRY_EXHAUSTED"
    assert body["operator_id"] == 16


def test_parallel_ops_breakage_helpdesk_failures_returns_payload():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.breakage_helpdesk_failures.return_value = {
            "window_days": 7,
            "window_since": "2026-02-21T00:00:00",
            "provider_filter": "jira",
            "failure_category_filter": "transient",
            "provider_ticket_status_filter": "on_hold",
            "total": 1,
            "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
            "by_provider": {"jira": 1},
            "by_failure_category": {"transient": 1},
            "by_provider_ticket_status": {"on_hold": 1},
            "jobs": [
                {
                    "id": "job-bh-1",
                    "status": "failed",
                    "provider": "jira",
                    "failure_category": "transient",
                    "provider_ticket_status": "on_hold",
                }
            ],
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures"
            "?window_days=7&provider=jira&failure_category=transient&provider_ticket_status=on_hold"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["jobs"][0]["provider"] == "jira"
    assert body["jobs"][0]["failure_category"] == "transient"
    assert body["jobs"][0]["provider_ticket_status"] == "on_hold"
    assert body["operator_id"] == 16


def test_parallel_ops_breakage_helpdesk_failure_trends_returns_payload():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.breakage_helpdesk_failure_trends.return_value = {
            "generated_at": "2026-03-06T10:00:00",
            "window_days": 7,
            "window_since": "2026-02-28T10:00:00",
            "bucket_days": 1,
            "filters": {
                "provider": "zendesk",
                "failure_category": "transient",
                "provider_ticket_status": "on_hold",
            },
            "points": [
                {
                    "bucket_start": "2026-03-06T00:00:00",
                    "bucket_end": "2026-03-06T23:59:59",
                    "total_jobs": 1,
                    "failed_jobs": 1,
                    "failed_rate": 1.0,
                    "by_failure_category": {"transient": 1},
                }
            ],
            "aggregates": {"total_jobs": 1, "failed_jobs": 1, "failed_rate": 1.0},
            "by_failure_category": {"transient": 1},
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/trends"
            "?window_days=7&bucket_days=1&provider=zendesk&failure_category=transient&provider_ticket_status=on_hold"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates"]["failed_jobs"] == 1
    assert body["points"][0]["failed_rate"] == 1.0
    assert body["operator_id"] == 16


def test_parallel_ops_breakage_helpdesk_failures_triage_returns_payload():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.breakage_helpdesk_failure_triage.return_value = {
            "generated_at": "2026-03-06T10:00:00",
            "window_days": 7,
            "window_since": "2026-02-28T10:00:00",
            "filters": {
                "provider": "zendesk",
                "failure_category": "transient",
                "provider_ticket_status": "on_hold",
            },
            "top_n": 5,
            "total_failed_jobs": 3,
            "hotspots": {
                "failure_categories": [{"key": "transient", "count": 2, "ratio": 0.6667}]
            },
            "replay_candidates_total": 2,
            "replay_candidates": [{"id": "job-bh-1"}],
            "triage_actions": [{"code": "retry_with_backoff"}],
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/triage"
            "?window_days=7&provider=zendesk&failure_category=transient&provider_ticket_status=on_hold&top_n=5"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_failed_jobs"] == 3
    assert body["replay_candidates_total"] == 2
    assert body["triage_actions"][0]["code"] == "retry_with_backoff"
    assert body["operator_id"] == 16


def test_parallel_ops_breakage_helpdesk_failures_triage_apply_returns_payload():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.apply_breakage_helpdesk_failure_triage.return_value = {
            "updated_total": 1,
            "updated_jobs": [{"id": "job-bh-1", "triage_status": "in_progress"}],
            "skipped_not_found_total": 0,
        }
        resp = client.post(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/triage/apply",
            json={
                "triage_status": "in_progress",
                "job_ids": ["job-bh-1"],
                "triage_owner": "ops-l2",
                "root_cause": "provider_rate_limit",
                "resolution": "retry_with_backoff",
                "note": "triage note",
                "tags": ["hot", "provider"],
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["updated_total"] == 1
    assert body["updated_jobs"][0]["triage_status"] == "in_progress"
    assert body["operator_id"] == 16
    assert db.commit.called


def test_parallel_ops_breakage_helpdesk_failures_replay_enqueue_returns_payload():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.enqueue_breakage_helpdesk_failure_replay_jobs.return_value = {
            "source": "job_ids",
            "batch_id": "bh-replay-1",
            "created_jobs_total": 1,
            "created_jobs": [
                {
                    "batch_id": "bh-replay-1",
                    "source_job_id": "job-bh-1",
                    "job_id": "job-replay-1",
                }
            ],
            "errors_total": 0,
        }
        resp = client.post(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/enqueue",
            json={"job_ids": ["job-bh-1"], "limit": 10},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["created_jobs_total"] == 1
    assert body["batch_id"] == "bh-replay-1"
    assert body["created_jobs"][0]["job_id"] == "job-replay-1"
    assert body["created_jobs"][0]["batch_id"] == "bh-replay-1"
    assert body["operator_id"] == 16
    assert db.commit.called


def test_parallel_ops_breakage_helpdesk_failures_replay_batch_returns_payload():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.get_breakage_helpdesk_failure_replay_batch.return_value = {
            "generated_at": "2026-03-06T10:00:00",
            "batch_id": "bh-replay-1",
            "total": 1,
            "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
            "requested_total": 1,
            "requested_by_ids": [16],
            "by_job_status": {"pending": 1},
            "by_sync_status": {"queued": 1},
            "by_provider": {"zendesk": 1},
            "by_failure_category": {"transient": 1},
            "jobs": [{"job_id": "job-replay-1", "source_job_id": "job-bh-1"}],
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/batches/bh-replay-1"
            "?page=1&page_size=20"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["batch_id"] == "bh-replay-1"
    assert body["total"] == 1
    assert body["jobs"][0]["job_id"] == "job-replay-1"
    assert body["operator_id"] == 16


def test_parallel_ops_breakage_helpdesk_failures_replay_batches_list_returns_payload():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.list_breakage_helpdesk_failure_replay_batches.return_value = {
            "generated_at": "2026-03-06T10:00:00",
            "window_days": 7,
            "window_since": "2026-02-28T10:00:00",
            "filters": {
                "provider": "zendesk",
                "job_status": None,
                "sync_status": None,
            },
            "total_batches": 1,
            "total_jobs": 1,
            "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
            "by_job_status": {"pending": 1},
            "by_sync_status": {"queued": 1},
            "by_provider": {"zendesk": 1},
            "by_failure_category": {"transient": 1},
            "batches": [{"batch_id": "bh-replay-1", "jobs_total": 1}],
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/batches"
            "?window_days=7&provider=zendesk&page=1&page_size=20"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_batches"] == 1
    assert body["batches"][0]["batch_id"] == "bh-replay-1"
    assert body["operator_id"] == 16


def test_parallel_ops_breakage_helpdesk_failures_replay_trends_returns_payload():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.breakage_helpdesk_replay_trends.return_value = {
            "generated_at": "2026-03-06T10:00:00",
            "window_days": 7,
            "window_since": "2026-02-28T10:00:00",
            "bucket_days": 1,
            "filters": {
                "provider": "zendesk",
                "job_status": None,
                "sync_status": None,
            },
            "points": [
                {
                    "bucket_start": "2026-03-05T00:00:00",
                    "bucket_end": "2026-03-06T00:00:00",
                    "total_jobs": 2,
                    "failed_jobs": 1,
                    "failed_rate": 0.5,
                    "batches_total": 1,
                    "by_provider": {"zendesk": 2},
                    "by_job_status": {"failed": 1, "pending": 1},
                    "by_sync_status": {"failed": 1, "queued": 1},
                }
            ],
            "aggregates": {
                "total_jobs": 2,
                "failed_jobs": 1,
                "failed_rate": 0.5,
                "total_batches": 1,
            },
            "by_provider": {"zendesk": 2},
            "by_job_status": {"failed": 1, "pending": 1},
            "by_sync_status": {"failed": 1, "queued": 1},
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/trends"
            "?window_days=7&bucket_days=1&provider=zendesk"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates"]["total_jobs"] == 2
    assert body["aggregates"]["total_batches"] == 1
    assert body["operator_id"] == 16


def test_parallel_ops_breakage_helpdesk_failures_replay_trends_invalid_maps_contract_error():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.breakage_helpdesk_replay_trends.side_effect = ValueError(
            "bucket_days must be <= window_days"
        )
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/trends"
            "?window_days=7&bucket_days=14"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("bucket_days") == 14


def test_parallel_ops_breakage_helpdesk_failures_replay_trends_export_returns_download_response():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_breakage_helpdesk_replay_trends.return_value = {
            "content": b"bucket_start,bucket_end,total_jobs,failed_jobs,failed_rate,batches_total\n",
            "media_type": "text/csv",
            "filename": "parallel-ops-breakage-helpdesk-replay-trends.csv",
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/trends/export"
            "?window_days=7&bucket_days=1&provider=zendesk&export_format=csv"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="parallel-ops-breakage-helpdesk-replay-trends.csv"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.headers.get("x-operator-id") == "19"
    assert "bucket_start,bucket_end,total_jobs" in resp.text


def test_parallel_ops_breakage_helpdesk_failures_replay_trends_export_invalid_maps_contract_error():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_breakage_helpdesk_replay_trends.side_effect = (
            ValueError("export_format must be json, csv or md")
        )
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/trends/export"
            "?window_days=7&bucket_days=1&export_format=xlsx"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("export_format") == "xlsx"


def test_parallel_ops_breakage_helpdesk_failures_replay_batch_export_returns_download_response():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_breakage_helpdesk_failure_replay_batch.return_value = {
            "content": b'batch_id,job_id\\nbh-replay-1,job-replay-1\\n',
            "media_type": "text/csv",
            "filename": "parallel-ops-breakage-helpdesk-replay-batch-bh-replay-1.csv",
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/batches/bh-replay-1/export"
            "?export_format=csv"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="parallel-ops-breakage-helpdesk-replay-batch-bh-replay-1.csv"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.headers.get("x-operator-id") == "19"
    assert "bh-replay-1" in resp.text


def test_parallel_ops_breakage_helpdesk_failures_replay_batch_not_found_maps_contract_error():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.get_breakage_helpdesk_failure_replay_batch.side_effect = (
            ValueError("Replay batch not found: bh-replay-missing")
        )
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/batches/bh-replay-missing"
        )

    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_replay_batch_not_found"
    assert detail.get("context", {}).get("batch_id") == "bh-replay-missing"


def test_parallel_ops_breakage_helpdesk_failures_replay_batch_export_not_found_maps_contract_error():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_breakage_helpdesk_failure_replay_batch.side_effect = (
            ValueError("Replay batch not found: bh-replay-missing")
        )
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/batches/bh-replay-missing/export"
            "?export_format=json"
        )

    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_replay_batch_not_found"
    assert detail.get("context", {}).get("batch_id") == "bh-replay-missing"


def test_parallel_ops_breakage_helpdesk_failures_replay_batches_cleanup_returns_payload():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.cleanup_breakage_helpdesk_failure_replay_batches.return_value = {
            "generated_at": "2026-03-06T12:00:00",
            "ttl_hours": 24,
            "limit": 200,
            "dry_run": True,
            "cutoff_at": "2026-03-05T12:00:00",
            "archived_jobs": 2,
            "archived_batches": 1,
            "batch_ids": ["bh-replay-1"],
            "job_ids": ["job-replay-1", "job-replay-2"],
        }
        resp = client.post(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/batches/cleanup",
            json={"ttl_hours": 24, "limit": 200, "dry_run": True},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["archived_jobs"] == 2
    assert body["dry_run"] is True
    assert body["batch_ids"][0] == "bh-replay-1"
    assert body["operator_id"] == 19
    service_cls.return_value.cleanup_breakage_helpdesk_failure_replay_batches.assert_called_once_with(
        ttl_hours=24,
        limit=200,
        dry_run=True,
    )
    assert db.commit.called


def test_parallel_ops_breakage_helpdesk_failures_replay_batches_cleanup_invalid_maps_contract_error():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.cleanup_breakage_helpdesk_failure_replay_batches.side_effect = (
            ValueError("limit must be between 1 and 1000")
        )
        resp = client.post(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/batches/cleanup",
            json={"ttl_hours": 24, "limit": 200, "dry_run": True},
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("ttl_hours") == 24
    assert detail.get("context", {}).get("dry_run") is True
    assert db.rollback.called


def test_parallel_ops_breakage_helpdesk_failures_export_returns_download_response():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_breakage_helpdesk_failures.return_value = {
            "content": b"id,provider,error_code\\njob-1,jira,provider_timeout\\n",
            "media_type": "text/csv",
            "filename": "parallel-ops-breakage-helpdesk-failures.csv",
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export"
            "?window_days=7&provider=jira&failure_category=transient&provider_ticket_status=on_hold&export_format=csv"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="parallel-ops-breakage-helpdesk-failures.csv"' in (
        resp.headers.get("content-disposition", "")
    )
    assert resp.headers.get("x-operator-id") == "19"
    assert "provider_timeout" in resp.text


def test_parallel_ops_breakage_helpdesk_failures_export_zip_returns_download_response():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_breakage_helpdesk_failures.return_value = {
            "content": b"PK\x03\x04",
            "media_type": "application/zip",
            "filename": "parallel-ops-breakage-helpdesk-failures.zip",
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export?window_days=7&export_format=zip"
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/zip")
    assert 'filename="parallel-ops-breakage-helpdesk-failures.zip"' in (
        resp.headers.get("content-disposition", "")
    )


def test_parallel_ops_breakage_helpdesk_failures_export_job_create_run_status_download_endpoints():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.enqueue_breakage_helpdesk_failures_export_job.return_value = {
            "job_id": "job-bh-exp-1",
            "status": "pending",
            "download_ready": False,
        }
        create_resp = client.post(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export/jobs",
            json={
                "window_days": 7,
                "provider": "zendesk",
                "failure_category": "transient",
                "provider_ticket_status": "on_hold",
                "export_format": "csv",
                "execute_immediately": False,
            },
        )
        service_cls.return_value.run_breakage_helpdesk_failures_export_job.return_value = {
            "job_id": "job-bh-exp-1",
            "status": "completed",
            "download_ready": True,
        }
        run_resp = client.post(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export/jobs/job-bh-exp-1/run"
        )
        service_cls.return_value.get_breakage_helpdesk_failures_export_job.return_value = {
            "job_id": "job-bh-exp-1",
            "status": "completed",
            "download_ready": True,
        }
        status_resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export/jobs/job-bh-exp-1"
        )
        service_cls.return_value.download_breakage_helpdesk_failures_export_job.return_value = {
            "content": b"id,provider,error_code\\njob-bh-1,zendesk,provider_timeout\\n",
            "media_type": "text/csv",
            "filename": "parallel-ops-breakage-helpdesk-failures.csv",
        }
        download_resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export/jobs/job-bh-exp-1/download"
        )
        service_cls.return_value.cleanup_expired_breakage_helpdesk_failures_export_results.return_value = {
            "ttl_hours": 24,
            "limit": 200,
            "expired_jobs": 1,
            "job_ids": ["job-bh-exp-0"],
        }
        cleanup_resp = client.post(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export/jobs/cleanup",
            json={"ttl_hours": 24, "limit": 200},
        )

    assert create_resp.status_code == 200
    assert create_resp.json()["job_id"] == "job-bh-exp-1"
    assert create_resp.json()["operator_id"] == 19
    assert run_resp.status_code == 200
    assert run_resp.json()["status"] == "completed"
    assert status_resp.status_code == 200
    assert status_resp.json()["download_ready"] is True
    assert status_resp.json()["operator_id"] == 19
    assert download_resp.status_code == 200
    assert download_resp.headers.get("content-type", "").startswith("text/csv")
    assert 'filename="parallel-ops-breakage-helpdesk-failures.csv"' in (
        download_resp.headers.get("content-disposition", "")
    )
    assert download_resp.headers.get("x-operator-id") == "19"
    assert "provider_timeout" in download_resp.text
    assert cleanup_resp.status_code == 200
    assert cleanup_resp.json()["expired_jobs"] == 1
    assert cleanup_resp.json()["operator_id"] == 19
    assert db.commit.called


def test_parallel_ops_breakage_helpdesk_failures_export_jobs_overview_returns_payload():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.breakage_helpdesk_failures_export_jobs_overview.return_value = {
            "generated_at": "2026-03-06T10:00:00",
            "window_days": 7,
            "window_since": "2026-02-28T10:00:00",
            "filters": {
                "provider": "zendesk",
                "failure_category": "transient",
                "export_format": "csv",
            },
            "total": 1,
            "pagination": {"page": 1, "page_size": 20, "pages": 1, "total": 1},
            "by_job_status": {"completed": 1},
            "by_sync_status": {"completed": 1},
            "by_provider": {"zendesk": 1},
            "by_failure_category": {"transient": 1},
            "by_export_format": {"csv": 1},
            "duration_seconds": {
                "count": 1,
                "min_seconds": 0.1234,
                "max_seconds": 0.1234,
                "avg_seconds": 0.1234,
                "p50_seconds": 0.1234,
                "p95_seconds": 0.1234,
            },
            "jobs": [
                {
                    "job_id": "job-bh-exp-1",
                    "provider": "zendesk",
                    "failure_category": "transient",
                    "export_format": "csv",
                }
            ],
        }
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export/jobs/overview"
            "?window_days=7&provider=zendesk&failure_category=transient&export_format=csv&page=1&page_size=20"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["by_provider"]["zendesk"] == 1
    assert body["by_failure_category"]["transient"] == 1
    assert body["duration_seconds"]["count"] == 1
    assert body["jobs"][0]["failure_category"] == "transient"
    assert body["jobs"][0]["export_format"] == "csv"
    assert body["operator_id"] == 19


def test_parallel_ops_breakage_helpdesk_failures_export_job_not_found_maps_contract_error():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.get_breakage_helpdesk_failures_export_job.side_effect = (
            ValueError("Parallel ops breakage-helpdesk export job not found: job-missing")
        )
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export/jobs/job-missing"
        )

    assert resp.status_code == 404
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_export_job_not_found"
    assert detail.get("context", {}).get("job_id") == "job-missing"


def test_parallel_ops_metrics_returns_prometheus_text():
    user = SimpleNamespace(id=17, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.prometheus_metrics.return_value = (
            "# HELP yuantus_parallel_doc_sync_jobs_total ...\n"
            "yuantus_parallel_doc_sync_jobs_total{window_days=\"7\"} 3\n"
        )
        resp = client.get("/api/v1/parallel-ops/metrics?window_days=7")

    assert resp.status_code == 200
    assert resp.text.startswith("# HELP yuantus_parallel_doc_sync_jobs_total")
    assert "yuantus_parallel_doc_sync_jobs_total" in resp.text
    assert resp.headers.get("x-operator-id") == "17"
    assert resp.headers.get("content-type", "").startswith("text/plain")


def test_parallel_ops_doc_sync_failures_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.doc_sync_failures.side_effect = ValueError(
            "page_size must be between 1 and 200"
        )
        resp = client.get("/api/v1/parallel-ops/doc-sync/failures?window_days=10")

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"


def test_parallel_ops_breakage_helpdesk_failures_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.breakage_helpdesk_failures.side_effect = ValueError(
            "page_size must be between 1 and 200"
        )
        resp = client.get("/api/v1/parallel-ops/breakage-helpdesk/failures?window_days=10")

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"


def test_parallel_ops_breakage_helpdesk_failure_trends_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.breakage_helpdesk_failure_trends.side_effect = ValueError(
            "bucket_days must be <= window_days"
        )
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/trends?window_days=7&bucket_days=14"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"


def test_parallel_ops_breakage_helpdesk_failures_triage_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.breakage_helpdesk_failure_triage.side_effect = ValueError(
            "top_n must be between 1 and 50"
        )
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/triage?window_days=7&top_n=5"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("top_n") == 5


def test_parallel_ops_breakage_helpdesk_failures_triage_apply_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.apply_breakage_helpdesk_failure_triage.side_effect = (
            ValueError("triage_status must be one of: ignored, in_progress, open, resolved")
        )
        resp = client.post(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/triage/apply",
            json={"triage_status": "oops", "job_ids": ["job-bh-1"]},
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("triage_status") == "oops"


def test_parallel_ops_breakage_helpdesk_failures_replay_enqueue_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=16, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.enqueue_breakage_helpdesk_failure_replay_jobs.side_effect = (
            ValueError("window_days must be one of: 1, 7, 14, 30, 90")
        )
        resp = client.post(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/replay/enqueue",
            json={"window_days": 8, "limit": 100},
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("window_days") == 8


def test_parallel_ops_breakage_helpdesk_failures_export_jobs_overview_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.breakage_helpdesk_failures_export_jobs_overview.side_effect = (
            ValueError("export_format must be json, csv, md or zip")
        )
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export/jobs/overview"
            "?window_days=7&export_format=xlsx"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("export_format") == "xlsx"


def test_parallel_ops_breakage_helpdesk_failures_export_invalid_request_maps_contract_error():
    user = SimpleNamespace(id=19, roles=["admin"], is_superuser=False)
    client, _db = _client_with_user(user)

    with patch(
        "yuantus.meta_engine.web.parallel_tasks_ops_router.ParallelOpsOverviewService"
    ) as service_cls:
        service_cls.return_value.export_breakage_helpdesk_failures.side_effect = ValueError(
            "export_format must be json, csv or md"
        )
        resp = client.get(
            "/api/v1/parallel-ops/breakage-helpdesk/failures/export?window_days=7&export_format=xlsx"
        )

    assert resp.status_code == 400
    detail = resp.json().get("detail") or {}
    assert detail.get("code") == "parallel_ops_invalid_request"
    assert detail.get("context", {}).get("export_format") == "xlsx"


# --------------------------------------------------------------------------
# G3 3D visual explode — explode-config endpoints (cad-3d router)
# --------------------------------------------------------------------------

_EXPLODE_SVC = "yuantus.meta_engine.web.parallel_tasks_cad_3d_router.ThreeDOverlayService"


def test_put_3d_explode_valid_persists_and_returns_config():
    user = SimpleNamespace(id=1, roles=["engineer"], is_superuser=False)
    client, _db = _client_with_user(user)
    stored = {
        "factor": 1.5,
        "mode": "radial",
        "offsets": [{"component_ref": "C-001", "offset": [1.0, 0.0, 0.0]}],
    }
    with patch(_EXPLODE_SVC) as service_cls:
        service_cls.return_value.upsert_explode.return_value = stored
        resp = client.put(
            "/api/v1/cad-3d/explode/doc-1",
            json={
                "factor": 1.5,
                "mode": "radial",
                "offsets": [{"component_ref": "C-001", "offset": [1, 0, 0]}],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["document_item_id"] == "doc-1"
    assert body["explode"]["factor"] == 1.5
    service_cls.return_value.upsert_explode.assert_called_once()


def test_put_3d_explode_rejects_bad_offset_length():
    user = SimpleNamespace(id=1, roles=["engineer"], is_superuser=False)
    client, _db = _client_with_user(user)
    resp = client.put(
        "/api/v1/cad-3d/explode/doc-1",
        json={
            "factor": 1.0,
            "mode": "radial",
            "offsets": [{"component_ref": "C-001", "offset": [1, 0]}],
        },
    )
    assert resp.status_code == 422


def test_put_3d_explode_rejects_out_of_range_factor():
    user = SimpleNamespace(id=1, roles=["engineer"], is_superuser=False)
    client, _db = _client_with_user(user)
    resp = client.put(
        "/api/v1/cad-3d/explode/doc-1",
        json={"factor": -1.0, "mode": "radial", "offsets": []},
    )
    assert resp.status_code == 422


def test_get_3d_explode_returns_config():
    user = SimpleNamespace(id=1, roles=["engineer"], is_superuser=False)
    client, _db = _client_with_user(user)
    with patch(_EXPLODE_SVC) as service_cls:
        service_cls.return_value.get_explode.return_value = {
            "factor": 2.0,
            "mode": "axial",
            "offsets": [],
        }
        resp = client.get("/api/v1/cad-3d/explode/doc-1")
    assert resp.status_code == 200
    assert resp.json()["explode"]["factor"] == 2.0


def test_get_3d_explode_none_when_absent():
    user = SimpleNamespace(id=1, roles=["engineer"], is_superuser=False)
    client, _db = _client_with_user(user)
    with patch(_EXPLODE_SVC) as service_cls:
        service_cls.return_value.get_explode.return_value = None
        resp = client.get("/api/v1/cad-3d/explode/doc-1")
    assert resp.status_code == 200
    assert resp.json()["explode"] is None


def test_get_3d_explode_visibility_denied_returns_403():
    user = SimpleNamespace(id=1, roles=["viewer"], is_superuser=False)
    client, _db = _client_with_user(user)
    with patch(_EXPLODE_SVC) as service_cls:
        service_cls.return_value.get_explode.side_effect = PermissionError("hidden")
        resp = client.get("/api/v1/cad-3d/explode/doc-1")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "explode_access_denied"
