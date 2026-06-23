# DEV & VERIFICATION — PLM-Collab pact-broker auto-gate (Phase A wiring)

**Date:** 2026-06-22
**Companion to:** the ratified design `plm-collab-pact-broker-autogate-design-20260621.md` (§8
owner-ratified) and `plm-collab-v1-pact-boundary-and-staging-checklist-20260621.md` §3 (which named the
manual-sync drift gap this closes).

This records the **Phase A (advisory) wiring** of the pact-broker auto-gate and — the important part —
the **verification that cannot run yet** (no broker exists), captured as an activation runbook for when
ops provisions it. Read §2 first: a green CI here does **not** mean the integration works.

## 1. What shipped

| Piece | PR | State |
|---|---|---|
| Design decision doc (§8 ratified: PactFlow; GitHub repo secrets, least-privilege scoped-write; advisory→blocking) | #843 | **MERGED** (`1d1031c4`) |
| Yuantus **provider** — advisory broker verify+publish+can-i-deploy CI step + `scripts/ci/pact_broker_provider_verify.py` | #854 | **DRAFT**, CI-green at `61e8fa61`; held for PactFlow/secrets + one real broker run |
| MetaSheet2 **consumer** — advisory `pact-broker publish` step after `test:contract` | zensgit/metasheet2#3065 | **DRAFT**, CI-green at `262c6b89`; held for PactFlow/secrets + one real broker run |

Both build PRs are **strictly additive**: the committed-pact verifier (`test_pact_provider_yuantus_plm`)
and the consumer `test:contract` remain the **live** gates, unchanged. `scripts/sync_metasheet2_pact.sh`
is retained as the local/manual fallback.

## 2. The honest verification boundary (read this)

**These PRs are NOT verified, and a green run does NOT mean the broker integration works.** With no
PactFlow account or secrets yet:

- every broker step maps the secret to step-level `env` and a **shell guard** at the top of `run` skips
  (exit 0) when it's empty — a clean no-op until ops provisions the secret, then auto-activates. It
  deliberately does **not** use a step `if:`: the `secrets` context is unavailable to `steps.if`, which
  would skip even after the secret is set. A green CI today = "the guarded step skipped," not "the broker
  works" — the same vacuous-green trap, stated plainly so it can't read as "done."
- the only real verification today is the **local** gates (committed pact + `test:contract`), which are
  untouched and live.

So Phase A is **reviewable wiring that activates on provisioning**, not a verified gate. The two build
PRs do not have the same activation shape: the MetaSheet2 consumer publish can only prove its
`mainBranch` broker path after it merges and the `push: main` workflow runs; the Yuantus provider PR can
be verified pre-merge only after that consumer main-branch publish exists (§5).

## 3. What IS verified before broker provisioning

- `.github/workflows/ci.yml` and `yuantus-pact-consumer.yml` are valid YAML.
- Yuantus #854 current head `61e8fa61` is CI-green. Its `contracts` job executes
  `test_ci_contracts_pact_provider_gate` (**6 passed** locally), pinning:
  - shell-guard skip when `PACT_BROKER_BASE_URL` is absent;
  - configured-but-missing-token failure;
  - token redaction;
  - provider `YuantusPLM`;
  - broker consumer selector `{"mainBranch": true}`;
  - `pact-ruby-standalone` install plus `pact/bin` PATH wiring for the
    `pact-provider-verifier` / `pact-broker` CLIs.
- MetaSheet2#3065 current head `262c6b89` is CI-green. Its `yuantus-pact-consumer` workflow
  now executes `scripts/ops/yuantus-pact-consumer-broker-workflow-contract.test.mjs`, pinning
  local `test:contract` before publish, env+shell guards instead of `steps.if`, SHA + broker
  `--branch` semantics, and no legacy `--tag`.
- the local committed-pact verifier remains the live provider gate in Yuantus CI; the additive
  broker step has not replaced it.
- the wiring matches the ratified design: provider uses a scoped-**write** token to *publish verification
  results* (not read-only); consumer a scoped-**write** token to *publish the pact*; both use the broker's
  first-class `--branch` (not legacy tags); `can-i-deploy` is informational in Phase A.

## 4. Ops prerequisite (the gating item — yours, not mine)

Nothing works until ops provisions:
1. the **PactFlow** account / instance;
2. **two least-privilege scoped-WRITE tokens** — MetaSheet2 (publish consumer pact) and Yuantus
   (publish its own provider verification results); `can-i-deploy` queries can use a read-only token;
3. GitHub Actions **secrets** in BOTH repos: `PACT_BROKER_BASE_URL`, `PACT_BROKER_TOKEN`. Never in-repo.

## 5. Activation + verification runbook (run AFTER provisioning — this is the verification)

1. **Consumer publish first** — merge/activate MetaSheet2#3065 only after ops has provisioned
   `PACT_BROKER_BASE_URL` + `PACT_BROKER_TOKEN`. The pull-request run can validate the workflow shape,
   but it publishes the PR ref (`GITHUB_REF_NAME`), not `main`; it therefore cannot satisfy a Yuantus
   `mainBranch` selector. GitHub only exposes `workflow_dispatch` after the
   workflow-dispatch-enabled workflow exists on the default branch, so pre-merge activation should use a
   `pull_request` rerun or a small branch push after secrets are set, not a manual dispatch expectation.
   The real verification is the post-merge `push: main` run: the "Publish consumer pact" step should run
   (not skip) and the pact should appear in PactFlow as
   `Metasheet2@<sha>` on branch `main`. *If it errors:* confirm the `pact-broker publish` flags +
   auth against the PactFlow CLI docs.
2. **Provider verify+publish second** — only after step 1 has produced the consumer `mainBranch` pact,
   re-run Yuantus#854 / the `contracts` workflow. The "Pact broker verify…" step should run,
   `pact-provider-verifier` should verify the broker pact against the seeded provider and publish
   results as `YuantusPLM@<sha>`. On the PR run its provider branch is the PR ref; after merge, the
   `push: main` run records branch `main`. *If it errors:* confirm the `pact-provider-verifier` flags +
   broker auth — the script's CLI invocation is **best-effort and must be confirmed here**.
3. **can-i-deploy** — `can-i-deploy --pacticipant YuantusPLM --version <sha>` should return a matrix
   answer (not an auth/empty error), proving the published `Metasheet2` pact and `YuantusPLM`
   verification results populate the matrix.
4. **Drift catch** — deliberately break a pinned field on one side; confirm the broker verification goes
   **red** (advisory) — proving the gate would catch the drift that the §3 boundary doc flagged.
5. **Phase B flip** — only after a stable window of green advisory runs **and** owner sign-off: remove
   `continue-on-error` / make `can-i-deploy` gating (blocking). Until then it stays advisory.

## 6. Known best-effort / confirm-at-activation

- The exact `pact-provider-verifier` + `pact-broker publish` flags and the PactFlow auth model are
  written from documented patterns but **not run against a live broker**. The CLI install/PATH wiring is
  pinned by tests, but still only becomes runtime evidence when the guarded broker steps execute with
  real secrets.
  §5.1–5.2 are where they get confirmed/fixed.
- pact-python 3.2.1's native broker API was **deliberately avoided** (the CLI is used instead) to reduce
  version-specific risk on the provider side.

## 7. Scope / non-goals (unchanged)

- The broker covers the existing 32 interactions + future ones (incl. V1.2's embed-token pact).
- **Not** here: Phase B blocking, broker webhooks, SSO / write-back / approval-automation, T2 — separate
  owner-gated lines.

---

*PRs: #843 (design, merged `1d1031c4`), #854 (Yuantus provider, draft),
zensgit/metasheet2#3065 (consumer, draft).
Local fallback retained: `scripts/sync_metasheet2_pact.sh`. Activation order is asymmetric: consumer
publish lands first and proves the `main` pact on the post-merge push; provider verify follows once that
main-branch pact exists (§5).*
