"""Worker E2E evidence smoke (scripts/ecm_publish_phase0/worker_e2e_smoke.py).

Drives the REAL worker (run_once_with_session) against an in-memory DB with a fake
Athena adapter, so the script's drain + assert + blast-radius logic is validated
without a live Athena or a live Yuantus DB. Covers the false-pass / false-fail /
isolation branches an adversarial review flagged.
"""
from __future__ import annotations

import contextlib
import importlib.util
import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.ecm_publication.adapter import (
    EcmPublicationAdapter,
    NullEcmPublicationAdapter,
    SendResult,
    ValidationResult,
)
from yuantus.meta_engine.ecm_publication.models import EcmPublicationOutbox
from yuantus.meta_engine.ecm_publication.service import EcmPublicationOutboxService
from yuantus.meta_engine.ecm_publication.worker import EcmPublicationOutboxWorker
from yuantus.meta_engine.models.file import FileContainer
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.version.models import ItemVersion, VersionFile
from yuantus.models import user as _user  # noqa: F401
from yuantus.models.base import Base

import_all_models()

_PAST = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _load_script():
    root = __import__("pathlib").Path(__file__).resolve()
    for _ in range(12):
        if (root / "pyproject.toml").is_file() and (root / "scripts").is_dir():
            break
        root = root.parent
    path = root / "scripts" / "ecm_publish_phase0" / "worker_e2e_smoke.py"
    spec = importlib.util.spec_from_file_location("worker_e2e_smoke", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


smoke = _load_script()


# --- fake adapters -----------------------------------------------------------
class _PassAdapter(NullEcmPublicationAdapter):
    def send(self, payload):
        return SendResult(
            ok=True, remote_id="athena-doc-1",
            properties={"athena_document_id": "athena-doc-1", "athena_disposition": "CREATED"},
        )


class _ValidateFailAdapter(NullEcmPublicationAdapter):
    def validate_contract(self, payload):
        return ValidationResult(ok=False, errors=["bad payload"])


class _RemoteErrorAdapter(NullEcmPublicationAdapter):
    def send(self, payload):
        return SendResult(ok=False, error="boom", error_kind="remote_error")


class _FakeRealAdapter(EcmPublicationAdapter):
    """A non-Null adapter for preflight tests (the live gate blocks Null subclasses)."""

    def build_payload(self, snapshot):
        return {}

    def validate_contract(self, payload):
        return ValidationResult(ok=True)

    def send(self, payload):
        return SendResult(ok=True, remote_id="x")


@pytest.fixture()
def scope():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def _scope():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    return _scope


def _seed_pending_row(scope, *, target="athena") -> str:
    with scope() as s:
        iid = f"P-{uuid.uuid4().hex[:8]}"
        s.add(Item(id=iid, item_type_id="Part", config_id=f"c-{iid}", generation=1, is_current=True))
        s.flush()
        v = ItemVersion(
            id=f"v-{uuid.uuid4().hex}", item_id=iid, generation=1, revision="A",
            version_label="1.A", state="Released", is_current=True, is_released=True,
        )
        s.add(v)
        s.flush()
        fc = FileContainer(
            id=f"fc-{uuid.uuid4().hex}", filename="x.step", system_path="/v/x",
            mime_type="model/step", file_size=10, cad_format="STEP", checksum="c1",
        )
        s.add(fc)
        s.flush()
        s.add(VersionFile(id=f"vf-{uuid.uuid4().hex}", version_id=v.id, file_id=fc.id, file_role="native_cad"))
        s.flush()
        (row,) = EcmPublicationOutboxService(s).enqueue_release(v, user_id=1)
        row.target_system = target
        row.next_attempt_at = _PAST
        s.commit()
        return row.id


def _insert_row(scope, *, state, reason=None, attempt_count=1, properties=None, target="athena") -> str:
    with scope() as s:
        r = EcmPublicationOutbox(
            id=uuid.uuid4().hex, item_id="P", version_id="v",
            file_id=f"f-{uuid.uuid4().hex[:8]}", file_role="native_cad", target_system=target,
            state=state, reason=reason, payload_fingerprint="fp", attempt_count=attempt_count,
            max_attempts=3, next_attempt_at=_PAST, properties=properties,
        )
        s.add(r)
        s.commit()
        return r.id


def _wf(adapter, backoff=0):
    return lambda config: EcmPublicationOutboxWorker(config.worker_id, adapter=adapter, backoff_seconds=backoff)


def _config(outbox_id, *, max_ticks=None):
    return smoke.config_from_env(
        {"YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM": "athena"}, outbox_id=outbox_id, max_ticks=max_ticks
    )


def _run(scope, adapter, outbox_id, *, backoff=0, max_ticks=None):
    return smoke.run_worker_e2e(
        _config(outbox_id, max_ticks=max_ticks),
        worker_factory=_wf(adapter, backoff), session_scope=scope,
        row_model=lambda: EcmPublicationOutbox,
    )


# --- happy path + hard fail --------------------------------------------------
def test_pass_when_row_reaches_sent_with_athena_props(scope):
    rid = _seed_pending_row(scope)
    result = _run(scope, _PassAdapter(), rid)
    assert result["status"] == "passed"
    (row,) = result["rows"]
    assert row["id"] == rid and row["state"] == "sent" and row["outcome"] == "passed"
    assert row["athena_document_id"] == "athena-doc-1"


def test_fail_when_validation_terminal(scope):
    rid = _seed_pending_row(scope)
    result = _run(scope, _ValidateFailAdapter(), rid)
    assert result["status"] == "failed"
    assert result["rows"][0]["state"] == "failed"


# --- live-safety: isolation + required outbox-id -----------------------------
def test_requires_outbox_id(scope):
    result = smoke.run_worker_e2e(
        smoke.config_from_env({"YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM": "athena"}),
        worker_factory=_wf(_PassAdapter()), session_scope=scope,
        row_model=lambda: EcmPublicationOutbox,
    )
    assert result["status"] == "blocked" and "outbox-id" in result["message"]


def test_blast_radius_blocks_when_backlog_present(scope):
    rid = _seed_pending_row(scope)
    _seed_pending_row(scope)  # unrelated due-pending backlog row (same target)
    result = _run(scope, _PassAdapter(), rid)
    assert result["status"] == "blocked" and "blast radius" in result["message"]


def test_blast_radius_blocks_on_other_target_due_row(scope):
    # REGRESSION: the worker's _claim_batch does NOT filter by target_system, so a due
    # pending row of a DIFFERENT target is in the tick's claim set. The precheck must
    # see it and block -- and neither row may be processed.
    rid = _seed_pending_row(scope, target="athena")
    other = _insert_row(scope, state="pending", target="other")  # different target, due
    result = _run(scope, _PassAdapter(), rid)
    assert result["status"] == "blocked" and "ALL target systems" in result["message"]
    with scope() as s:
        assert s.get(EcmPublicationOutbox, rid).state == "pending"
        assert s.get(EcmPublicationOutbox, other).state == "pending"


# --- already-sent / false-pass guards ----------------------------------------
def test_already_sent_passes_without_draining(scope):
    rid = _insert_row(
        scope, state="sent", reason=None,
        properties={"remote_id": "athena-doc-9", "athena_document_id": "athena-doc-9", "athena_disposition": "CREATED"},
    )
    result = _run(scope, _PassAdapter(), rid)
    assert result["status"] == "passed" and result["ticks"] == 0


def test_already_sent_without_athena_props_fails(scope):
    # A Null-published / older 'sent' row has remote_id but no athena_document_id.
    rid = _insert_row(scope, state="sent", properties={"remote_id": "null:abc"})
    result = _run(scope, _PassAdapter(), rid)
    assert result["status"] == "failed"
    assert any("athena_document_id missing" in f for f in result["rows"][0]["failures"])


def test_conflict_after_sent_fails(scope):
    rid = _insert_row(
        scope, state="sent",
        properties={"remote_id": "athena-doc-9", "athena_document_id": "athena-doc-9",
                    "athena_disposition": "CREATED", "conflict_after_sent": True},
    )
    result = _run(scope, _PassAdapter(), rid)
    assert result["status"] == "failed"
    assert any("conflict_after_sent" in f for f in result["rows"][0]["failures"])


def test_skipped_row_fails_not_passes(scope):
    rid = _insert_row(scope, state="skipped", reason="not_eligible")
    result = _run(scope, _PassAdapter(), rid)
    assert result["status"] == "failed"
    assert result["rows"][0]["outcome"] == "failed"


# --- retrying (backoff) is inconclusive, not a hard fail ---------------------
def test_remote_error_with_backoff_is_inconclusive_retrying(scope):
    rid = _seed_pending_row(scope)
    result = _run(scope, _RemoteErrorAdapter(), rid, backoff=30, max_ticks=2)
    assert result["status"] == "inconclusive_retrying"
    row = result["rows"][0]
    assert row["state"] == "pending" and row["outcome"] == "retrying"
    assert row["attempt_count"] >= 1


# --- preflight (settings-level gate) -----------------------------------------
def _preflight(*, enabled, settings_target, adapter):
    config = smoke.config_from_env({"YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM": "athena"}, outbox_id="x")
    settings = SimpleNamespace(ECM_PUBLISH_ENABLED=enabled, PUBLICATION_ECM_TARGET_SYSTEM=settings_target)
    return smoke.live_preflight(
        config, settings=settings,
        resolve_adapter=lambda target, settings=None: adapter,
        null_adapter_cls=NullEcmPublicationAdapter,
    )


def test_preflight_clean_when_settings_go_live():
    assert _preflight(enabled=True, settings_target="athena", adapter=_FakeRealAdapter()) == []


def test_preflight_blocks_killswitch_off():
    bl = _preflight(enabled=False, settings_target="athena", adapter=_FakeRealAdapter())
    assert any("ECM_PUBLISH_ENABLED" in b for b in bl)


def test_preflight_blocks_target_mismatch():
    bl = _preflight(enabled=True, settings_target="other", adapter=_FakeRealAdapter())
    assert any("PUBLICATION_ECM_TARGET_SYSTEM" in b for b in bl)


def test_preflight_blocks_null_adapter():
    bl = _preflight(enabled=True, settings_target="athena", adapter=NullEcmPublicationAdapter())
    assert any("Null adapter" in b for b in bl)


# --- env-name discipline (bare names not accepted) ---------------------------
def test_bare_env_names_not_accepted():
    # The worker reads only YUANTUS_-prefixed env; the script must match.
    config = smoke.config_from_env({"ECM_PUBLISH_ENABLED": "true"})
    assert config.publish_enabled is False  # bare name ignored


def test_missing_inputs_empty_when_all_present():
    env = {
        "YUANTUS_ECM_PUBLISH_ENABLED": "true",
        "YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM": "athena",
        "YUANTUS_PUBLICATION_ECM_BASE_URL": "http://athena",
        "YUANTUS_PUBLICATION_ECM_TRANSFER_USER": "svc",
        "YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET": "secret",
        "YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID": "00000000-0000-0000-0000-000000000111",
    }
    assert smoke.missing_live_inputs(smoke.config_from_env(env)) == []


# --- dry run + main ----------------------------------------------------------
def test_dry_run_counts_due_without_draining(scope):
    _seed_pending_row(scope)
    plan = smoke.dry_run_plan(
        _config(None), session_scope=scope, row_model=lambda: EcmPublicationOutbox,
        preflight=lambda c: ["stub-preflight-blocker"],
    )
    assert plan["status"] == "dry_run" and plan["network_io"] is False
    assert plan["due_pending_rows_all_targets"] == 1
    assert plan["worker_preflight_blockers"] == ["stub-preflight-blocker"]
    with scope() as s:
        assert [r.state for r in s.query(EcmPublicationOutbox).all()] == ["pending"]


def test_main_yes_live_blocks_on_missing_inputs(monkeypatch, capsys):
    monkeypatch.setattr(smoke.os, "environ", {}, raising=False)
    rc = smoke.main(["--yes-live", "--outbox-id", "00000000-0000-0000-0000-0000000000aa"])
    assert rc == 2
    assert '"status": "blocked"' in capsys.readouterr().out
