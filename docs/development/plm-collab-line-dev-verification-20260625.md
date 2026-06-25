# PLM × MetaSheet line — development cycle verification (2026-06-25)

**Scope:** verification of what this cycle shipped against the remaining-development plan
(`plm-collab-line-remaining-development-plan-20260625.md`). Covers the two buildable items; the three
deferred items are verified only as **correctly deferred with a named prerequisite** — no fabricated
proof.

## Shipped — verified

### Broker-failure alerting (Yuantus)
- **Change:** a `Notify on contracts gate failure` step at the contracts-job end — `if: failure()`,
  `continue-on-error`, shell-guarded on `ALERT_WEBHOOK_URL`, webhook posted via `curl`.
- **Verified locally:**
  - `test_contracts_gate_failure_alert_is_inert_and_guarded` (in `test_ci_contracts_pact_provider_gate.py`,
    which is in the CI contract-checks run list) asserts: fires only on `failure()`;
    `continue-on-error: true`; **no `steps.if: secrets`** (the unreliable pattern — the `secrets` context
    is unavailable to `steps.if`); the shell guard `[ -z "${ALERT_WEBHOOK_URL:-}" ]`; and the secret is
    only an `env` reference (`ALERT_WEBHOOK_URL: ${{ secrets.ALERT_WEBHOOK_URL }}`), never a literal.
  - `ci.yml` parses as valid YAML; the gate test file is **8 passed** locally.
  - **Inert-until-provisioned by construction:** with `ALERT_WEBHOOK_URL` unset (the default), the step
    logs "skipping" and exits 0 — it cannot affect CI until ops sets the secret.
- **Activation (ops):** set the `ALERT_WEBHOOK_URL` repo secret. No code change.
- **Honest boundary:** green CI does **not** prove the notification fires — that's the same shape as the
  broker-gate activation. The behavioral proof is the first real activation (ops sets the secret + a
  contracts failure occurs). Until then it is verified *wired + inert*, not *firing*.

### Off-main publish hygiene (metasheet2 — DRAFT, owner-gated)
- **Change:** gate the consumer-publish step to `push:main` only + a contract assertion that the publish
  is main-only.
- **Status:** a **draft on the metasheet2 repo** — the consumer repo is owner-gated, so it is not merged
  from here. Its regression test asserts the publish step is main-only so the off-main-version cause
  cannot return.
- **Verified:** in the draft's own CI (metasheet2); referenced here, not re-run.

## Deferred — verified as correctly-deferred (not silently dropped)
- **Consumer-side `can-i-deploy`** — prerequisite: a **provider-verification webhook** (else the
  pending-pact chicken-and-egg makes a blocking version wedge every pact-changing consumer PR and an
  advisory version inert). Owner-gated; sequenced after the owned-HTTPS evidence.
- **`--to-environment`** — prerequisite: a deploy pipeline that calls `record-deployment` (none today;
  `record-deployment` refs = 0).
- **Owned-HTTPS staging re-run** — prerequisite: ops-provisioned HTTPS domains. Instrument ready (#876),
  config-only; the sslip.io-tier evidence is recorded (#875).

## What this cycle did NOT do (by design)
- It did not build a blocking or advisory consumer `can-i-deploy` draft — that would be an unmergeable
  draft on a design that needs a webhook that does not exist (motion, not progress).
- It did not wire `--to-environment` — it would false-block with no recorded deployments.
- It did not stand up a local cross-app rig to "prove" V1.2 — that isn't staging, and the real evidence
  is already recorded (#875) with the owned-HTTPS run instrumented (#876).
