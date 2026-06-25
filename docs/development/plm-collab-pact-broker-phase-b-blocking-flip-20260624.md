# PLM-Collab pact-broker auto-gate — Phase B (blocking flip) — design & verification

**Date:** 2026-06-24
**Status:** Phase B blocking flip implemented on `claude/plm-collab-pact-broker-phase-b`. Flips the
activated Phase A advisory gate (provider verify #854 `f0855650`, real-run activation #861 `a352baa9`)
to **blocking**, per the ratified design `plm-collab-pact-broker-autogate-design-20260621.md` §4/§8.
Owner sign-off recorded 2026-06-24 (selection: "Phase B: flip to blocking").

## 1. What shipped

- `.github/workflows/ci.yml` — the *Pact broker verify + publish + can-i-deploy* step is now **blocking**:
  `continue-on-error: true` removed. A failed provider-verify (`rc1`) or `can-i-deploy` (`rc2`) now
  **fails the Yuantus build** — the step's last line `[ $rc1 -eq 0 ] && [ $rc2 -eq 0 ]` is its exit code.
- Step renamed `(advisory, Phase A)` → `(blocking, Phase B)`; log line → `(Phase B: BLOCKING)`.
- Install resilience: the `curl … install.sh | bash` (the main transient CI fragility under a blocking
  gate) now retries 3×; a persistent failure still blocks (the verify/can-i-deploy below cannot run).
- `test_ci_contracts_pact_provider_gate.py` — a new `test_pact_broker_step_is_blocking_phase_b` pins the
  blocking state: positive Phase-B markers (step name + `(Phase B: BLOCKING)`) and the **absence of a
  `continue-on-error:` directive in the broker step block** (scoped — not a global match, since other
  steps may use `continue-on-error` legitimately).

## 2. What stays the same (deliberately)

- **Secret-guard skip retained.** The step still exits 0 when `PACT_BROKER_BASE_URL` is unset
  (unconfigured / fork CI). This is the legitimate, unit-tested resilience
  (`test_pact_broker_provider_script_skips_without_broker_url`) — *not* a bypass of the gate when the
  broker is configured.
- **`--version $GITHUB_SHA`, no `--to-environment`.** The branch/version `can-i-deploy` is kept; the
  deploy-environment model is documented but not wired (§4).
- **Committed-pact verifier stays.** `test_pact_provider_yuantus_plm.py` remains a redundant,
  broker-independent local gate.
- **Token-missing → `exit 1` is now load-bearing.** A configured-but-tokenless broker fails the build —
  correct: a real misconfiguration should fail loudly rather than silently hollow the gate.

## 3. Consumer-side asymmetry (by design)

The MetaSheet2 consumer workflow (`yuantus-pact-consumer.yml`) only **publishes** the pact (post-merge,
`push:main`) and runs **no `can-i-deploy`** — so it is not a gate and stays advisory. The blocking gate
is the **Yuantus provider's pre-merge** verify + `can-i-deploy`: a Yuantus change that breaks a verified
consumer contract now fails the Yuantus PR. A consumer-side `can-i-deploy --pacticipant Metasheet2`
(gating MetaSheet2 pre-merge) is a possible later slice and is owner-gated on the MetaSheet2 repo; it is
**not** part of Phase B.

## 4. `--to-environment` deploy model (defined, not wired)

CI uses `can-i-deploy --pacticipant YuantusPLM --version $GITHUB_SHA` — the "latest compatible pair"
check. The environment/deployment model (`--to-environment production|staging`) gates a **deploy**, not a
**merge**: it asks the broker "is the version about to land in environment X compatible with everything
already deployed there?" That requires the deploy pipeline to call `pact-broker record-deployment` when a
version is actually deployed. Until deployments are recorded, `--to-environment` errors (no deployed
versions in the environment) and would **false-block**. Phase B therefore keeps the version check and
defers `--to-environment` to whenever a deploy pipeline records deployments.

## 5. Outage posture + the one-line revert

The design (§4) names the trade-off: "a broker outage would wedge both repos' CI." Blocking trades CI
availability for contract safety. Posture actually in place:

- **The most likely flake is the pact-CLI install** (curl from `raw.githubusercontent.com`), not PactFlow
  — so the install retries 3×.
- **A reachability pre-check was deliberately NOT added.** It would be a silent self-skip path that
  hand-builds the exact false-green Phase B exists to kill: a transient blip at the curl (or a slow but
  healthy broker clipped by a timeout) flips the gate to `exit 0`, and a real contract break sails
  through on that run. A gate that skips on infra error is not a gate. If outage tolerance is ever wanted,
  the correct shape is **bounded retry** (`can-i-deploy --retry-while-unknown`) — which preserves the
  gate — not a skip.
- **The proportionate mitigation is the documented one-line revert.** For a sustained PactFlow outage,
  restore `continue-on-error: true` on the broker step (re-defangs to advisory in one line) until it
  recovers. This is noted inline in the step comment. The owner accepted the wedge risk at sign-off.

## 6. Verification

Phase B is a config flip of an already-activated, already-verified gate, so the proof is the activation
evidence — not a newly manufactured live-broker run:

- **The gate has teeth (behavioral proof).** At activation (#861) the deliberate drift-catch produced
  `provider-verify rc=1 / can-i-deploy rc=1` on a broken contract (`features.bom_multitable.{supported,
  entitled}` flipped boolean→string). Under Phase A that verdict was advisory (logged, non-blocking);
  **under Phase B the same `rc=1` now fails the build.** Same verdict, now enforced — that *is* the gate.
- **Config pinned (static proof).** `test_pact_broker_step_is_blocking_phase_b` asserts the blocking
  markers and the absence of a `continue-on-error:` directive in the broker step; the pre-existing
  wiring asserts (install URL, `PATH`, `can-i-deploy --pacticipant YuantusPLM`, token-missing) are
  preserved by the edit.
- **Skip-path safe (unit proof).** `test_pact_broker_provider_script_skips_without_broker_url` (unset →
  `exit 0`) and `test_pact_broker_provider_script_fails_when_url_set_without_token` (configured but
  tokenless → `exit 1`) confirm both branches of the guard.
- **No live-broker run is manufactured.** The verifier flags + auth were confirmed against real PactFlow
  at activation (#861); re-proving them here would be theater.

Local run (this branch): `test_ci_contracts_pact_provider_gate.py` — 7 passed (6 pre-existing + the new
blocking pin); `ci.yml` parses as valid YAML.

## 7. Scope

**In:** the Yuantus provider blocking flip + install retry + the scoped test pin + this MD + the design
doc status update. **Out (deferred):** the `--to-environment` deploy gate (needs deploy-time
`record-deployment`); the consumer-side `can-i-deploy` (MetaSheet2 pre-merge gate, owner-gated); multi
environment/branch selectors at the flip. `sync_metasheet2_pact.sh` is now a convenience mirror — under
Phase B the broker is authoritative.
