# PLM-Collab V1/V1.1/V2 Seats — Staging Evidence Template

**Date:** YYYY-MM-DD
**Environment:** `<staging host / deployment id>`
**Operator:** `<name>`
**Status:** DRAFT until every required row below is filled with evidence.

Use this template for the operator evidence required by
`docs/development/plm-collab-v1-pact-boundary-and-staging-checklist-20260621.md` §5.
It is an evidence record, not a test plan: paste command output snippets, CI/run URLs, and
explicitly mark anything not run. Do not paste private keys, signed license payloads, bearer
tokens, database URLs, or raw `license_data`.

## 1. Version Pair And Contract Pin

| Field | Value |
|---|---|
| Yuantus commit / image | `<sha or image digest>` |
| MetaSheet2 commit / image | `<sha or image digest>` |
| Pact hash | `5ecbe1ee...` or `<new hash if re-pinned>` |
| Local pact fallback evidence | `<sync_metasheet2_pact.sh --check --verify-provider output or CI URL>` |
| PactFlow broker evidence | `<broker run URL(s), or NOT RUN with reason>` |
| License type | `real vendor-signed` / `dogfood ephemeral-key` |
| Tenant | `<tenant id>` |
| Org | `<org id or n/a>` |

## 2. PactFlow Broker Real-Run

Expected for the post-broker trial baseline:
- MetaSheet2 publishes `Metasheet2@<sha>` on branch `main`.
- Yuantus verifies the broker-sourced pact and publishes `YuantusPLM@<sha>` verification
  results.
- `can-i-deploy` returns a real matrix answer, not an auth/empty-broker error.
- A deliberate drift breaks the advisory broker gate, proving the gate has teeth.

Note: the Phase A `can-i-deploy` command is a broker-population smoke, not a
deployment approval gate; the environment/deployment model is intentionally deferred to
the Phase B blocking flip.

If this section is `NOT RUN`, the evidence can support only a pre-broker staging run, not
the full PactFlow-real-run trial baseline.

Evidence:

```text
<paste redacted PactFlow / GitHub Actions run URLs and key output snippets>
```

Pass/fail:
- [ ] PASS
- [ ] FAIL
- [ ] NOT RUN — reason:

## 3. License Import And Status

Expected:
- `yuantus license import <signed-license.json>` activates `plm.bom_multitable` for the tenant.
- `yuantus license status --tenant-id <tenant>` reports `bom_multitable: ENTITLED`.
- Output does not expose `license_data`, private keys, public-key material, or bearer tokens.

Evidence:

```text
<paste redacted command output / CI URL>
```

Pass/fail:
- [ ] PASS
- [ ] FAIL
- [ ] NOT RUN — reason:

## 4. Capability Manifest And BOM Context

Expected:
- `scripts/dev/smoke_combined_profile.sh` shows health plus manifest `.advisory:true`
  and `bom_multitable.supported:true`.
- `scripts/dev/smoke_bom_review_api.sh` proves:
  - unentitled existing part and missing part both return `context:null` without existence leak;
  - entitled tenant returns `context.part` plus `lines[]`;
  - capability manifest `entitled` toggles true/false by tenant.

Evidence:

```text
<paste redacted smoke output / CI URL>
```

Pass/fail:
- [ ] PASS
- [ ] FAIL
- [ ] NOT RUN — reason:

## 5. Seats Set And Enforce

Expected when testing caps:
- `YUANTUS_QUOTA_MODE=enforce`.
- Import a license carrying `seats=N`.
- Import prints `seat cap projected: TenantQuota.max_users=N`.
- Provisioning the `(N+1)`-th user is blocked with `429 QUOTA_EXCEEDED`
  (or soft-warning evidence if explicitly running `soft` mode).

Evidence:

```text
<paste redacted command/API output / CI URL>
```

Pass/fail:
- [ ] PASS
- [ ] FAIL
- [ ] NOT RUN — reason:

## 6. Seats Clear

Expected:
- Re-import a license carrying explicit `seats:null`.
- `TenantQuota.max_users` is cleared.
- Audit records the cap as cleared, for example `max_users=cleared`.
- The previously blocked `(N+1)`-th user can now be provisioned.
- Contrast remains true: absent `seats` is no-op; `seats:0`/invalid is no-op.

Evidence:

```text
<paste redacted command/API output / CI URL>
```

Pass/fail:
- [ ] PASS
- [ ] FAIL
- [ ] NOT RUN — reason:

## 7. Explicit Untested List

List every item deliberately not covered by this staging run. If none, write `None`.

- `<item>` — reason:

## 8. Trialability Decision

Decision:
- [ ] Trialable for the stated environment/version pair.
- [ ] Not trialable; blocking reason:

Scope of decision:
- [ ] Full post-broker baseline: §2 PactFlow broker real-run is PASS.
- [ ] Pre-broker staging only: §2 is NOT RUN, and this record must not be used to claim
      PactFlow real-run completion.

Reviewer notes:

```text
<notes>
```
