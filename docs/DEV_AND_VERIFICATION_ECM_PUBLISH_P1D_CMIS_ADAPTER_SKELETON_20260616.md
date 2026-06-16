# ECM Publish — P1D: Athena CMIS Adapter (Skeleton) (Dev & Verification)

Date: 2026-06-16
Branch: `feat/ecm-p1d-cmis-adapter-skeleton` (off `main` after #767)
Follows: worker CLI entrypoint #767
**Status: SKELETON — built per "未就绪先处理" (Athena not yet ready). Structurally
complete + fully unit-tested with the network mocked; live validation (Phase 0
U1–U5) is DEFERRED until a live Athena is available.**

## 1. What this delivers

The real `AthenaCmisPublicationAdapter` — the `resolve_adapter` non-Null branch that
turns a worker `send` into an actual Athena CMIS write. It mirrors the proven
`erp_publication.http_adapter` (sync `httpx` + injectable transport, `CircuitBreaker`,
status→reason mapping) and **reuses the existing Athena connection** (`ATHENA_BASE_URL`
/ `ATHENA_SERVICE_TOKEN`) and the Athena failure classifier (`is_athena_breaker_failure`).

- **`ecm_publication/cmis_adapter.py`** — `AthenaCmisPublicationAdapter`:
  - `build_payload` (LOCAL, no network): a CMIS `createDocument` envelope from the flat
    ECM snapshot — repository / object-type / folder path (`<root>/<item>/<version>`) /
    name / the per-file identity / provisional `cmis:` + `plm:` properties + an
    idempotency key (the per-file identity tuple).
  - `validate_contract` (LOCAL): the per-file identity 5-tuple + folder + object type.
  - `send` (the ONLY network call): sync POST under the circuit breaker; a real **2xx**
    → ok (remote_id from `objectId`/`id`/`remote_id`); 5xx / timeout / connection / 429 /
    408 / **401 / 403** / **3xx** / circuit-open → `remote_error` (**retryable** — token
    expiry and proxy redirects are transient, not terminal); genuine payload-contract
    4xx (400/404/409/422) → `validation_error` (terminal). `Idempotency-Key` + bearer
    auth headers.
- **`adapter_registry.resolve_adapter`** — gates on `PUBLICATION_ECM_TARGET_SYSTEM`:
  returns the CMIS adapter only when it is configured, a reachable base URL exists, AND
  the row's `target_system` matches; otherwise **fails closed to Null**. **Default OFF**
  (empty) → Null everywhere in dev/CI.
- **Settings** (`PUBLICATION_ECM_*`, all default off/empty): `TARGET_SYSTEM` (the gate),
  `BASE_URL` + `SERVICE_TOKEN` (optional overrides — fall back to `ATHENA_BASE_URL` /
  `ATHENA_SERVICE_TOKEN`), `PATH`, `REPOSITORY_ID`, `ROOT_FOLDER_PATH`, `OBJECT_TYPE_ID`,
  `TIMEOUT_SECONDS`.

## 2. What is PROVISIONAL / deferred to Phase 0 (U1–U5)

This is a skeleton; the following are explicitly **not** validated and will be adjusted
against a live Athena:

- The **CMIS wire mapping** in `build_payload`: the browser-binding vs AtomPub choice,
  the exact `createDocument` field/property names, the folder-creation/ensure semantics,
  and the content-stream upload (this skeleton sends a JSON envelope referencing the file
  by id + fingerprint; the real content-stream transfer is a Phase-0 item).
- **OAuth client-credentials**: the skeleton authenticates with the static
  `ATHENA_SERVICE_TOKEN` bearer. Wiring the existing async `AthenaClient` token flow
  (+ shared breaker) is a live-bring-up step.
- The `remote_id` extraction keys (`objectId`/`id`) are provisional.

A Phase-0 checklist (U1–U5) should at minimum confirm: U1 auth + repository reachable;
U2 folder ensure/create; U3 createDocument + content stream; U4 the status→reason mapping
against real Athena responses; U5 idempotent re-delivery (Athena dedupes on the key).

## 3. Safety

- **No real write by default**: `resolve_adapter` returns Null unless
  `PUBLICATION_ECM_TARGET_SYSTEM` is set, so dev/CI/default never POSTs to Athena.
- `build_payload` / `validate_contract` are LOCAL — dry-run never reaches the network.
- `send` is the only network call, under the circuit breaker; retryable vs terminal
  classification matches the worker's reschedule rule (`remote_error`/`adapter_error`
  retry; `validation_error` terminal).
- **Forward flag carried from #767 (decide here):** `ECM_PUBLISH_ENABLED` gates the
  *enqueue* hook, not the worker. Once this adapter is switched on (real writes), a
  running worker will keep draining the backlog to Athena even if an operator flips
  `ECM_PUBLISH_ENABLED` off. Decide before go-live whether the worker (or
  `resolve_adapter`) should also honor the kill-switch per tick, vs. "stop publishing =
  stop the worker". Recommended: have the drain path check the kill-switch so one toggle
  halts both enqueue and dispatch — to be finalized with the Phase-0 runbook.
- **Forward flag — auth-error classification (Phase 0 decision):** `send` currently maps
  **both** 401 and 403 to `remote_error` (retryable), but the Athena breaker predicate
  (`is_athena_breaker_failure`) does **not** count 401/403, so a persistently
  wrong/expired token would retry every poll up to `max_attempts` **without** tripping
  the breaker to dampen the storm. Also, 401 (expired token) is cleanly retryable *with
  OAuth refresh* (the deferred auth path), whereas 403 (forbidden = principal lacks
  permission) is usually terminal and retrying won't help. Confirm against real Athena:
  (a) whether 403 should be terminal (`validation_error`) rather than retryable; and
  (b) whether the breaker predicate should count 401/403 so retryable auth failures also
  open the circuit. To be finalized with the OAuth wiring in Phase 0.

## 4. Verification

Test env: `.venv-wp13` (python3.11); `unset YUANTUS_PYTEST_DB YUANTUS_TEST_DB PYTEST_DB`.

`test_ecm_cmis_adapter.py` (31, all pass) — **all HTTP mocked via `httpx.MockTransport`;
never touches a real Athena**: local `build_payload` (identity + folder + fingerprint)
and `validate_contract` (ok + missing fields); 2xx → ok with `remote_id` (from each of
`objectId`/`id`/`remote_id`); 2xx-no-body → idempotency-key fallback; contract 4xx
(400/404/409/422) → `validation_error`; 5xx + 408/429 + **401/403** → `remote_error`;
**3xx → not-success/remote_error**; timeout → `remote_error`; circuit-open →
`remote_error`; on-wire `Idempotency-Key` + bearer auth; the `PUBLICATION_ECM_*` override
beats the `ATHENA_*` fallback; the Athena breaker failure predicate; and the resolver
(unconfigured → Null, configured-match → CMIS, other-target → Null, **no-base-url → Null**).

### 4.1 Adversarial verify (2 reviewers) — bugs caught + fixed

The 20 initial green tests missed two real reliability bugs and a silent-config trap,
all now fixed + regression-tested:

- **401/403 dead-letter** (P2): auth failures were mapped to terminal `validation_error`,
  so an expired/rotated bearer/OAuth token (the *expected* steady-state failure)
  permanently dead-lettered the row — un-retried by the worker and un-replayable (409).
  Fix: 401/403 → `remote_error` (retryable). *(The erp http_adapter has the identical
  mapping; an erp parity fix is a noted follow-up, out of this slice's scope.)*
- **Resolver not fail-closed** (P2): the gate dropped erp's base-URL guard, so
  `TARGET_SYSTEM` set with no base URL returned a live adapter pointing at
  `http://athena.invalid` → every publish `ConnectError` → `remote_error` → the
  at-least-once outbox churned forever. Fix: require a reachable base URL → else Null.
- **Silent-drop config** (P2): `PUBLICATION_ECM_BASE_URL` / `_SERVICE_TOKEN` were read
  but never declared on `Settings` (`extra=ignore` → env override silently dropped). Fix:
  declared both; a test pins that the override beats the `ATHENA_*` fallback.
- **3xx false-SENT** (P3): an un-followed redirect was treated as success. Fix: 3xx →
  remote_error (+ a real-2xx guard).
- **CI-isolation invariant** (P3): the worker's Null-by-default test now asserts
  `resolve_adapter` is Null *before* running, so a stray `YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM`
  fails loudly instead of opening a live socket.

Registered in `ci.yml` (sorted, before `test_ecm_publication_adapter.py`) + `conftest.py`
`_ALLOWLIST_NO_DB`. **No routes, no tables** → route-count pins (712) and migration
coverage unaffected. The existing ECM worker/adapter/router/enqueue suite stays green
(the worker still resolves Null by default).

## 5. Boundary / next

This is the last *code* step before a real publish. The remaining work is **Phase 0
live validation (U1–U5)** against a live Athena (env/credentials/controlled-record-repo),
which adjusts the provisional CMIS mapping and wires OAuth — to be scheduled when the
Athena environment is ready.
