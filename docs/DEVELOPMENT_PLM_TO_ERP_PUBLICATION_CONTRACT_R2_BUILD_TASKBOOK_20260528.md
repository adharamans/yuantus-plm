# Claude Taskbook: PLM→ERP Publication Contract — R2 Build (Adapter / Outbox)

Date: 2026-05-28

Type: **Doc-only taskbook (build plan).** It ratifies the six preconditions the
R2 scope-lock (#666) left open (its §10), resolves decision **D-R2-1** and the two
items #666 deferred, and pins the concrete adapter-interface shape, the outbox
persistence model, the state machine + transitions, the version-scoped
idempotency key, and the publication snapshot — each **grounded against real
code**, so the R2 implementation slice has a settled plan. It changes no code.
**Merging this taskbook does NOT authorize the R2 implementation** — that
requires its own explicit opt-in.

Parents: `DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_PLAN_20260527.md` (#663),
`..._R1A_TASKBOOK_20260527.md` (#664), the merged R1-B `publication-readiness`
API (#665), and the merged R2 scope-lock
`..._R2_ADAPTER_OUTBOX_TASKBOOK_20260528.md` (#666). Baseline `main = d027a504`.

## 0. What this taskbook is (and is not)

The R2 scope-lock (#666) **locked** the boundaries (adapter-interface-only, no
real connector / Odoo / GPL-AGPL / external write; durable outbox; dry-run with
no external side effect; version-scoped idempotency key; state ≠ reason; a
publication snapshot at enqueue; `/publication/export` OUT). It deliberately left
**six preconditions** (its §10) plus D-R2-1 and two deferred items to be settled
**before** implementation. This taskbook settles them. It does **not** re-open any
#666 lock (those are inherited verbatim — see §10), and it writes **no code**.

Per the program discipline, every decision below is presented as
**ratify-with-recommendation** (the same flow #666 used for D-R2-1): the
recommendation is grounded, but it is the reviewer's call to ratify.

## 1. Grounding (read against `main = d027a504`)

All decisions below cite code read this cycle:

- `document_sync/models.py` — `SyncJobState` (`:44`: pending/running/completed/
  failed/cancelled) vs `SyncRecordOutcome` (`:58`: synced/skipped/conflict/error);
  `SyncJob` (`:108`) / `SyncRecord` (`:161`); `SyncRecord.document_id` is
  `index=True` **not unique** (`:173`); enums are `String(30)` columns with
  `default=Enum.VALUE.value`; `properties` is `JSON().with_variant(JSONB,
  "postgresql")` (`:146`). **No** version_id/target_system/publication_kind
  columns, **no** reason column, **no** composite unique, **no** per-row snapshot
  column anywhere on the sync tables.
- `services/job_service.py` — durable queue/worker idiom: `create_job` (`:20`,
  in-flight dedupe → returns existing), `poll_next_job` (`:84`, `FOR UPDATE SKIP
  LOCKED` on PG + SQLite fallback), `complete_job` (`:125`), `fail_job(...,
  retry=True)` (`:186`, retry/backoff → re-PENDING or terminal FAILED),
  `requeue_stale_jobs` (`:308`).
- `models/job.py:50` — `ConversionJob.dedupe_key` is `String(120)` `index=True`
  **not unique**; `parallel_tasks_service.py` `enqueue_sync` (`:456`) computes a
  `payload_hash` (`:487`) but the in-flight dedupe-hit branch (`:498-527`) only
  records the new request hash and returns the existing job — it does **not**
  compare payload hash before reusing the row. A resubmit with the same key but
  different content can silently return the original. R2 must not replicate this
  gap.
- `services/scheduler_service.py` — `SchedulerRunResult` splits `enqueued` vs
  `would_enqueue` (`:38`/`:41`): the repo's built-in dry-run idiom (dry-run is a
  distinct result bucket, not a side-effecting call).
- `storage/storage_interface.py:10` (`StorageProvider(ABC)`) and
  `operations/base.py:9` (`BaseOperation(ABC)`) — the repo's interface idiom is
  **ABC + `@abstractmethod`**; there is **no** `typing.Protocol` interface in
  `src`. `services/eco_permission_adapter.py:38` (`EcoPermissionAdapter`) shows
  that a `*Adapter` suffix in this repo means a **concrete delegator**, not an
  abstract interface.
- `cad_connectors/registry.py:16` (registry/priority selection) and
  `services/search_service.py:112` (`engine = "elasticsearch" if self.client else
  "db"`, same-shape fallback) — the multi-backend / configured-else-fallback
  idioms.
- `integrations/dedup_vision.py:91` (`DedupVisionClient`: settings-driven
  base-url, `CircuitBreaker`, primary→fallback) — the **real external client**
  idiom, which belongs to the later real-connector taskbook, **not** R2.
- `web/plm_erp_publication_router.py` — `PublicationReadinessResponse` (`:103`)
  and nested models; `eligible` formula (`:239`–`244`); esign predicate
  (`:189`–`194`). R2 consumes this verdict; it does not re-derive it.

## 2. Adapter interface shape (ratifies #666 §10.1)

**Recommendation (to ratify):** the abstract seam is an **ABC** (matching the
repo idiom; no `Protocol` exists in `src`):

```python
class ErpPublicationAdapter(ABC):
    @abstractmethod
    def build_payload(self, snapshot: dict) -> dict: ...
    @abstractmethod
    def validate_contract(self, payload: dict) -> ValidationResult: ...
    @abstractmethod
    def send(self, payload: dict) -> SendResult: ...
```

- **Naming decision (ratify):** keep `ErpPublicationAdapter` to honor #666 §2.1's
  locked vocabulary ("adapter interface"). NOTE the deliberate, documented
  departure: in this repo `*Adapter` otherwise denotes a *concrete* delegator
  (`eco_permission_adapter.py:38`); here it is the **abstract base**, and its
  docstring must say so. (Alternative considered: rename the abstract base to
  `ErpPublicationProvider` to match `StorageProvider` and reserve `*Adapter` for
  concrete impls. Either is defensible; the recommendation keeps #666's word and
  documents the exception rather than silently renaming a locked term.)
- **`dry_run` is NOT an adapter method — this refines #666 §2's literal
  "`send`/`dry_run`".** Dry-run is an **outbox-service operation** that calls only
  `build_payload` + `validate_contract` and never `send`. This makes #666 §3
  (dry-run produces no external side effect) **structurally** true — the only
  external-write entry point (`send`) is never reached on the dry-run path —
  mirroring `scheduler_service.py`'s `would_enqueue` vs `enqueue` split.
- The concrete real-ERP implementation (e.g. `OdooPublicationAdapter(
  ErpPublicationAdapter)`, an HTTP client in the `DedupVisionClient` mold) is a
  **later, separate taskbook** (#666 §9). R2 ships only the ABC plus a
  `NullErpPublicationAdapter` (see §4).

## 3. Outbox persistence (ratifies #666 §10.6 — the grounded decision)

**Recommendation (to ratify): a dedicated `meta_erp_publication_outbox` table
(`ErpPublicationOutbox`), single-level (one row per idempotency key), modeled on
the `document_sync` *pattern* but NOT extending `meta_sync_jobs` /
`meta_sync_records`.** #666 §2.2 asks to model on the precedent "where
reasonable"; the column-level evidence shows reuse is *not* reasonable — four
required features have no fitting columns on the shared sync tables and reuse
would mean abusing a shared enum + adding columns + a new composite unique to an
unrelated domain:

1. **State enum divergence** — `SyncJobState` (`models.py:44`) is
   pending/running/completed/failed/cancelled; the outbox needs
   pending/dry_run_ready/sent/failed/skipped (#666 §6). Three are absent; three
   are irrelevant.
2. **No reason column** — #666 §6 mandates a reason field orthogonal to state;
   `SyncRecord` has only the single `outcome` enum (`models.py:58,180`).
3. **No version-scoped idempotency** — #666 §5 needs unique `(item_id,
   version_id, target_system, publication_kind)`; `SyncRecord` has only
   `document_id` (`index`, not unique, `:173`) and there is no composite unique
   anywhere.
4. **No per-row snapshot store** — #666 §7 needs a structured per-item snapshot;
   `SyncRecord` has only checksums + `Text` detail fields (no JSONB).

**What is reused (the pattern, not the tables):** String-UUID PK; the
state-vs-outcome orthogonality #666 §6 already cites; `String(30)` enum columns
with `default=Enum.VALUE.value`; a JSONB `snapshot` bag; `created_by_id` FK to
`rbac_users`; audit `created_at`; `error_message: Text`. Enqueue / poll /
complete / fail / retry semantics follow `JobService` (`job_service.py:20/84/125/
186/308`), including `FOR UPDATE SKIP LOCKED` with the SQLite fallback.

**Single-level, not two-level:** a publication targets one `(item, version,
target_system, publication_kind)` — exactly the idempotency key — so one row per
key. (`document_sync`'s two-level job→record shape exists to batch many documents;
R2 publishes per item-version. Publishing many items = many rows.)

**Proposed columns** (`meta_erp_publication_outbox`):

| column | type | note |
|---|---|---|
| `id` | `String` PK | uuid |
| `item_id` | `String` (index) | |
| `version_id` | `String` | part of the unique key (see §6) |
| `target_system` | `String(120)` | which ERP target |
| `publication_kind` | `String(60)`, default `"readiness"` | payload kind |
| `state` | `String(30)`, default `"pending"` | §5 |
| `reason` | `String(30)`, nullable | §5 — orthogonal to state |
| `snapshot` | JSONB | §7 |
| `payload_fingerprint` | `String(128)` | content hash for the drift guard (§6) |
| `attempt_count` | `Integer`, default 0 | retry bookkeeping |
| `max_attempts` | `Integer`, default 3 | per `JobService` |
| `replay_of` | `String`, nullable | lineage (cf. `parallel_tasks_service.py:1178`) |
| `error_message` | `Text`, nullable | |
| `dispatched_at` | `DateTime(tz)`, nullable | when `send` was attempted |
| `properties` | JSONB, nullable | extensibility bag |
| `created_at` | `DateTime(tz)`, server_default now | |
| `created_by_id` | `Integer` FK `rbac_users.id`, nullable | |
| | `UniqueConstraint(item_id, version_id, target_system, publication_kind)` | §6 |

## 4. `sent` reachability without a real connector (resolves deferred item 1)

R2 binds to **no** real connector and performs **no** external write (#666 §2.1,
§9), yet the state machine includes `sent` (#666 §6) and D-R2-1 re-validates
"for `sent` transitions" (#666 §4).

**Recommendation (to ratify): `sent` is reachable in R2 ONLY via an in-repo
`NullErpPublicationAdapter` (and test fakes), which perform no external I/O.**
`send()` on the Null adapter records the dispatch **locally** (no network, no
external write — honoring §9) and returns success, so the full state machine +
process loop are exercised end-to-end in R2's tests. With no adapter configured,
rows terminate at `dry_run_ready`. **Production `sent` against a real ERP requires
the later real-connector taskbook** — `sent` via the Null adapter explicitly does
**not** mean a real ERP received anything, and the row records which adapter ran
(the `search_service.py:112` "which backend ran" idiom).

Alternative considered: reserve `sent` entirely for the connector slice (R2 stops
at `dry_run_ready`). Rejected as the recommendation because it leaves the `send`
path and the `sent`/`failed` transitions untested until the connector lands;
the Null-adapter path gives R2 a fully-exercised, regression-guarded state
machine while still writing nothing external.

## 5. State machine + reasons (inherits #666 §6; pins transitions)

States (#666 §6, locked): `pending`, `dry_run_ready`, `sent`, `failed`,
`skipped`. Reasons (#666 §6, locked, separate column): `not_eligible`,
`adapter_error`, `remote_error`, `validation_error`.

**Transition table (to ratify)**, modeled on `document_sync`'s `_JOB_TRANSITIONS`
map (`document_sync/service.py:41`) and `transition_job_state` validator (`:457`):

| from | to | trigger | reason on arrival |
|---|---|---|---|
| `pending` | `dry_run_ready` | dry-run: `build_payload`+`validate_contract` ok | — |
| `pending` | `sent` | `adapter.send` ok (Null adapter in R2) | — |
| `pending` | `skipped` | ineligible at enqueue/revalidate | `not_eligible` |
| `pending` | `failed` | build/validate/send error | `validation_error` \| `adapter_error` \| `remote_error` |
| `dry_run_ready` | `sent` | promote dry-run → send (revalidate first, §8) | — |
| `dry_run_ready` | `skipped` | revalidate flips ineligible | `not_eligible` |
| `dry_run_ready` | `failed` | send error on promotion | `adapter_error` \| `remote_error` |
| `failed` | `pending` | replay/retry — **only** if reason ∈ {`remote_error`, `adapter_error`} | cleared |
| `skipped` | `pending` | replay — only if revalidate now eligible | cleared |
| `sent` | — | terminal | — |

Retry semantics (#666 §6, locked): retry `remote_error`/`adapter_error`; **never**
`not_eligible`/`validation_error`. Bookkeeping (`attempt_count`/`max_attempts`)
follows `job_service.py:186`.

## 6. Idempotency key + duplicate behavior (inherits #666 §5; resolves deferred item 2)

Key (#666 §5, locked, version-scoped): `(item_id, version_id, target_system,
publication_kind)`.

**Recommendation (to ratify):**

1. **Enforce a real DB `UniqueConstraint`** on the four-tuple (§3 table). This
   closes the `document_sync` gap where `dedupe_key` is `index`-only, not unique
   (`models/job.py:50`).
2. **Primary behavior: reuse-existing-row, never conflict-fail.** A duplicate
   enqueue returns the existing row; it never creates a second row. This matches
   the repo idempotency idiom (`job_service.py:20` returns the existing in-flight
   job).
3. **Content-drift guard** — store a `payload_fingerprint` (content hash of the
   snapshot) and **compare it on a duplicate enqueue** (the step
   `enqueue_sync` skips in the dedupe-hit branch at
   `parallel_tasks_service.py:498-527`, silently dropping new content):
   - fingerprint matches → return existing row (pure idempotency);
   - fingerprint differs **and** row is non-terminal (`pending` / `dry_run_ready`
     / `failed`) → **re-snapshot in place** (latest enqueue wins; recorded in
     audit / `properties`);
   - fingerprint differs **and** row is already `sent` → **conflict** (reject;
     do not silently supersede an already-published version). This single
     supersede-a-published-version case is the **sole** exception to "never
     conflict-fail."
4. **Versionless items (refines #666 §7):** the key requires a concrete
   `version_id`. An item with no current released version is ineligible and is
   reported `skipped` / `not_eligible` synchronously; because there is no version
   to scope the key, no outbox row is persisted for it. This **refines** #666 §7's
   "an ineligible item enqueued resolves to state `skipped` [row]": ineligible
   items that *do* have a version (e.g. not-latest-released) get a persisted
   `skipped` row as #666 §7 states; the no-version case is the one where a
   version-scoped row cannot be formed, so the verdict is surfaced without a row.

## 7. Publication snapshot (inherits #666 §7; pins the 1:1 mapping)

The snapshot JSONB captured at enqueue maps **1:1** to the merged R1-B
`PublicationReadinessResponse` (`plm_erp_publication_router.py:103`), so replay /
audit never drift:

```
snapshot = {
  eligible,                       # bool — independent of blocking_reasons (note 4)
  blocking_reasons: [{reason, detail}],
  ruleset_id, limits: {mbom_limit, routing_limit, baseline_limit},
  item: {item_id, lifecycle_state},
  version: {version_id, generation, revision, version_label, state,
            is_current, is_released, released_at, primary_file_id} | null,
  file_refs: [{file_id, file_role, is_primary, sequence, snapshot_path}],
  summary: {ok, resources, ok_resources, error_count, warning_count},
  esign: {present, is_complete, completed_at},
  generated_at, captured_at,
}
```

**Snapshot fidelity notes (grounded — carry into implementation):**

1. `version.released_at` is stored as an **ISO string** (the router emits
   `released_at.isoformat()`), though the underlying column is a `datetime`.
2. `file_refs[].is_primary` is a non-nullable `bool` (coerced via `bool()`).
3. `esign.is_complete` is **`None`** when the manifest is absent **or** the dict
   lacks the `is_complete` key — distinct from `False`. Missing-manifest does
   **not** block (R1-A §3); the snapshot preserves this and the revalidate path
   must not change it into a new semantic.
4. `eligible` is computed **independently** of `blocking_reasons` (router
   `:239`–`244`): `summary.ok == false` with zero per-resource errors yields
   `eligible = false` but possibly an empty `blocking_reasons`. **Never derive
   `eligible` from an empty `blocking_reasons` list.**
5. `version` is `null` when the item has no current version; `file_refs` is then
   empty.

## 8. Replay eligibility revalidation — D-R2-1 (ratifies #666 §10.2 / §4)

**Recommendation (to ratify): re-validate eligibility against current PLM state
for `sent` transitions; use the snapshot as-enqueued for dry-run and audit** (the
#666 §4 recommendation). On a `sent` transition (including `dry_run_ready` →
`sent` and `failed`/`skipped` → `pending` replays), R2 re-checks eligibility; if
it flips to ineligible, the row goes to `skipped` / `not_eligible` and is never
`sent`.

**The revalidate path MUST reuse R1-B's exact eligibility logic — not a copy**
(#666 §8: "R2 does not re-implement eligibility"). It calls the same response
builder the `publication-readiness` router uses
(`plm_erp_publication_router.py`), so the formula (`:239`–`244`) and the esign
predicate (`:189`–`194`, incl. missing-manifest-does-not-block) stay identical
and cannot drift. The snapshot (§7) is the *as-enqueued* verdict, preserved for
deterministic audit; the revalidate result is the *current* verdict gating a real
send.

## 9. Implementation surface (the plan — NOT built here)

When the R2 implementation is separately opted in, it delivers (suggested module
`src/yuantus/meta_engine/erp_publication/`):

1. `models.py` — `ErpPublicationOutbox` (§3) + `ErpPublicationState` /
   `ErpPublicationReason` (`String(30)` value enums).
2. An Alembic migration creating `meta_erp_publication_outbox` + the composite
   unique (§6).
3. `adapter.py` — `ErpPublicationAdapter(ABC)` (§2) + `NullErpPublicationAdapter`
   (§4).
4. `service.py` — `ErpPublicationOutboxService(session)` with
   `enqueue` / `dry_run` / `process` (send) / `replay`, modeled on `JobService`
   verbs; consumes the R1-B verdict via the shared response builder (§8).
5. Unit tests (state machine, idempotency reuse + drift guard, dry-run no-send,
   revalidate-on-`sent`, Null-adapter `sent`, snapshot fidelity).

**HTTP routes are deferred to a separate thin slice.** The outbox model +
service + adapter land first (no route-count change); the enqueue / dry-run /
replay / status routes are their own opt-in so the `len(app.routes)` pins move in
an isolated change governed by the full-tree residual-scan discipline. (No route
count is touched by this doc-only taskbook.)

## 10. Inherited from #666 — NOT re-litigated here

State machine states; the reason set + retry rule; the version-scoped idempotency
key *shape*; the snapshot field *set*; dry-run-no-external-side-effect; state ≠
reason orthogonality (modeled on `document_sync/models.py:44,58`); R1-B linkage
(consume `eligible` + `blocking_reasons`, do not re-derive); and the non-goals
(no real connector, no Odoo runtime, no GPL/AGPL reuse, no external write from
R2, no purchase/sale transaction, `/publication/export` OUT). This taskbook
*ratifies the shapes/behaviors* #666 left open; it does not change #666's locks.

## 11. Preconditions to enter the R2 IMPLEMENTATION taskbook

1. §2 adapter shape + the `Adapter`-naming decision + `dry_run`-in-service
   refinement ratified;
2. §3 persistence (dedicated `meta_erp_publication_outbox`, single-level)
   ratified;
3. §4 `sent`-reachability-via-Null-adapter ratified;
4. §5 transition table ratified;
5. §6 duplicate-key behavior (reuse-row + DB unique + drift guard + sole
   sent-supersede conflict) ratified;
6. §7 snapshot 1:1 mapping + fidelity notes ratified;
7. §8 D-R2-1 (revalidate-on-`sent`, reusing R1-B logic) ratified.

A **separate explicit opt-in** then authorizes the implementation (model +
migration + adapter + service + tests); HTTP routes and the real ERP connector
are each further separate opt-ins.

## 12. Reviewer Focus

1. §2 — is keeping `ErpPublicationAdapter` (abstract base, documented departure)
   the right call, or rename to `Provider`? Is `dry_run`-as-service-op (refining
   #666 §2) acceptable?
2. §3 — ratify dedicated table over extending `document_sync` (4 grounded
   mismatches); single-level grain.
3. §4 — ratify `sent` reachable only via the Null adapter in R2 (vs reserving
   `sent`).
4. §5 — ratify the transition table + retry rule.
5. §6 — ratify reuse-row + DB unique + drift guard, with the sole
   sent-supersede conflict; ratify the versionless-item carve-out.
6. §7 — confirm the snapshot maps 1:1 to R1-B and the 5 fidelity notes hold.
7. §8 — ratify revalidate-on-`sent` reusing R1-B's exact logic (no re-impl).
8. §9 — confirm routes are deferred so route-count stays isolated.

## 13. Status

Doc-only build plan. Ready for review once the doc exists at the canonical path;
`DELIVERY_DOC_INDEX.md` references it + its DEV/verification record (sorted under
`## Development & Verification`); doc-index / sorting / completeness checks pass;
`git diff --check` clean. Ratifying §2–§8 sets the R2 implementation plan; **a
separate explicit opt-in authorizes the implementation.** The HTTP route slice,
the real ERP connector, and `/publication/export` remain later, separately-opted
slices.
