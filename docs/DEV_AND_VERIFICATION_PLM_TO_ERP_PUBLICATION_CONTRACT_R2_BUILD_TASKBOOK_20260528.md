# DEV & Verification: PLM→ERP Publication Contract — R2 Build Taskbook

Date: 2026-05-28

Records the doc-only delivery of
`DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R2_BUILD_TASKBOOK_20260528.md`
— the build plan that ratifies the R2 scope-lock's (#666) six open preconditions,
resolves D-R2-1 and the two deferred items, and pins the adapter interface, the
outbox model, the state machine, the idempotency key, and the snapshot — all
grounded against real code. Doc-only: no code; merging it does **not** authorize
the R2 implementation. Baseline `main = d027a504` (after R2 scope-lock #666).

## 1. What changed

- New build taskbook (adapter shape; dedicated outbox table; state machine +
  transitions; version-scoped idempotency key + duplicate behavior; snapshot 1:1
  mapping; D-R2-1 resolution).
- This DEV/verification record.
- Two sorted `DELIVERY_DOC_INDEX.md` entries (under `## Development &
  Verification`).

## 2. Grounding (against `main = d027a504`)

Decisions are grounded against code read this cycle (cited in the taskbook §1):

- **Persistence = dedicated `meta_erp_publication_outbox`** (not extending
  `document_sync`) is forced by four column-level mismatches on
  `meta_sync_jobs`/`meta_sync_records`: state-enum divergence
  (`document_sync/models.py:44`), no reason column (`:58`/`:180`), no
  version-scoped composite unique (`document_id` is `index`-only `:173`), and no
  per-row snapshot store. The reused *pattern* (String-UUID PK, `String(30)`
  value enums, JSONB bag, `created_by_id`, state-vs-outcome split) and the
  worker semantics (`job_service.py:20/84/125/186/308`, `FOR UPDATE SKIP LOCKED`)
  are document_sync/JobService precedents — a grounded decision, not assumed.
- **Adapter = ABC** matches the repo idiom (`storage_interface.py:10`,
  `operations/base.py:9`; no `Protocol` in `src`); `*Adapter` as a concrete
  delegator (`eco_permission_adapter.py:38`) is the documented-departure note.
- **`dry_run` as a service op** (build+validate, never send) mirrors
  `scheduler_service.py:38/41` (`would_enqueue` vs `enqueue`) — structurally
  enforcing #666 §3.
- **Duplicate = reuse-row + DB unique + fingerprint drift guard** closes the
  real gap in `parallel_tasks_service.py:487/498-527` (payload hash computed but
  not compared on a dedupe hit) and `models/job.py:50` (`dedupe_key` index-only,
  not unique).
- **Snapshot maps 1:1** to the merged R1-B `PublicationReadinessResponse`
  (`plm_erp_publication_router.py:103`); the revalidate path reuses R1-B's exact
  eligibility logic (`:239`–`244`) + esign predicate (`:189`–`194`), not a copy
  (#666 §8).

## 3. Decisions ratified (summary)

Adapter `ErpPublicationAdapter(ABC)` (`build_payload`/`validate_contract`/`send`;
dry-run is a service op — refines #666 §2); dedicated single-level
`meta_erp_publication_outbox`; transition table modeled on
`document_sync` `_JOB_TRANSITIONS` (`document_sync/service.py:41`) +
`transition_job_state` (`:457`); version-scoped idempotency key enforced by a
DB unique, reuse-existing-row, never conflict-fail except the sole
supersede-an-already-`sent`-version case; `sent` reachable in R2 only via an
in-repo `NullErpPublicationAdapter` (no external I/O); D-R2-1 = revalidate for
`sent`, snapshot for dry-run/audit; HTTP routes deferred to a separate thin
slice (route-count stays isolated).

## 4. Verification (this doc-only PR)

- doc-contract pytests — delivery-doc-index references; `## Development &
  Verification` sorting + completeness (every `DEV_AND_VERIFICATION_*.md`
  indexed); doc-index sorting — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass
  (unchanged; no client/helper change).
- `git diff --check` clean.

## 5. Status

Doc-only build plan. Ratifying §2–§8 of the taskbook sets the R2 implementation
plan; the R2 implementation (then the HTTP route slice, then the real ERP
connector and `/publication/export`) each need their own explicit opt-in.
