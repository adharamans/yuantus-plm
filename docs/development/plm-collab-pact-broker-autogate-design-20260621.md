# PLM-Collab pact-broker auto-gate — design (decision doc)

**Date:** 2026-06-21
**Status:** owner-ratified and merged as #843 (`1d1031c4`). No code. Closes the residual gap named in
`plm-collab-v1-pact-boundary-and-staging-checklist-20260621.md` §3 — the Yuantus↔MetaSheet2 pact is kept
in sync **manually** (`sync_metasheet2_pact.sh`); there is no automatic cross-repo drift gate. This
designs the pact-broker that adds one. Build is 3 PRs (this design → MetaSheet2 publish → Yuantus pull),
gated on ratification.

## 0. Problem + goal

Both repos pin the same 32-interaction pact (hash `5ecbe1ee…`), verified independently: MetaSheet2's
consumer contract test (route + artifact sanity) and Yuantus's provider verifier (response shape). But the
two pact **files** are reconciled by a human running `sync_metasheet2_pact.sh --check`; **nothing in
either CI fails if they silently diverge.** Goal: a **pact broker** so the consumer publishes its pact and
the provider verifies the *published* version — drift becomes a CI signal, not a thing someone must
remember during a patch.

## 1. Broker hosting — hosted vs self-hosted  *(decision)*

| Option | Pros | Cons |
|---|---|---|
| **Hosted (PactFlow)** | managed, `can-i-deploy` built in, near-zero ops, TLS/auth handled | SaaS dependency + cost; the pact (API shapes only — no secrets/PII) leaves the network |
| **Self-hosted (`pact-foundation/pact-broker` + Postgres)** | full control, no SaaS, data stays in-house | ops burden: host, DB, backups, auth, upgrades |

**Recommendation:** the pact carries only interface schemas (no secrets, no customer data), so for a
2-repo pilot **hosted PactFlow** is the lowest-ops choice. Move to self-hosted only if a data-residency /
no-SaaS policy requires it. *Owner ratifies.*

## 2. Secrets  *(decision)*

The broker needs a base URL + an auth token in **both** repos' CI — and these **never** live in the repo.

- GitHub Actions secrets per repo: `PACT_BROKER_BASE_URL`, `PACT_BROKER_TOKEN`.
- **Least privilege — both tokens *write*, scoped differently.** *(Correction: provider verification
  results must be **published back** to the broker, or `can-i-deploy` has no matrix to read — a read-only
  provider token 403s on publish and hollows the advisory gate.)* MetaSheet2's token may **publish the
  consumer pact**; Yuantus's token may **publish provider verification results + its own provider version
  and branch metadata** — and **not** delete, **not** write MetaSheet2's consumer contract. The `can-i-deploy`
  *query* is read-only; the verify-and-publish-results step is not. Minimally-scoped, separate tokens so a
  leak on one side can't mutate the other's data.
- Token custody (provision + rotate) is an owner/ops task. **No token value appears in any PR.**

## 3. Version + tag naming  *(decision)*

- **Pacticipant names** = the names embedded in the committed pact artifact: consumer
  `Metasheet2`, provider `YuantusPLM`. Do **not** substitute repo slugs such as
  `metasheet2`, `yuantus`, or `yuantus-plm`; those create different pacticipants in the broker.
- **Version** = git commit SHA; **branch** = the GitHub ref name, recorded via the broker's first-class
  `--branch` (e.g. `pact-broker publish --consumer-app-version <sha> --branch main`). Use the **branch**
  concept to carry branch semantics — **not** legacy `--tag`; add a `--tag` only if a legacy integration
  still needs it.
- The pilot publishes consumer `Metasheet2@<sha>` on branch `main` from the post-merge
  `push: main` run; Yuantus publishes provider verification results for `YuantusPLM@<sha>`.
  A provider PR run records its own PR ref as the provider branch; the post-merge `push: main`
  run records branch `main`.
- `can-i-deploy` in Phase A (advisory) targets the **`main` branch / latest compatible pair**. The
  **environment / deployment** model (`--to-environment …`) is defined later, at the Phase B blocking
  flip. Standard pact-broker `branch`/`version` convention; no custom scheme.

## 4. advisory → blocking migration  *(decision)*

Do **not** make the gate blocking on day one — a broker outage would wedge both repos' CI.

- **Phase A — advisory:** wire publish + verify + `can-i-deploy` as **non-blocking** CI steps (record the
  result, never fail the build). Prove the broker and the green path are stable.
- **Phase B — blocking:** after a stability window (e.g. ~2 weeks / N consecutive green runs) **and owner
  sign-off**, flip `can-i-deploy` to **blocking** so a broken / unverified contract fails the build.

> **Executed 2026-06-24** — owner sign-off given (the stability window was short-circuited by explicit
> decision). The broker step is now **blocking** (`continue-on-error` removed) on
> `claude/plm-collab-pact-broker-phase-b`. The secret-guard skip + committed-pact verifier are retained;
> `--to-environment` and the consumer-side `can-i-deploy` stay deferred. Details + verification in
> plm-collab-pact-broker-phase-b-blocking-flip-20260624.md.

## 5. Manual-sync fallback  *(decision)*

Keep `scripts/sync_metasheet2_pact.sh --check --verify-provider` as the **local + backstop** path. The
broker is the *automated* gate; the script stays for local dev and for when the broker is unavailable. The
in-repo provider copy `contracts/pacts/metasheet2-yuantus-plm.json` remains the offline source of truth
until Phase B; after Phase B the broker is authoritative and the copy is a convenience mirror.

## 6. Build sequence (after ratification)

1. **(this) design ratified** — hosting, secrets, naming, migration, fallback agreed.
2. **MetaSheet2 PR** — consumer CI publishes the pact to the broker (`pact-broker publish`, consumer
   version = SHA, `--branch` = ref) instead of only committing the JSON. Owner-gated merge
   (metasheet2). A pull-request run can prove the wiring only; it publishes a PR ref, not `main`.
   The first real `mainBranch` consumer pact is produced by the post-merge `push: main` run.
3. **Yuantus PR** — after the MetaSheet2 `push: main` publish exists, provider CI pulls the
   consumer `mainBranch` pact from the broker, verifies it (augmenting the local copy),
   **publishes the verification results back to the broker** (the write the §2 provider token is
   scoped for), then runs `can-i-deploy` **advisory**. `sync_metasheet2_pact.sh` retained as
   fallback.

Sequencing matters: the provider-pull (3) needs the consumer to have published from `main` first.
Publishing from a MetaSheet2 pull request branch does not satisfy Yuantus's `mainBranch` selector.

## 7. Scope / non-goals

- The broker covers the **existing 32 interactions** and any future ones — including V1.2's
  **embed-token** pact when it lands, which is *why* this precedes V1.2.
- **Not** in scope: SSO, write-back, approval-automation execution, workbench rebuild, or making
  `can-i-deploy` blocking before the stability window.

## 8. Ratified decisions (owner-approved 2026-06-22)

1. **Hosting:** **PactFlow** (hosted) — ratified.
2. **Secrets:** **GitHub Actions repo secrets** in both repos, **least-privilege scoped *write* tokens**
   (MetaSheet2 → publish the consumer pact; Yuantus → publish its own provider verification results) —
   ratified. Provisioning + rotation is an ops task; **no token value appears in any PR**.
3. **advisory→blocking:** **advisory first** for a stability window, then **owner sign-off** flips
   `can-i-deploy` to blocking — ratified.

Build is unblocked (MetaSheet2 consumer-publish → Yuantus provider-pull, both advisory in Phase A). The
one remaining **ops prerequisite** is provisioning the PactFlow account + the two GitHub secrets
(`PACT_BROKER_BASE_URL`, `PACT_BROKER_TOKEN`) in each repo; the CI wiring is written against those secret
names and runs **advisory (non-blocking)** until they exist, so it can land before the secrets are set.

---

*Closes the §3 residual gap of `plm-collab-v1-pact-boundary-and-staging-checklist-20260621.md`. Build PRs:
MetaSheet2 consumer-publish → Yuantus provider-pull. Fallback retained: `scripts/sync_metasheet2_pact.sh`.*
