"""Behavioral tests for the design-loopback ECO route (#601 §3.1 R1).

The route is a thin HTTP seam over the merged service method
`BreakageIncidentService.create_breakage_design_loopback_eco`
(shipped by #596 `6e4ce54`). The service method's behavior is
covered by `test_breakage_design_loopback_eco_creation_wiring.py`;
these tests cover the *route*'s HTTP semantics — request schema,
status-code mapping, response shape, and dependency wiring — by
mocking the service method itself.

AST pins the route handler does not call `ECOService.create_eco`
directly; only the service method is allowed to bridge to ECO
creation.
"""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.web import parallel_tasks_breakage_router as router_module
from yuantus.meta_engine.web.parallel_tasks_breakage_router import (
    parallel_tasks_breakage_router,
)


def _current_user(user_id: int = 42) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        tenant_id="tenant-1",
        org_id="org-1",
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.test",
        roles=["operator"],
        is_superuser=False,
    )


def _client(user: CurrentUser | None = None):
    user = user or _current_user()
    mock_db = MagicMock()

    def override_get_db():
        yield mock_db

    app = FastAPI()
    app.include_router(parallel_tasks_breakage_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), mock_db


def _service_result(*, incident_id: str, eco_id: str, reference: str, created: bool):
    """Mock the BreakageDesignLoopbackEcoCreation shape the service returns."""
    return SimpleNamespace(
        incident_id=incident_id,
        eco=SimpleNamespace(id=eco_id),
        reference=reference,
        created=created,
        preparation=SimpleNamespace(eligible=True),
    )


# --------------------------------------------------------------------------
# 200 + created=True
# --------------------------------------------------------------------------


def test_route_returns_200_created_true_for_new_eco():
    client, _ = _client(_current_user(user_id=42))
    service_method = MagicMock(
        return_value=_service_result(
            incident_id="brk-1",
            eco_id="eco-9",
            reference="abc123",
            created=True,
        )
    )

    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        service_method,
    ):
        response = client.post(
            "/api/v1/breakages/brk-1/design-loopback/eco",
            json={"allow_duplicate": False},
        )

    assert response.status_code == 200
    assert response.json() == {
        "incident_id": "brk-1",
        "eco_id": "eco-9",
        "reference": "abc123",
        "created": True,
        "operator_id": 42,
    }
    # The service is called with positional incident_id + keyword
    # arguments. user_id comes from the auth dependency.
    call = service_method.call_args
    assert call.args[0] == "brk-1"
    assert call.kwargs == {"user_id": 42, "allow_duplicate": False}


# --------------------------------------------------------------------------
# 200 + created=False (dedupe hit)
# --------------------------------------------------------------------------


def test_route_returns_200_created_false_for_dedupe_hit():
    """Per taskbook §3.1 author-recommended response shape: a
    dedupe hit returns 200 + ``created: false`` rather than 409.
    The caller's intent ("get me the ECO for this breakage") is
    satisfied either way; 409 is reserved for genuine semantic
    errors (incident not eligible)."""

    client, _ = _client()
    service_method = MagicMock(
        return_value=_service_result(
            incident_id="brk-1",
            eco_id="eco-9",
            reference="abc123",
            created=False,
        )
    )

    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        service_method,
    ):
        response = client.post(
            "/api/v1/breakages/brk-1/design-loopback/eco",
            json={},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is False
    assert body["eco_id"] == "eco-9"
    assert body["reference"] == "abc123"


# --------------------------------------------------------------------------
# 404 — incident not found
# --------------------------------------------------------------------------


def test_route_maps_not_found_value_error_to_404():
    """`BreakageIncidentService.create_breakage_design_loopback_eco`
    raises ``ValueError("Breakage incident not found: <id>")`` when
    `session.get(BreakageIncident, id)` returns None (verified at
    `parallel_tasks_service.py:4220–4222`). Route maps to 404 with
    the `breakage_not_found` discriminator code.
    """

    client, _ = _client()
    service_method = MagicMock(
        side_effect=ValueError("Breakage incident not found: brk-missing")
    )

    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        service_method,
    ):
        response = client.post(
            "/api/v1/breakages/brk-missing/design-loopback/eco",
            json={"allow_duplicate": False},
        )

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "breakage_not_found"
    assert "brk-missing" in detail["message"]
    assert detail["context"] == {"incident_id": "brk-missing"}


# --------------------------------------------------------------------------
# 409 — incident not eligible
# --------------------------------------------------------------------------


def test_route_maps_ineligible_value_error_to_409():
    """Per the merged closeout contract §3.1 RATIFIED policy,
    `eligible_statuses = {"resolved", "closed"}` — open/in_progress/
    unknown are NOT eligible. The service method's
    `prepare_breakage_design_loopback_intake` returns an ineligible
    preparation in that case, which `create_breakage_design_loopback_eco`
    converts to ``ValueError`` with the prefix "breakage status ...
    is not eligible ...". Route maps to 409 with the
    `breakage_not_eligible_for_loopback` discriminator code so the
    caller can distinguish from a true 404.
    """

    client, _ = _client()
    service_method = MagicMock(
        side_effect=ValueError(
            "breakage status 'open' is not eligible for design loopback"
        )
    )

    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        service_method,
    ):
        response = client.post(
            "/api/v1/breakages/brk-open/design-loopback/eco",
            json={"allow_duplicate": False},
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "breakage_not_eligible_for_loopback"
    assert "not eligible" in detail["message"]
    assert detail["context"] == {"incident_id": "brk-open"}


# --------------------------------------------------------------------------
# allow_duplicate=True forwards to service
# --------------------------------------------------------------------------


def test_allow_duplicate_true_is_forwarded_to_service():
    """The `allow_duplicate` flag must reach the service method verbatim.
    A typo like hardcoded `False` or a missing forward would silently
    break the operator-driven duplicate path (taskbook §3.1 spec)."""

    client, _ = _client()
    service_method = MagicMock(
        return_value=_service_result(
            incident_id="brk-1",
            eco_id="eco-new",
            reference="ref-new",
            created=True,
        )
    )

    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        service_method,
    ):
        response = client.post(
            "/api/v1/breakages/brk-1/design-loopback/eco",
            json={"allow_duplicate": True},
        )

    assert response.status_code == 200
    assert service_method.call_args.kwargs["allow_duplicate"] is True


def test_allow_duplicate_defaults_to_false_when_omitted():
    """If the request body omits `allow_duplicate`, the field must
    default to False (matches `BreakageDesignLoopbackEcoCreateRequest`
    Pydantic default). Empty body `{}` must NOT be rejected as 422.
    """

    client, _ = _client()
    service_method = MagicMock(
        return_value=_service_result(
            incident_id="brk-1",
            eco_id="eco-1",
            reference="ref-1",
            created=True,
        )
    )

    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        service_method,
    ):
        response = client.post(
            "/api/v1/breakages/brk-1/design-loopback/eco",
            json={},
        )

    assert response.status_code == 200
    assert service_method.call_args.kwargs["allow_duplicate"] is False


# --------------------------------------------------------------------------
# DB session: commit on success, rollback on error
# --------------------------------------------------------------------------


def test_db_commit_on_success_rollback_on_value_error():
    """Caller owns the transaction boundary (taskbook §3.1). On
    success: route commits. On `ValueError` (any flavor): route
    rolls back before raising HTTPException.
    """

    # Success path → commit.
    client_ok, db_ok = _client()
    service_ok = MagicMock(
        return_value=_service_result(
            incident_id="brk-1",
            eco_id="eco-1",
            reference="ref-1",
            created=True,
        )
    )
    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        service_ok,
    ):
        client_ok.post(
            "/api/v1/breakages/brk-1/design-loopback/eco", json={}
        )
    assert db_ok.commit.called
    assert not db_ok.rollback.called

    # Failure path → rollback.
    client_err, db_err = _client()
    service_err = MagicMock(side_effect=ValueError("Breakage incident not found: x"))
    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        service_err,
    ):
        client_err.post(
            "/api/v1/breakages/brk-missing/design-loopback/eco", json={}
        )
    assert not db_err.commit.called
    assert db_err.rollback.called


def test_db_rollback_on_non_value_error_propagated():
    """Advisor pre-commit defense-in-depth: any non-`ValueError`
    exception path (e.g., `HTTPException` from
    `ECOService.create_eco`'s permission check, an unrelated
    runtime error) must ALSO roll back the session before
    re-raising. The route must not let the original exception's
    status code (e.g., 403 for permission failure) leak through
    while leaving the session in a partially-flushed state.

    Today `ECOService.create_eco` checks permission at
    `eco_service.py:520` BEFORE any `session.add` / `flush` (lines
    534-535), so this rollback is structurally redundant for the
    happy path. The test pins it for a hypothetical future
    reorder.
    """

    from fastapi import HTTPException

    # Simulate ECOService permission denial as the merged service
    # would surface it (HTTPException is the framework's permission-
    # failure carrier).
    client_perm, db_perm = _client()
    perm_failure = HTTPException(
        status_code=403,
        detail={"code": "eco_create_denied", "message": "Forbidden"},
    )
    service_perm = MagicMock(side_effect=perm_failure)
    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        service_perm,
    ):
        response = client_perm.post(
            "/api/v1/breakages/brk-1/design-loopback/eco",
            json={"allow_duplicate": False},
        )
    # Rollback called; commit NOT called.
    assert not db_perm.commit.called
    assert db_perm.rollback.called
    # The original 403 status reaches the caller verbatim — the
    # route did not translate it into the breakage-local 4xx codes.
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "eco_create_denied"

    # Same for an unrelated runtime exception (defensive — the
    # route's `except Exception` block must catch anything that
    # isn't a ValueError, roll back, and re-raise so the framework
    # surfaces a 500.
    client_rt, db_rt = _client()
    service_rt = MagicMock(side_effect=RuntimeError("unrelated bug"))
    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        service_rt,
    ):
        # TestClient surfaces RuntimeError as a 500 via the default
        # ServerErrorMiddleware; we just need to verify rollback.
        try:
            client_rt.post(
                "/api/v1/breakages/brk-1/design-loopback/eco",
                json={"allow_duplicate": False},
            )
        except RuntimeError:
            pass
    assert not db_rt.commit.called
    assert db_rt.rollback.called


# --------------------------------------------------------------------------
# AST guard: route handler does NOT call ECOService.create_eco directly
# --------------------------------------------------------------------------


def test_route_handler_does_not_call_eco_service_directly():
    """Per #601 taskbook §3.1 + the established session pattern
    [[feedback-runtime-pr-semantics]]: the route handler is a thin
    HTTP seam over the merged service method. It must NOT call
    `ECOService.create_eco` directly — only
    `BreakageIncidentService.create_breakage_design_loopback_eco`
    is allowed to bridge to ECO creation, because that method owns
    the dedupe + caller-owned transaction semantics.

    AST `Call`-walk: no `ECOService(...)` instantiation, no
    `.create_eco(...)` call anywhere in the route handler body.
    """

    handler = router_module.create_breakage_design_loopback_eco
    src = inspect.getsource(handler)
    tree = ast.parse(__import__("textwrap").dedent(src))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            # Block `ECOService(...)` instantiation.
            if isinstance(fn, ast.Name):
                assert fn.id != "ECOService", (
                    "route handler must not instantiate ECOService directly; "
                    "delegate via BreakageIncidentService.create_breakage_design_loopback_eco"
                )
            # Block `<anything>.create_eco(...)` attribute call.
            if isinstance(fn, ast.Attribute):
                assert fn.attr != "create_eco", (
                    "route handler must not call create_eco() directly; "
                    "delegate via BreakageIncidentService.create_breakage_design_loopback_eco"
                )

    # Positive: the delegated service method IS referenced in source.
    assert "create_breakage_design_loopback_eco" in src
    assert "BreakageIncidentService" in src
