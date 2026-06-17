# ECM Publish — P1D Readiness Gate (thin)

Date: 2026-06-16
Status: **GATE — must pass before any P1D-retarget code is written**
Design/contract reference: `DEVELOPMENT_ECM_PUBLISH_P1D_RETARGET_TRANSFER_RECEIVER_TASKBOOK_20260616.md` (#769)

P1D is the step that turns the outbox into a **real external write** to Athena. Two
**runtime semantics** are go/no-go and must be locked *before* the adapter is written —
they are operational, not design niceties. This gate holds the line; it deliberately
contains nothing else.

## Gate 1 — Athena Phase 0 (U1–U5) live readiness (GO / NO-GO)

P1D-retarget **code does not start** until every line is confirmed against the **live**
Athena (Transfer Receiver surface, per #769). Until all are GO, P1D stays default-off and
unwritten.

| # | Precondition | Owner | State |
|---|---|---|---|
| R1 | **Endpoints reachable** from the PLM deployment: base URL + `/api/v1/transfer/receiver/{verify,folders,documents}` | Athena-ops / joint | ☐ |
| R2 | **Credentials**: a `TransferReceiverRegistration` provisioned (authType + user/secret), scoped to the PLM root folder subtree; PLM holds the secret | Athena-ops | ☐ |
| R3 | **Target record-repo schema**: the target folder/tree exists; folder strategy ratified (nested `/PLM/<item>/<version>` vs flat — #769 D8); whether published files must be *declared-as-records* (admin-only) decided | joint | ☐ |
| R4 | **Idempotency key / path rules**: `sourceNodeId` folding (#769 D2) + watermark `sourceLastModifiedAt` (#769 D3) + `conflictPolicy` (#769 D4) confirmed against the receiver's real `(root_folder_id, sourceRepositoryId, sourceNodeId)` mapping + the 4-rule matrix | joint | ☐ |
| R5 | **Failure semantics**: `disposition → SENT` (incl. `UNCHANGED`/`SKIPPED`) and terminal-vs-retryable (403 cred/scope, quota rejection) confirmed against **real** responses (#769 D10) | joint | ☐ |

A line is GO only when verified against the live environment (not from source inference).
These are the live half of #769 §4's U1–U5; this table is the operational checklist with
owners.

## Gate 2 — kill-switch semantics (DECISION — locked; as implemented on `main`)

**Decision: the worker's dispatch path ALSO honors `ECM_PUBLISH_ENABLED`, re-checked
per tick.** This supersedes #769 §5's deferral of the question.

- **Behavior (as implemented in `EcmPublicationOutboxWorker._claim_batch`).** Each tick,
  the worker reads `get_settings()`. If a live target is configured
  (`PUBLICATION_ECM_TARGET_SYSTEM` is set) **and** `ECM_PUBLISH_ENABLED` is `False`, the
  claim query is filtered with `target_system != PUBLICATION_ECM_TARGET_SYSTEM` — i.e. rows
  for the **configured live target are not claimed** (they stay PENDING, untouched, and
  resume when the switch is flipped back on). Rows for **other** target systems and the
  default Null path keep their existing behavior. This is a **target-scoped** halt, not a
  blanket "claim nothing / return 0" — it surgically stops only the live external-write
  target.
- **Where (load-bearing):** the check lives in the **worker** (`_claim_batch`), NOT in
  `resolve_adapter`. Returning a Null adapter when disabled would mark rows **SENT via Null
  without writing** — a silent false-success. The worker must simply **not claim/process**
  the live-target rows while disabled.
- **Granularity: per-tick** (one settings read per claim) — exposure of a switch flip is
  bounded by `poll_interval` + `batch_size`. A per-row check before `send` (immediate
  mid-batch halt) is the stricter alternative; **not adopted** unless ops needs mid-batch
  immediacy.
- **Activation model — RESTART-ONLY, not a hot toggle (operational note).**
  `ECM_PUBLISH_ENABLED` is an env-backed `Settings` field (`settings.py`), and
  `get_settings()` is `@lru_cache(maxsize=1)` — so the worker reads the value **frozen at
  process start**. Flipping the switch therefore means **change the config and restart the
  worker**; a running worker will **not** pick up an env change on the next tick. (An env
  var cannot be changed in-process anyway, so this matches a config-and-restart ops model.)
  If a true **no-restart runtime kill** is later required (flip a value, a running worker
  stops within a tick), it needs a **non-env config source** — e.g. a DB/API-backed flag
  read per tick (or `get_settings.cache_clear()` on a signal) — which is **out of scope
  here**. The Phase-0 runbook MUST state the restart semantics so ops does not expect a
  no-restart flip.
- **Rationale.** Once P1D is a real external write, ops intuition reads "turn off
  `ECM_PUBLISH_ENABLED`" as "stop publishing." One toggle must halt **both** enqueue
  (existing, P1B) **and** dispatch (new) for the live target. Today the switch only gated
  enqueue, so a running worker would keep draining — that surprise must not exist at go-live.
- **Test (present on `main`):** with a configured target + `ECM_PUBLISH_ENABLED` off, the
  configured-target rows are not claimed and make no transition; other-target rows still
  flow; flip on → normal drain.

This worker change is **surface-independent** (it applies whether dispatch targets Transfer
or anything else) and is already merged.

## Exit criteria

The P1D-retarget adapter + Gate-2 worker change are already on `main`; **go-live** (turning
`ECM_PUBLISH_ENABLED` on for the configured live target) is permitted only when: **Gate 1**
R1–R5 are all GO against live Athena, **and** the Phase-0 runbook carries Gate-2's
**restart-only** activation note. Design specifics (the adapter shape, identity folding, the
Transfer contract) live in #769.
