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
| Design decision doc (§8 ratified: PactFlow; GitHub repo secrets, least-privilege scoped-write; advisory→blocking) | #843 | open (ratified) |
| Yuantus **provider** — advisory broker verify+publish+can-i-deploy CI step + `scripts/ci/pact_broker_provider_verify.py` | #854 | **DRAFT** |
| MetaSheet2 **consumer** — advisory `pact-broker publish` step after `test:contract` | zensgit/metasheet2#3065 | **DRAFT** |

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

So Phase A is **reviewable wiring that activates on provisioning**, not a verified gate. Both build PRs
are **DRAFT** and must not merge until provisioned **and** one real run is green (§5).

## 3. What IS verified (locally, this session)

- `.github/workflows/ci.yml` and `yuantus-pact-consumer.yml` are valid YAML.
- `test_ci_contracts_pact_provider_gate` — **2 passed**: the new provider step does not trip the gate.
- the local committed-pact verifier still **skips cleanly** (pact-python absent in the dev venv) — the
  additive step did not break it.
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

1. **Consumer publish** — re-run MetaSheet2 `yuantus-pact-consumer.yml`; the "Publish consumer pact"
   step should now run (not skip) and the pact should appear in PactFlow as `metasheet2@<sha>` on branch
   `main`. *If it errors:* confirm the `pact-broker publish` flags + auth against the PactFlow CLI docs.
2. **Provider verify+publish** — re-run Yuantus `contracts`; the "Pact broker verify…" step should run,
   `pact-provider-verifier` should verify the broker pact against the seeded provider and publish results
   as `yuantus-plm@<sha>` branch `main`. *If it errors:* confirm the `pact-provider-verifier` flags +
   broker auth — the script's CLI invocation is **best-effort and must be confirmed here**.
3. **can-i-deploy** — `can-i-deploy --pacticipant yuantus-plm --version <sha>` should return a matrix
   answer (not an auth/empty error), proving the published results populate the matrix.
4. **Drift catch** — deliberately break a pinned field on one side; confirm the broker verification goes
   **red** (advisory) — proving the gate would catch the drift that the §3 boundary doc flagged.
5. **Phase B flip** — only after a stable window of green advisory runs **and** owner sign-off: remove
   `continue-on-error` / make `can-i-deploy` gating (blocking). Until then it stays advisory.

## 6. Known best-effort / confirm-at-activation

- The exact `pact-provider-verifier` + `pact-broker publish` flags, the `pact-ruby-standalone` install,
  and the PactFlow auth model are written from documented patterns but **not run against a live broker**.
  §5.1–5.2 are where they get confirmed/fixed.
- pact-python 3.2.1's native broker API was **deliberately avoided** (the CLI is used instead) to reduce
  version-specific risk on the provider side.

## 7. Scope / non-goals (unchanged)

- The broker covers the existing 32 interactions + future ones (incl. V1.2's embed-token pact).
- **Not** here: Phase B blocking, broker webhooks, SSO / write-back / approval-automation, T2 — separate
  owner-gated lines.

---

*PRs: #843 (design, ratified), #854 (Yuantus provider, draft), zensgit/metasheet2#3065 (consumer, draft).
Local fallback retained: `scripts/sync_metasheet2_pact.sh`. The two build PRs are draft + held until ops
provisioning and one real verification run (§5).*
