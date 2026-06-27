# L2 — Forensic transition-history `?reason_code` filter (impl + verification)

> Task: backlog line 2 (lifecycle/audit) — the deferred sibling of the `?outcome` filter (#879). Branch `claude/forensic-reason-code-filter` · base `origin/main`.

## What
Add a repeatable `?reason_code` query param to the superuser forensic route `GET /api/v1/transition-history/forensic/{item_id}` (alongside the `?outcome` filter merged in #879). `reason_code` is the low-cardinality discriminator stored in `LifecycleTransitionHistory.properties` JSON (`permission_denied`, `condition_failed`, `assembly_release_blocked`, `*_aborted`, …) — letting ops triage failed attempts by cause, e.g. `?reason_code=permission_denied&reason_code=condition_failed`.

## Design decision (owner: B — accept-any)
**No whitelist, no 400 for unknown reason_codes** — an unknown value simply yields an empty result set. Rationale: the reason_code vocabulary is 11 string literals scattered in `lifecycle/service.py` with no single source of truth; a router-side whitelist would silently 400 legitimate queries whenever a new code is added to the service but forgotten in the router list. Accept-any is robust for a forensic-triage tool. (The `?outcome` filter keeps its whitelist because its vocabulary is the fixed 5-value outcome enum.)

## How
- **Service** (`lifecycle/service.py` `get_transition_history`): new `reason_codes: Optional[Sequence[str]] = None`. SQL-level JSON filter `LifecycleTransitionHistory.properties["reason_code"].as_string().in_(tuple(reason_codes))` — a portable WHERE clause (PostgreSQL JSONB `->>` / SQLite `JSON_EXTRACT`) that composes correctly with `order_by` + `limit` (filter → order → limit). Pattern proven in-codebase (`Item.properties`, `EcmPublicationOutbox.properties`).
- **Router** (`lifecycle_transition_history_router.py`): repeatable `reason_code: Optional[List[str]] = Query(None)`, passed as `reason_codes=`. **No new route** (route count unchanged at 723); item-scoped read still `success_only=True`.

## Verification (local)
`PYTHONPATH=worktree/src YUANTUS_PYTEST_DB=1 pytest` — router test **24 passed** (existing + 6 new): single reason_code; multiple (repeatable); no-filter returns all; composes-with-limit (filter before order/limit); **unknown reason_code → empty 200, NOT 400**; + the doc-index completeness/sorting/references contracts. No route-count pin change; tests added to the already-CI-registered `test_lifecycle_transition_history_router.py` (no new ci.yml/detect_changes wiring needed — #879 already covers the file + the lifecycle detect_changes case).

## Follow-up
- If a whitelist is later wanted, extract a single shared `reason_code` constant module so router + service stay synchronized (avoids the drift trap).
