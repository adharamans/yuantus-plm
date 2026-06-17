#!/usr/bin/env python3
"""Worker end-to-end evidence smoke for ECM publish.

Validates the LAST production-readiness item for PLM->ECM publish: that ONE enqueued
controlled-file outbox row is drained by the worker all the way to a real Athena
`sent`, with Athena document properties recorded.

  release() -> ECM outbox row -> ecm-publication-worker -> Athena Transfer Receiver
  -> outbox SENT (+ athena_document_id)

This script automates the *drain + verify* half. The operator still prepares a
disposable released version with a controlled file (see the §5 checklist in
docs/DEV_AND_VERIFICATION_ECM_PUBLISH_P1E_LIVE_CLOSEOUT_AND_WORKER_E2E_PLAN_20260617.md)
and passes its outbox id via --outbox-id.

SAFETY:
- Default mode is a DRY RUN: no worker run, no Athena I/O.
- `--yes-live` REQUIRES `--outbox-id`. A worker tick drains the whole DUE batch, not a
  single row, so the script refuses to drain unless the named row is the ONLY due-pending
  target row (no backlog published as a side effect of a smoke).
- The live preflight reads the SAME `get_settings()` the worker uses and refuses to run
  if the kill-switch is off, the target system mismatches, or `resolve_adapter` would
  return the Null adapter (a Null "sent" is never a real publish).
- The transfer secret is never logged.

Run inside a Yuantus deployment that can reach BOTH the database and the live Athena
Transfer Receiver.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, TextIO

# Only PENDING is claimable by the worker's _claim_batch; keep this in sync.
_CLAIMABLE_STATES = {"pending"}
_TERMINAL_STATES = {"sent", "failed", "skipped"}
# Kept byte-identical to AthenaTransferReceiverAdapter._SENT_DISPOSITIONS.
_SENT_DISPOSITIONS = {"CREATED", "RENAMED", "OVERWRITTEN", "UNCHANGED", "SKIPPED"}


@dataclass
class WorkerE2EConfig:
    target_system: str
    base_url: str
    transfer_user: str
    transfer_secret: str
    root_folder_id: str
    publish_enabled: bool
    worker_id: str
    outbox_id: Optional[str]
    max_ticks: int = 6


def _env_value(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        # Settings is env_prefix=YUANTUS_; match that exactly so the script's view
        # cannot diverge from the worker's. (Bare names are NOT accepted.)
        value = env.get(f"YUANTUS_{name}")
        if value and value.strip():
            return value.strip()
    return ""


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def config_from_env(
    env: Mapping[str, str] | None = None,
    *,
    outbox_id: str | None = None,
    worker_id: str | None = None,
    max_ticks: int | None = None,
) -> WorkerE2EConfig:
    source = env or os.environ
    return WorkerE2EConfig(
        target_system=_env_value(source, "PUBLICATION_ECM_TARGET_SYSTEM"),
        base_url=_env_value(source, "PUBLICATION_ECM_BASE_URL", "ATHENA_BASE_URL"),
        transfer_user=_env_value(source, "PUBLICATION_ECM_TRANSFER_USER"),
        transfer_secret=_env_value(source, "PUBLICATION_ECM_TRANSFER_SECRET"),
        root_folder_id=_env_value(source, "PUBLICATION_ECM_ROOT_FOLDER_ID"),
        publish_enabled=_truthy(_env_value(source, "ECM_PUBLISH_ENABLED")),
        worker_id=worker_id or _env_value(source, "PUBLICATION_ECM_WORKER_E2E_WORKER_ID")
        or "ecm-worker-e2e",
        outbox_id=outbox_id or (_env_value(source, "PUBLICATION_ECM_WORKER_E2E_OUTBOX_ID") or None),
        max_ticks=max_ticks if max_ticks is not None else 6,
    )


def missing_live_inputs(config: WorkerE2EConfig) -> list[str]:
    """Env-level required inputs (cheap, no imports)."""
    missing: list[str] = []
    if not config.publish_enabled:
        missing.append("YUANTUS_ECM_PUBLISH_ENABLED=true (kill-switch must be on)")
    if not config.target_system:
        missing.append("YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM")
    if not config.base_url:
        missing.append("YUANTUS_PUBLICATION_ECM_BASE_URL or YUANTUS_ATHENA_BASE_URL")
    if not config.transfer_user:
        missing.append("YUANTUS_PUBLICATION_ECM_TRANSFER_USER")
    if not config.transfer_secret:
        missing.append("YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET")
    if not config.root_folder_id:
        missing.append("YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID")
    return missing


def live_preflight(
    config: WorkerE2EConfig,
    *,
    settings: Any = None,
    resolve_adapter: Callable[..., Any] | None = None,
    null_adapter_cls: Any = None,
) -> list[str]:
    """Settings-level gate read through the SAME path the worker uses, so passing it
    means the worker would really go live (not a Null run / kill-switch-off no-op)."""
    if settings is None:
        from yuantus.config import get_settings

        settings = get_settings()
    if resolve_adapter is None:
        from yuantus.meta_engine.ecm_publication.adapter_registry import resolve_adapter
    if null_adapter_cls is None:
        from yuantus.meta_engine.ecm_publication.adapter import NullEcmPublicationAdapter

        null_adapter_cls = NullEcmPublicationAdapter

    blockers: list[str] = []
    if not bool(getattr(settings, "ECM_PUBLISH_ENABLED", False)):
        blockers.append("get_settings().ECM_PUBLISH_ENABLED is not true (worker kill-switch off)")
    settings_target = (getattr(settings, "PUBLICATION_ECM_TARGET_SYSTEM", "") or "").strip()
    if settings_target != config.target_system:
        blockers.append(
            f"settings PUBLICATION_ECM_TARGET_SYSTEM {settings_target!r} != target {config.target_system!r}"
        )
    try:
        adapter = resolve_adapter(config.target_system, settings=settings)
        if isinstance(adapter, null_adapter_cls):
            blockers.append("resolve_adapter() returns the Null adapter — no real Athena write")
    except Exception as exc:  # adapter resolution failure is itself a blocker
        blockers.append(f"resolve_adapter() raised {type(exc).__name__}")
    return blockers


# --------------------------------------------------------------------------- #
# Default (live) wiring -- imported lazily.
# --------------------------------------------------------------------------- #
def _default_session_scope() -> contextlib.AbstractContextManager:
    from yuantus.database import get_db_session

    return get_db_session()


def _default_worker_factory(config: WorkerE2EConfig):
    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.meta_engine.ecm_publication.worker import EcmPublicationOutboxWorker

    import_all_models()
    return EcmPublicationOutboxWorker(config.worker_id)


def _row_model():
    from yuantus.meta_engine.ecm_publication.models import EcmPublicationOutbox

    return EcmPublicationOutbox


def _worker_claim_set(session: Any, model: Any, *, stale_timeout_seconds: int | None = None) -> set[str]:
    """Rows a worker tick could actually claim. This MIRRORS the worker's
    ``_claim_batch`` selection (pending + due + claimable) across **ALL** target
    systems -- the worker does NOT filter by target_system (it only excludes the
    configured target while the kill-switch is off, which the live preflight already
    forbids). Filtering by our target_system here would miss other-target due rows
    the same tick would drain, breaking the single-row blast-radius guarantee.
    """
    from sqlalchemy import or_

    now = datetime.now(timezone.utc)
    query = (
        session.query(model)
        .filter(model.state == "pending")
        .filter(model.next_attempt_at <= now)
    )
    if stale_timeout_seconds is not None:
        stale_cutoff = now - timedelta(seconds=stale_timeout_seconds)
        query = query.filter(
            or_(model.claimed_at.is_(None), model.claimed_at < stale_cutoff)
        )
    return {r.id for r in query.all()}


def _row_snapshot(row: Any) -> dict:
    props = dict(row.properties or {})
    nat = getattr(row, "next_attempt_at", None)
    return {
        "id": row.id,
        "item_id": row.item_id,
        "version_id": row.version_id,
        "file_id": row.file_id,
        "file_role": row.file_role,
        "target_system": row.target_system,
        "state": row.state,
        "reason": row.reason,
        "attempt_count": row.attempt_count or 0,
        "max_attempts": getattr(row, "max_attempts", None),
        "next_attempt_at": nat.isoformat() if hasattr(nat, "isoformat") else nat,
        "remote_id": props.get("remote_id"),
        "athena_document_id": props.get("athena_document_id"),
        "athena_disposition": props.get("athena_disposition"),
        "conflict_after_sent": bool(props.get("conflict_after_sent")),
    }


def _classify_row(snap: dict) -> dict:
    failures: list[str] = []
    state = snap["state"]
    if state == "sent":
        if snap["conflict_after_sent"]:
            failures.append(
                "properties.conflict_after_sent set — content drifted post-send; "
                "current content was NOT republished"
            )
        if snap["reason"] is not None:
            failures.append(f"reason is {snap['reason']!r}, expected null")
        if (snap["attempt_count"] or 0) < 1:
            failures.append("attempt_count < 1")
        if not snap["remote_id"]:
            failures.append("properties.remote_id missing")
        if not snap["athena_document_id"]:
            failures.append("properties.athena_document_id missing (Null run / not a real publish?)")
        if snap["athena_disposition"] not in _SENT_DISPOSITIONS:
            failures.append(f"athena_disposition {snap['athena_disposition']!r} not in {sorted(_SENT_DISPOSITIONS)}")
        outcome = "passed" if not failures else "failed"
    elif state == "pending" and (snap["attempt_count"] or 0) >= 1:
        outcome = "retrying"
        failures.append(
            f"still pending after attempt {snap['attempt_count']}/{snap['max_attempts']} "
            "(deferred by retry backoff) — rerun or raise --max-ticks; not a hard failure"
        )
    else:
        outcome = "failed"
        failures.append(f"state is {state!r}, expected 'sent'")
    return {**snap, "outcome": outcome, "passed": outcome == "passed", "failures": failures}


def _finish(config: WorkerE2EConfig, rows: list[dict], ticks: int) -> dict:
    outcomes = {r["outcome"] for r in rows}
    if rows and outcomes == {"passed"}:
        status = "passed"
    elif "failed" in outcomes:
        status = "failed"
    elif "retrying" in outcomes:
        status = "inconclusive_retrying"
    else:
        status = "failed"
    return {
        "status": status,
        "target_system": config.target_system,
        "worker_id": config.worker_id,
        "ticks": ticks,
        "rows": rows,
    }


def run_worker_e2e(
    config: WorkerE2EConfig,
    *,
    worker_factory: Callable[[WorkerE2EConfig], Any] = _default_worker_factory,
    session_scope: Callable[[], contextlib.AbstractContextManager] = _default_session_scope,
    row_model: Callable[[], Any] = _row_model,
) -> dict:
    if not config.outbox_id:
        return {"status": "blocked", "message": "live worker E2E requires --outbox-id (one disposable row)"}
    model = row_model()
    oid = config.outbox_id

    with session_scope() as session:
        row = session.get(model, oid)
        snap = _row_snapshot(row) if row is not None else None
    if snap is None:
        return _finish(config, [{"id": oid, "outcome": "failed", "passed": False, "failures": ["row not found"]}], 0)

    # Already terminal (e.g. idempotent re-run of a sent row): validate, no drain.
    if snap["state"] in _TERMINAL_STATES:
        return _finish(config, [_classify_row(snap)], 0)

    if snap["state"] in _CLAIMABLE_STATES:
        # Build the worker first so the blast-radius check can mirror its real claim
        # set (using the worker's own stale-timeout) across ALL target systems -- a
        # tick drains the whole due batch, not just our target.
        worker = worker_factory(config)
        with session_scope() as session:
            claim_set = _worker_claim_set(
                session, model, stale_timeout_seconds=getattr(worker, "stale_timeout_seconds", None)
            )
        if claim_set != {oid}:
            # The worker drains the whole due batch (any target); refuse unless our row
            # is the ONLY thing a tick would claim, so the live write is bounded to it.
            return {
                "status": "blocked",
                "message": (
                    "refusing to drain (blast radius): a worker tick would claim "
                    f"{sorted(claim_set)} (ALL target systems, not just "
                    f"{config.target_system!r}), expected exactly {{{oid!r}}}. Clear the "
                    "backlog before --yes-live."
                ),
            }
        ticks = 0
        while ticks < config.max_ticks:
            with session_scope() as session:
                worker.run_once_with_session(session)
            ticks += 1
            with session_scope() as session:
                row = session.get(model, oid)
            if row is None or row.state not in _CLAIMABLE_STATES:
                break
        with session_scope() as session:
            row = session.get(model, oid)
            final = (
                _classify_row(_row_snapshot(row))
                if row is not None
                else {"id": oid, "outcome": "failed", "passed": False, "failures": ["row not found"]}
            )
        return _finish(config, [final], ticks)

    # Any other non-terminal, non-claimable state (e.g. dry_run_ready): cannot drain.
    return _finish(config, [_classify_row(snap)], 0)


def dry_run_plan(
    config: WorkerE2EConfig,
    *,
    session_scope: Callable[[], contextlib.AbstractContextManager] | None = None,
    row_model: Callable[[], Any] | None = None,
    preflight: Callable[[WorkerE2EConfig], list[str]] | None = None,
) -> dict:
    plan: dict = {
        "status": "dry_run",
        "network_io": False,
        "target_system": config.target_system,
        "worker_id": config.worker_id,
        "outbox_id": config.outbox_id,
        "missing_live_inputs": missing_live_inputs(config),
    }
    if config.outbox_id is None:
        plan.setdefault("missing_live_inputs", []).append("--outbox-id (required for --yes-live)")
    scope = session_scope or _default_session_scope
    model_fn = row_model or _row_model
    try:
        model = model_fn()
        with scope() as session:
            # ALL-target due-pending count == the blast radius a tick would drain.
            plan["due_pending_rows_all_targets"] = len(_worker_claim_set(session, model))
    except Exception as exc:  # no DB reachable in plan mode is fine
        plan["due_pending_rows_all_targets"] = None
        plan["pending_count_error"] = type(exc).__name__
    # Best-effort settings-level preflight (worker would really go live?).
    pf = preflight or live_preflight
    try:
        plan["worker_preflight_blockers"] = pf(config)
    except Exception as exc:
        plan["worker_preflight_blockers"] = None
        plan["preflight_error"] = type(exc).__name__
    return plan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Worker end-to-end evidence smoke for ECM publish (drain + verify one row)."
    )
    parser.add_argument("--yes-live", action="store_true", help="actually drain + verify")
    parser.add_argument("--outbox-id", help="the disposable outbox row id (required for --yes-live)")
    parser.add_argument("--worker-id", help="worker id to claim with")
    parser.add_argument("--max-ticks", type=int, default=None, help="max worker ticks (default 6)")
    return parser


def main(argv: list[str] | None = None, *, out: TextIO | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out = out or sys.stdout
    config = config_from_env(
        outbox_id=args.outbox_id, worker_id=args.worker_id, max_ticks=args.max_ticks
    )
    if not args.yes_live:
        print(json.dumps(dry_run_plan(config), indent=2, sort_keys=True), file=out)
        return 0

    blockers = list(missing_live_inputs(config))
    if not config.outbox_id:
        blockers.append("--outbox-id is required for --yes-live")
    if not blockers:
        try:
            blockers.extend(live_preflight(config))
        except Exception as exc:
            blockers.append(f"preflight failed: {type(exc).__name__}")
    if blockers:
        print(json.dumps({"status": "blocked", "blockers": blockers}, indent=2, sort_keys=True), file=out)
        return 2

    result = run_worker_e2e(config)
    print(json.dumps(result, indent=2, sort_keys=True), file=out)
    if result.get("status") == "passed":
        return 0
    if result.get("status") == "inconclusive_retrying":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
