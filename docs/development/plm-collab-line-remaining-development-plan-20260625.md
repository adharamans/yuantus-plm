# PLM × MetaSheet line — remaining development plan

**Date:** 2026-06-25
**Status:** the line is operationally live — entitlement ladder + seats; pact-broker **Phase B blocking**
gate (activated, selector-fixed, runbooked); V1.2 in-PLM embedded BOM review verified end-to-end on
temporary public origins. This doc enumerates what development **remains**, with honest
status / dependency / owner, so the gated remainder is legible rather than vanished.

Invariants already on main are referenced, **not re-derived** (the affordance gate framing, the staging
tier caveat, the secret-guard pattern): see the broker ops runbook
(`docs/PACT_BROKER_GATE_OPS_RUNBOOK_20260625.md`), the V1.2 staging instrument
(`docs/development/plm-collab-v1-2-staging-verification-instrument-20260625.md`), and the public-staging
evidence (`docs/development/plm-collab-v1-2-public-staging-evidence-20260625.md`).

## Status legend
- **SHIPPED** — landed this cycle.
- **DRAFT (owner-gated)** — built, but merges through the owning repo's governance.
- **DEFERRED** — designed, blocked on a *named* prerequisite (not "later").

## The five remaining items

### 1. Broker-failure alerting — SHIPPED (inert until provisioned)
- **What:** a `Notify on contracts gate failure` step at the contracts-job end — fires on `failure()`,
  posts a webhook, `continue-on-error` so a notification failure never adds to the build failure.
- **Inert until provisioned:** shell-guarded on `ALERT_WEBHOOK_URL` (unset → skip + exit 0), no webhook
  value in the repo — the **same guard shape as the broker step** (a shell guard, NOT a
  `steps.if: secrets`, which the `secrets` context can't satisfy and would skip even after provisioning).
- **Dependency to activate:** ops provisions the `ALERT_WEBHOOK_URL` repo secret. No code change.
- **Owner:** Yuantus (shipped) + ops (the secret).

### 2. Off-main publish hygiene — DRAFT (owner-gated, metasheet2)
- **What:** the MetaSheet2 consumer-publish workflow runs on `pull_request` too, so PR runs publish
  *off-main* pact versions (`--branch <PR-ref>`). One of those (`a752b0a`) became the broker's
  "global-latest" and false-no'd `can-i-deploy` until #869 pinned the consumer to `--main-branch`.
- **Fix:** gate the *publish step* to `push:main` only (keep the workflow running on PRs for the contract
  *test*, just not the publish) + a contract assertion that the publish is main-only so it can't regress.
- **Relationship to #869:** #869 neutralizes the *effect* (provider `can-i-deploy` ignores off-main
  "latest"); this removes the *cause* (stops creating off-main versions). Belt and suspenders.
- **Dependency:** metasheet2 owner merge (consumer repo is owner-gated).
- **Owner:** metasheet2.

### 3. Consumer-side `can-i-deploy` — DEFERRED (webhook prerequisite)
- **What:** close the provider/consumer gate asymmetry — gate MetaSheet2 *pre-merge* with
  `can-i-deploy --pacticipant Metasheet2`, so a contract-breaking MetaSheet2 change is caught in its own
  CI, not only on the next Yuantus run.
- **Why DEFERRED (not just owner-gated):** the **pending-pact chicken-and-egg** — a consumer pact change
  is not provider-verified until the next Yuantus run, so a **blocking** consumer `can-i-deploy` would
  wedge every pact-changing consumer PR, and an **advisory** one is a non-blocking signal nobody acts on.
  The correct design needs a **provider-verification webhook** (the broker triggers a Yuantus
  verification when the consumer publishes) so the new pact is verified *before* the consumer deploys.
- **Prerequisite:** the provider-verification webhook + metasheet2 owner; sequenced (per owner) after the
  owned-HTTPS staging evidence.
- **Owner:** metasheet2 + the webhook infra.

### 4. `--to-environment` deploy gate — DEFERRED (deploy-pipeline prerequisite)
- **What:** gate a *deploy* (not a merge) by environment — `can-i-deploy --to-environment <env>`.
- **Why DEFERRED:** requires the deploy pipeline to call `pact-broker record-deployment` when a version
  deploys. No deploy/release pipeline records deployments today (`record-deployment` refs = 0); without
  recorded deployments, `--to-environment` errors and would false-block.
- **Prerequisite:** a deploy pipeline that records deployments.
- **Owner:** the deploy pipeline (when it exists).

### 5. Owned-HTTPS staging re-run — DEFERRED (ops env)
- **What:** re-run the V1.2 staging instrument on owned HTTPS domains (vs the temporary sslip.io HTTP
  origins), capturing the full check-#8 expiry→degrade→recover cycle.
- **Status:** instrument ready (#876 strengthened check #8 + the HTTP→HTTPS migration note); the sslip.io
  evidence is recorded (#875). **Config-only** migration per the instrument §1 — no code change.
- **Prerequisite:** ops provisions owned HTTPS domains + deploys both apps.
- **Owner:** ops.

## Parallelism + scope honesty
Items 1 and 2 are independent (different repos) and were the only two genuinely buildable now — done in
parallel. Items 3-5 are each blocked on a distinct prerequisite (provider-verification webhook /
deploy-pipeline / ops HTTPS env) and are **not buildable** until those land. So the plan is "build the
two, name the three" — not "fan out on the unbuildable." Building a complex draft that cannot merge, on a
design that needs infra that does not exist, would be motion, not progress.
