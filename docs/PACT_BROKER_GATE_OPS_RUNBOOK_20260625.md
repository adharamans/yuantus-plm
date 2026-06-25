# Pact-broker gate — operational runbook

**Date:** 2026-06-25
**Scope:** operating the **blocking** pact-broker auto-gate (Phase B, #864 `bf0f9e55`). When the CI
`contracts` job fails on the broker step, this tells you whether it is a real contract break or an infra
outage, and how to respond.

## The gate

- **Step:** `Pact broker verify + publish + can-i-deploy (blocking, Phase B)` in the `contracts` job
  (`.github/workflows/ci.yml`).
- **Blocking:** a non-zero exit fails the build (Phase B; #864). No `continue-on-error`.
- **Skips when unconfigured:** if `PACT_BROKER_BASE_URL` is unset (forks, or the secret removed) the step
  logs `skipping broker verification (unconfigured / fork CI)` and exits 0 — not a failure.
- **What it does:** spins up the seeded provider, runs `pact_verifier_cli` against the broker-sourced
  consumer pact (`--publish`), then `can-i-deploy` for the provider version with the **consumer pinned to
  its main branch** (`--pacticipant Metasheet2 --main-branch`, matching provider-verify's `mainBranch`
  selector; #869) plus `--retry-while-unknown` for pending verifications. The `--main-branch` pin matters:
  without it, `can-i-deploy` checks the consumer's *global-latest* version across all branches, which a
  stray off-main publish can occupy and false-no the build (see the triage table).

## Triage — red `contracts` job on the broker step

Open the step log and match the symptom:

| Log signal | Diagnosis | Action |
|---|---|---|
| `provider-verify rc=1` / `can-i-deploy rc=1` with a mismatch / "incompatible" detail | **Real contract break** — a Yuantus change no longer satisfies the consumer pact | Fix the provider (or revert the change). **Do NOT revert the gate** — it is doing its job. |
| `can-i-deploy rc=1` "There is no verified pact between the latest Metasheet2 (…) and … YuantusPLM" — but **`provider-verify rc=0`** | A consumer version became broker-`latest` that this provider SHA never verified — typically an **off-main** consumer version published as latest (provider-verify only checks `mainBranch`) | **Not** a provider break. The gate pins the consumer to `--main-branch` (#869) so off-main "latest" is ignored; if it recurs, confirm `--pacticipant Metasheet2 --main-branch` is present and matches provider-verify's `mainBranch` selector. (This was the 2026-06-25 wedge, fixed by #869.) |
| `Pact CLI install failed … after 3 attempts` | **Infra** — the `raw.githubusercontent.com` install flaked past the 3× retry | Re-run the job. If persistent, the install source / CDN is down. |
| verify / can-i-deploy errors with network / 5xx / timeout (not a contract mismatch) | **PactFlow outage** | The one-line revert (below), if sustained. |
| `skipping broker verification (unconfigured / fork CI)` | The broker secret is unset; the gate did not run | Nothing — not a failure. |
| `Computer says yes \o/` + `rc=0 … (Phase B: BLOCKING)` | Healthy pass | — |

The distinguishing question: **does the failure name a contract mismatch, or an infra / network error?**
Contract mismatch → fix the contract. Infra / outage → the gate is collateral; revert only if sustained.

## The one-line revert (sustained broker outage)

If PactFlow is down long enough to wedge CI, **re-defang the gate to advisory in one line**: restore
`continue-on-error: true` on the broker step in `.github/workflows/ci.yml` (immediately after the step's
comment block, before `env:`). Commit + merge → CI unwedges; the step still runs and logs, but no longer
fails the build. The committed-pact verifier (`test_pact_provider_yuantus_plm.py`) remains the live local
gate meanwhile, so contract coverage is not lost — only the cross-repo broker check is paused.

## Re-enable (after the broker recovers)

Remove the `continue-on-error: true` again (back to blocking). Confirm a green run shows
`provider-verify rc=0 can-i-deploy rc=0 (Phase B: BLOCKING)` with the step **actually running** (not the
skip line).

## Stability-window review

Phase B's design (#843 §4) defined the blocking flip after a ~2-week stability window; at the actual flip
(#864) the window was **short-circuited by owner sign-off**, so the review happens *retroactively*:

- Track broker-step outcomes on `main` post-flip. Healthy = `rc=0 … (Phase B: BLOCKING)`.
- If the gate runs clean for the equivalent window (~2 weeks / N consecutive green runs with no
  infra-wedge), Phase B is **settled** — no action.
- If it infra-wedges (install / outage), the correct fix is **resilience that preserves the gate** — pin
  or cache the pact CLI, or a bounded `can-i-deploy --retry-while-unknown` — **not** a skip-on-error. A
  gate that skips on infra error is not a gate (this is why the Phase B flip rejected a reachability
  pre-check; see the Phase B MD §5).

## Alerting (gap, not implemented)

There is no automated broker-failure alert today; the signal is a red `contracts` job. A future
enhancement is a notification (e.g. Slack) on broker-step failure, which needs a webhook secret. Noted,
deferred — not part of this runbook.

## Consumer-side gate (known asymmetry, deferred)

The MetaSheet2 consumer only **publishes** (post-merge, no `can-i-deploy`), so a contract-breaking
MetaSheet2 change is caught on the *next Yuantus run*, not in MetaSheet2's own pre-merge CI. Closing this
(a consumer-side `can-i-deploy --pacticipant Metasheet2`) is a **development** follow-up, owner-gated on
the MetaSheet2 repo and complicated by pending-pact verification — out of scope for operating the gate.

## Related

- Design: `docs/development/plm-collab-pact-broker-autogate-design-20260621.md` (§4 migration).
- Phase B flip + verification: `docs/development/plm-collab-pact-broker-phase-b-blocking-flip-20260624.md`.
- Activation evidence: #861 (`a352baa9`) — provider-verify rc=0, drift-catch rc=1, can-i-deploy rc=0.
