"""PLM->ECM (Athena controlled-record) publication outbox — ECM-P1B.

A durable, release-triggered outbox: when a version is released, the entitled,
controlled files are enqueued (one row per file) for later async publication to
Athena (the ECM controlled-record repo, via the Transfer Receiver). Modeled on the proven `erp_publication` outbox
(state-vs-reason split, content fingerprint, retry/worker fields), with two ECM
specifics: a per-file idempotency key, and **conflict-as-audit** (a changed
fingerprint vs an already-SENT row is recorded as a CONFLICT/SKIPPED row, never
raised — the call site is `release()`, which must never fail).

P1B is the enqueue path only: model + `enqueue_release` + the `begin_nested` hook
inside `VersionService.release()` + an exception-safe entitlement gate. The worker /
manual routes / Null+real adapters are P1C/P1D. See
`docs/DEVELOPMENT_ECM_PUBLISH_P0_REFRESH_TASKBOOK_20260616.md`.
"""
