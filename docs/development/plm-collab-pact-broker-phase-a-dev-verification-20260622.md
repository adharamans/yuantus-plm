# DEV & VERIFICATION — PLM-Collab pact-broker auto-gate (Phase A wiring)

**Date:** 2026-06-22
**Companion to:** the ratified design `plm-collab-pact-broker-autogate-design-20260621.md` (§8
owner-ratified) and `plm-collab-v1-pact-boundary-and-staging-checklist-20260621.md` §3 (which named the
manual-sync drift gap this closes).

This records the **Phase A (advisory) wiring** of the pact-broker auto-gate and the post-provisioning
activation evidence. The broker is now live for the V1/V1.1 read surfaces: MetaSheet2 publishes the
consumer pact, Yuantus verifies the broker-sourced pact and publishes provider verification results, and
`can-i-deploy` returns a real matrix answer. The gate is still **advisory**; the Phase B blocking flip is
deferred until a stable window plus owner sign-off.

## 1. What shipped

| Piece | PR | State |
|---|---|---|
| Design decision doc (§8 ratified: PactFlow; GitHub repo secrets, least-privilege scoped-write; advisory→blocking) | #843 | **MERGED** (`1d1031c4`) |
| Yuantus **provider** — advisory broker verify+publish+can-i-deploy CI step + `scripts/ci/pact_broker_provider_verify.py` | #854 | **MERGED** (`f0855650`) after real broker verify/publish/can-i-deploy green on `main` |
| MetaSheet2 **consumer** — advisory `pact-broker publish` step after `test:contract` | zensgit/metasheet2#3065 | **MERGED** (`bfedff511f78d3b2026a89022473a7d42bf4fc09`) after publishing the `main` consumer pact |

Both build PRs are **strictly additive**: the committed-pact verifier (`test_pact_provider_yuantus_plm`)
and the consumer `test:contract` remain the **live** gates, unchanged. `scripts/sync_metasheet2_pact.sh`
is retained as the local/manual fallback.

## 2. The honest verification boundary (read this)

Before provisioning, a green run did **not** mean the broker integration worked. With no PactFlow account
or secrets:

- every broker step maps the secret to step-level `env` and a **shell guard** at the top of `run` skips
  (exit 0) when it's empty — a clean no-op until ops provisions the secret, then auto-activates. It
  deliberately does **not** use a step `if:`: the `secrets` context is unavailable to `steps.if`, which
  would skip even after the secret is set. A green CI today = "the guarded step skipped," not "the broker
  works" — the same vacuous-green trap, stated plainly so it can't read as "done."
- the only real verification today is the **local** gates (committed pact + `test:contract`), which are
  untouched and live.

That pre-provisioning caveat is now closed by the real-run evidence in §4. The remaining boundary is
different: Phase A is verified **advisory** wiring, not a blocking deployment gate. The committed-pact
verifier and consumer `test:contract` remain the live in-repo gates; broker failure is still advisory
until the Phase B flip.

## 3. What was verified before broker provisioning

- `.github/workflows/ci.yml` and `yuantus-pact-consumer.yml` are valid YAML.
- Yuantus #854 pre-provisioning PR head `56866eee` was CI-green after the latest hardening commit. Its
  `contracts` job executes
  `test_ci_contracts_pact_provider_gate` (**6 passed** locally), pinning:
  - `workflow_dispatch` remains present for manual provider activation/reruns once secrets exist;
  - shell-guard skip when `PACT_BROKER_BASE_URL` is absent;
  - configured-but-missing-token failure;
  - token redaction;
  - provider `YuantusPLM`;
  - broker consumer selector `{"mainBranch": true}`;
  - `PACT_BROKER_ERROR_ON_UNKNOWN_OPTION=true`, so misspelled broker CLI flags fail at
    activation instead of becoming a vacuous advisory green;
  - `pact-ruby-standalone` install plus `pact/bin` PATH wiring for the
    `pact-provider-verifier` / `pact-broker` CLIs.
- MetaSheet2#3065 pre-provisioning PR head `6d5de54` was CI-green after the latest hardening commit. Its
  `yuantus-pact-consumer` workflow
  now executes `scripts/ops/yuantus-pact-consumer-broker-workflow-contract.test.mjs`, pinning
  local `test:contract` before publish, env+shell guards instead of `steps.if`, SHA + broker
  `--branch` semantics, no legacy `--tag`, `PACT_BROKER_ERROR_ON_UNKNOWN_OPTION=true`, and
  the documented `workflow_dispatch` default-branch activation caveat.
- the local committed-pact verifier remains the live provider gate in Yuantus CI; the additive
  broker step has not replaced it.
- the wiring matches the ratified design: provider uses a scoped-**write** token to *publish verification
  results* (not read-only); consumer a scoped-**write** token to *publish the pact*; both use the broker's
  first-class `--branch` (not legacy tags); `can-i-deploy` is informational in Phase A.
  Because Phase A deliberately does not choose a `--to-environment` / deployment model, this command
  is a broker-population smoke (matrix answer exists), not a release/deployment approval gate.

## 4. Activation evidence after provisioning

The PactFlow account and GitHub secrets were provisioned, then the asymmetric activation sequence was run:

1. **Consumer publish first:** MetaSheet2#3065 merged to `main` at
   `bfedff511f78d3b2026a89022473a7d42bf4fc09`. The `Yuantus Pact Consumer` workflow run
   `28075321423` created `Metasheet2` version `bfedff511f78d3b2026a89022473a7d42bf4fc09` on branch
   `main` and published the pact for provider `YuantusPLM`.
2. **Provider verify/publish second:** Yuantus#854 verified the broker-sourced pact against the seeded
   provider, published the Yuantus verification result, and `can-i-deploy` returned a real matrix answer.
   The final PR verification run was `28080778247` / contracts job `83134996857`, with
   `pact_verifier_cli exit=0`, `Computer says yes`, and `provider-verify rc=0 can-i-deploy rc=0`.
3. **Drift catch:** a temporary provider proxy deliberately changed
   `features.bom_multitable.supported` and `features.bom_multitable.entitled` from booleans to strings.
   Broker verification failed as expected in run `28080419796` / contracts job `83133857389`:
   both fields reported Boolean-vs-String mismatches, `pact_verifier_cli exit=1`, and
   `provider-verify rc=1 can-i-deploy rc=1`. The temporary drift commits were then reverted before
   merge.
4. **Main-branch confirmation:** #854 squash-merged at `f0855650`. The post-merge `main` CI run
   `28081796150` / contracts job `83138269775` passed, and the broker step published
   `YuantusPLM@f0855650` on branch `main` with verification result `207 (success)`,
   `pact_verifier_cli exit=0`, `Computer says yes`, and
   `provider-verify rc=0 can-i-deploy rc=0`.

One nuance from the drift catch: changing `entitled: true` to `entitled: false` alone did not fail the
broker verification, because this pact matcher pins the field type/shape rather than the literal boolean
value. The meaningful drift proof is the type/shape mismatch above.

## 5. Ops prerequisite (completed for Phase A)

Phase A required ops to provision:
1. the **PactFlow** account / instance;
2. **two least-privilege scoped-WRITE tokens** — MetaSheet2 (publish consumer pact) and Yuantus
   (publish its own provider verification results); `can-i-deploy` queries can use a read-only token;
3. GitHub Actions **secrets** in BOTH repos: `PACT_BROKER_BASE_URL`, `PACT_BROKER_TOKEN`. Never in-repo.

Those prerequisites were satisfied for the Phase A real-run. Secret values remain outside the repo and
must not be pasted into staging evidence.

## 6. Activation + verification runbook (completed for Phase A; reuse for reruns)

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
   verification results populate the matrix. This is not a deployment approval: Phase A intentionally
   has no `--to-environment` / deployment record. Choose that model at the Phase B blocking flip.
4. **Drift catch** — deliberately break a pinned field on one side; confirm the broker verification goes
   **red** (advisory) — proving the gate would catch the drift that the §3 boundary doc flagged.
5. **Phase B flip** — only after a stable window of green advisory runs **and** owner sign-off: remove
   `continue-on-error` / make `can-i-deploy` gating (blocking). Until then it stays advisory.

## 7. Known best-effort / confirm-at-activation

- The exact broker flags and PactFlow auth model were confirmed by the real-run evidence in §4. Keep this
  section as the checklist for future drift debugging or token rotation, because a broker/CLI upgrade can
  still break flags or auth semantics.
- pact-python 3.2.1's native broker API was **deliberately avoided** (the CLI is used instead) to reduce
  version-specific risk on the provider side.

## 8. Scope / non-goals (unchanged)

- The broker launched with the existing 32 interactions and carries later additions (including
  V1.2's embed-token pact) as they land in the committed artifact.
- **Not** here: Phase B blocking, broker webhooks, SSO / write-back / approval-automation, T2 — separate
  owner-gated lines.

---

*PRs: #843 (design, merged `1d1031c4`), #854 (Yuantus provider, merged `f0855650`),
zensgit/metasheet2#3065 (consumer, merged `bfedff511f78d3b2026a89022473a7d42bf4fc09`).
Local fallback retained: `scripts/sync_metasheet2_pact.sh`. Activation order is asymmetric: consumer
publish lands first and proves the `main` pact on the post-merge push; provider verify follows once that
main-branch pact exists (§6).*
