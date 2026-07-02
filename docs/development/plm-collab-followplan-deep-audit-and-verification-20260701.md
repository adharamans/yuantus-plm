# PLM-collab 跟发计划 — Deep Audit & Verification (2026-07-01)

Type: independent adversarial re-audit of the PLM×MetaSheet integration line. The
five-dimension audit ran at baseline `origin/main = 464cf998` (post `#928` write-back
audit readout, `#930` line dev-verify record, `#931` locked-BOM ECO design doc), then
this record was reconciled against current `origin/main = 4e00e6e0` after the docs-only
`#932` date-obsolete revert decision doc and `#933` locked-BOM ECO ratification. Final
reconciliation is against Yuantus `origin/main = ed809114` after `#934`, `#938`, and
`#939`, plus MetaSheet2 main after `#3469` (`f372cd1f`).

## 0. Result at a glance

A five-dimension **adversarial** audit — each investigator tasked to *disprove* the closeout's
"nothing-ungated-buildable-left" claim — finds **no unfinished, ungated, unowned, buildable-now Yuantus
development item** on this line. Every shipped surface is implemented, wired, guard-gated,
single-Alembic-head, entitlement-lit, tested, and pact-verified; every remaining item is
**explicitly gated** (environment / product-governance / deferred-product / commercial), or
direction-ratified while its implementation phases remain separately gated (`#933`).

**No code was built** — and that is the correct engineering outcome, not an omission. The only candidates
surfaced were either (a) a genuine gap that was *consumer-side* and has since been closed by MetaSheet2
`#3469`, or (b) redundant
tidies that close no correctness/safety/false-green gap. Building (b) would be the "auto-pick more work"
anti-pattern this line explicitly avoids.

**One new finding worth recording** (missed by the feature-focused closeouts): the provider's
optimistic-concurrency machinery (`write_etag` / `If-Match` / 412) is fully shipped and round-trip-locked, but
was **unconsumed by MetaSheet2** at the audit baseline. That would have allowed a same-cell concurrent BOM
write-back to drop the loser's edit while reporting success on the *audited* Phase-7 path. The gap is now
closed on MetaSheet2 main by `#3469` (`f372cd1f`): the consumer threads `write_etag`, sends `If-Match`, and
handles 412 by reloading context and dropping the stale retry key.

## 1. Method

Initial audit baseline `464cf998`; first reconciliation baseline `4e00e6e0` (docs-only
`#932`/`#933`, no provider/consumer runtime surface change); final reconciliation baseline
Yuantus `ed809114` and MetaSheet2 `f372cd1f`. Five parallel investigators,
each read-only over `origin/main` (`git show origin/main:…` / `git grep … origin/main`) plus
`gh` for cross-repo PR state, each instructed to actively hunt for a buildable-now-ungated
item and to reject anything gated, owned/in-flight, or scope-invention. Corroborating
context: at the audit baseline there were no unmerged Yuantus/Athena code PRs in this line.
After the audit, `#934` opened as an owner-authorized date-obsolete DP1 light-path implementation
(`acknowledged -> open` table-local correction only) and has since merged as `423a59a5`.
MetaSheet2 `#3469` closed the consumer If-Match parity gap as `f372cd1f`. MetaSheet2's other
open PRs are separate programs (approval automation, multitable C2 write-through, workflow SSRF
hardening, nightly reports) owned by other lines. Scope note: "跟发计划" here is the PLM×MetaSheet integration line; the parallel
**Athena** Failure-Inventory line is likewise confirmed complete — zero open PRs, and its `#2`–`#5` backlog is
shipped (`#52`/`#54`) or resolved/gated (`#2` observe-only, `#5` first slice `#55`; status synced by `#56`/`#57`).
The dimensions: (1) write-back/projection/embed/#928-audit completeness;
(2) CI anti-false-green / test-wiring integrity; (3) MetaSheet2 consumer parity; (4) re-verification of the
`#924` gated items; (5) code-marker / migration / doc-index hygiene. (Dimension 2 was completed by hand after
its agent stalled.)

## 2. Per-dimension findings

**(1) Write-back / projection / embed / #928 audit — SHIPPED, no gap (high confidence).**
Router wired (`app.py:35` import, `:329` include, prefix `/api/v1`); no unwired `*_router.py`. Projection is
entitlement-first (gate before part lookup → no existence leak) → 404 → non-Part 400 → read-permission 403 →
curated allowlist (never raw `to_dict`). Embed-token (P3-D1): pinned gate → fail-closed 503 without a signing
key, origin-allowlist 403, Ed25519 TTL≤600, jti-audit. Write-back (Phase-7): exact guard ladder
403-SKU → 403-perm → 400-malformed/empty/missing-or-overlong-`Idempotency-Key`/non-scalar → 404-part/line →
409-lock → `If-Match`/412 → atomic `begin_nested` audit-before-mutation; per-tenant
`UNIQUE(tenant_id, idempotency_key)`; 30+ tests incl. 400-before-404, replay-cache, cross-tenant isolation,
audit-rollback. `#928` audit readout: superuser + tenant-scoped, `no-store`, ISO-8601 filters, pagination,
invalid-dt 400, read-only. Both SKUs lit (`bom_multitable` + distinct `bom_multitable_writeback`); `is_entitled`
raises on unknown key (no silent-False). Single Alembic head `bom_writeback_audit_002` (across 53 revisions),
model `__table_args__` ↔ migration in lockstep. Provider pact exercises the real PATCH write endpoint under a
strict no-pending verifier (no false-green). Zero TODO/FIXME/stub/skip in these files.

**(2) CI anti-false-green / test-wiring — INTACT, no genuine hole (verified by hand).**
Every shipped PLM-collab surface test (`test_bom_multitable_writeback/projection/embed_token`,
`test_entitlement_service`, `test_feature_router`, `test_integration_capabilities`, `test_seat_projection`) is
in the contracts `pytest` RUN block. The sort-order meta-test (`test_ci_contracts_ci_yml_test_list_order`)
passes. The authoritative route-count pin (`test_phase4_search_closeout_contracts`, `assert len(app.routes)==733`)
is wired. Deliberately **not** flagged: 561 test files on disk vs 374 referenced in ci.yml — this is the
intentional *curated-contracts* design (the excess run in the regression/e2e/coverage/plugin jobs or are
DB-gated and would red the contracts job); wholesale wiring is the "209 unwired tests" false-alarm trap.
The 3 secondary route-count pins (`…loopback_metrics`, `…route_count_delta`, `…tier_b_3_…portfolio`) are absent
from the contracts list but are **redundant with the wired authoritative pin** — no false-green hole.

**(3) MetaSheet2 consumer parity — original gap found, now closed by `#3469` (high confidence).** See §3.

**(4) `#924` gated items — each genuinely gated (high confidence).** See §4.

**(5) Code-marker / migration / doc-index hygiene — clean (high confidence).**
Markers resolve to intentional guards or externally-gated stubs (`local_storage.py:71` presigned-URL
`NotImplementedError`; artifact-conditional `pytest.skip` that still runs in CI). All three Alembic trees are
single-head with no orphans. All 1265 `DELIVERY_DOC_INDEX` references resolve.

## 3. The integration-parity finding — optimistic concurrency (FOUND, THEN CLOSED)

**Provider (complete):** `bom_multitable_projection_service.py:167` emits `write_etag` on every context line, so
it is already on the wire the consumer receives; `bom_multitable_router.py:319` accepts `If-Match`, guard-step
returns **412** on a stale etag, `:428` sets the response `ETag`; the read↔write etag round-trip was explicitly
locked (`#927`), with `If-Match` (`#917`) and etag hardening (`#922`).

**Consumer (missing at audit baseline):** the ms2 `PLMAdapter.ts` `BomMultitableLine` interface had no
`write_etag`; `updateBomMultitableLine` sent only `Idempotency-Key`, never `If-Match`; the published consumer
pact write interaction carried no `If-Match`. The consumer thin-write-pact design-lock predated the provider
`If-Match` work, and its deferral list did **not** mention optimistic-concurrency.

**Impact at baseline:** on a *same-cell* race, the consumer would report success to the editor whose edit was
silently dropped — a lost-update on the governed, audited Phase-7 write-back. **Scope caveat:** the provider
applies only sent cells (`exclude_unset=True`), so *cross-cell* concurrent edits already merged cleanly; the
exposure was same-cell only.

**Resolution:** MetaSheet2 `#3469` (`f372cd1f`) closed the consumer half. The consumer now carries
`write_etag` through the PLM adapter/workbench path, sends it as `If-Match`, maps provider 412 to a reload
flow, and drops the stale retry key before the next submit. This keeps same-cell conflict handling explicit
without weakening the audited write-back path. No Yuantus provider work remained after `#927`.

## 4. Remaining gated items — each with its exact gate

| # | Item | Gate | What must precede any code |
|---|---|---|---|
| 2 | Ops-activation proof (alerting / owned-HTTPS V1.2 rerun / deploy-environment gate) | **Environment** | Secret `ALERT_WEBHOOK_URL` (ci.yml guard keeps alerting inert until set), an owned domain, and a deploy pipeline (`can-i-deploy` is `--main-branch`, not `--to-environment`). |
| 3 | Phase-7 locked-BOM ECO revision route | **Direction ratified; implementation still gated** | `#933` ratified the direction from `#931`: A3 explicit ECO-path opt-in, B1 reuse ECO approval workflow, C2 pre-emptive `line.state` gating + discriminated-409 fallback. Code remains separately gated: `EcoPermissionAdapter` wiring is repo-wide authz, discriminated-409 is a provider/contract change, and the ECO-scoped `feature_key` / SKU question stays open. |
| 4 | Date-obsolete revert | **DP1 light path shipped; broader lifecycle undo still gated** | Design-first decision doc `#932` landed. The owner authorized only the DP1 (i)/(ii) table-local review-flag correction (`acknowledged -> open`), now shipped by `#934` (`423a59a5`); DP1 (iii) child-lifecycle undo and any broader revert semantics remain deferred/gated. |
| 5 | Phase-6 SSO / identity-session / bridge activation | **Deferred product** | Owner chooses bridge activation / continuous in-iframe UX as the next product line; the one-shot embed handshake is currently sufficient. |
| 6 | Broader commercial ops (vendor issuance / key custody / admin UX / B2 per-SKU seats) | **Commercial / owner** | Deployment + support model and per-SKU seat policy. |
| — | Embed-token `jti` revocation denylist | **Deferred design** | A design decision; TTL≤600 caps exposure today and verify-side lives in the consumer. |

## 5. Candidates hunted and deliberately NOT built (anti-scope-invention record)

- **3 secondary route-count pins into ci.yml** — redundant with the wired authoritative `test_phase4` pin;
  closes no false-green hole. Not built.
- **Stale lifecycle-lock comment** (`bom_multitable_router.py:~393`) — busywork; runtime behavior is
  data-driven off `LifecycleState.version_lock` and already regression-locked
  (`test_bom_multitable_writeback.py:311`). Not built.
- **Wiring the ~187 non-contracts tests** — intentional curated-list design; wholesale wiring would red the
  contracts job (DB-gated / other-job tests). Not done — this is the documented "209 unwired tests" trap.

## 6. Verification

Initial audit baseline `464cf998`, first reconciled after rebase to `4e00e6e0`, then final-reconciled after
Yuantus `#934/#938/#939` and MetaSheet2 `#3469`; read-only over `origin/main` + `gh` cross-repo state.
Checks executed: the sort-order meta-test passes;
every shipped-surface contract test confirmed present in the contracts RUN block; the
authoritative route-count pin confirmed wired; single Alembic head confirmed; all
`DELIVERY_DOC_INDEX` references resolve. **No code changed**, so no new runtime tests are
warranted; this record is docs-only, with the doc-index contracts rerun after rebase.

## 7. Conclusion

All **ungated, unowned, buildable-now** development on the PLM-collab line is **complete and independently
re-verified** at the current baseline. The remainder is genuinely gated (environment / governance / deferred /
commercial) — building it would choose infrastructure, governance, or commercial policy on the owner's behalf.
The one cross-repo loose end surfaced by the audit (optimistic-concurrency consumer adoption) is now shipped
on MetaSheet2 main (`#3469`), and the date-obsolete DP1 light path is shipped on Yuantus main (`#934`). This
record is therefore a **verified closure record**, not a request to invent more ungated work.
