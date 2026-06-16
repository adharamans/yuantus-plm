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

## Gate 2 — kill-switch semantics (DECISION — locked)

**Decision: the worker's dispatch path ALSO honors `ECM_PUBLISH_ENABLED`, re-checked
per tick.** This supersedes #769 §5's deferral of the question.

- **Behavior.** At the top of `EcmPublicationOutboxWorker.run_once_with_session` (before
  reclaim/claim), read `get_settings().ECM_PUBLISH_ENABLED`; if `False`, the tick
  **claims and sends nothing and returns 0**. PENDING rows are left untouched and resume
  when the switch is flipped back on.
- **Where (load-bearing):** the check lives in the **worker**, NOT in `resolve_adapter`.
  Returning a Null adapter when disabled would mark rows **SENT via Null without writing**
  — a silent false-success. The worker must simply **not process** while disabled.
- **Granularity: per-tick** (one cheap settings read per poll) is the locked default — it
  halts the whole drain; exposure is bounded by `poll_interval` + `batch_size`. A per-row
  check before `send` (immediate mid-batch halt) is the stricter alternative; **not
  adopted** unless ops later needs mid-batch immediacy.
- **Rationale.** Once P1D is a real external write, ops intuition reads "turn off
  `ECM_PUBLISH_ENABLED`" as "stop publishing." One toggle must halt **both** enqueue
  (existing, P1B) **and** dispatch (new). Today the switch only gates enqueue, so a running
  worker would keep draining — that surprise must not exist at go-live.
- **Test (cheap, lands with the worker change):** with `ECM_PUBLISH_ENABLED` off,
  `run_once_with_session` returns 0 and **no** row transitions; flip on → normal drain.

This worker change is small and **surface-independent** (it applies whether dispatch
targets Transfer or anything else), so it can land early — before the P1D-retarget build —
if desired.

## Exit criteria

P1D-retarget code starts only when: **Gate 1** R1–R5 are all GO against live Athena, **and**
**Gate 2** is ratified (and optionally already implemented as the small worker change).
Design specifics (the adapter shape, identity folding, the Transfer contract) live in #769.
