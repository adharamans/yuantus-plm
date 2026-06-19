# PLM BOM Review Add-on — V1 + V1.1 Development & Verification

**Date:** 2026-06-19 · **Scope (stated, not re-asked):** the buildable, trial-ready cut we signed —
**V1 (internal dogfood)** + **V1.1 (first external pilot)**. **V1.2 (in-PLM iframe host) and V2
(commercial ops) are deferred** per the agreed ladder; "complete all development" cannot mean the items
we explicitly chose to defer.

Canonical plan: `docs/development/plm-collaboration-upgrade-development-todo-20260618.md` (the taskbook).
This is its **execution + verification record** for the V1/V1.1 cut.

> **Honest framing up front.** This environment has **no docker stack, no `pact` runner, and metasheet2
> is owner-gated + 151 commits behind on the local checkout**. So — unlike a doc backed by a live browser
> run — the verification here leans almost entirely on the **in-process test harness** (which is exactly
> what CI runs). Live-deployment smokes and the metasheet2 consumer changes are written/specified, not run
> here, and are labelled as such. Nothing below is banked as proven unless it actually ran.

---

## 1. What shipped this round (V1 — 100% Yuantus-side, PR-gated)

| Artifact | Path | Status |
|---|---|---|
| **Dogfood license signer** (1B) | `scripts/dev/sign_dogfood_license.py` | **Built + verified** — ephemeral Ed25519, perpetual `plm.bom_multitable`, self-verify PASS |
| **BOM Review API 三态 smoke** (1D) | `scripts/dev/smoke_bom_review_api.sh` | Built (operator artifact, syntax-checked; runs against a live deploy) |
| **Combined-profile smoke** (1C) | `scripts/dev/smoke_combined_profile.sh` | Built (operator artifact, syntax-checked) |
| **V1 dogfood runbook** (1A) | `docs/development/plm-bom-review-addon-v1-dogfood-runbook-20260619.md` | Built |
| **V1 acceptance checklist** (1E) | `docs/development/plm-bom-review-addon-v1-acceptance-checklist-20260619.md` | Built |

The V1 **feature** code was already on `main` (provider projection + capability manifest + embed mint +
entitlement + offline license import; consumer BOM-review panel). V1 is **enablement + proof**, not new
feature code — which is why "ready" is the §2 conditions passing, not these files existing.

---

## 2. Verification — what actually ran here (in-process; = CI)

All on `main`, via `.venv-wp13/bin/python -m pytest`, 2026-06-19:

| Surface | Test | Result |
|---|---|---|
| Offline license import → entitlement | `test_license_import.py` | **13 passed** |
| BOM context 三态 + embed-token + manifest + entitlement + compose profiles + P2-D caps | `test_bom_multitable_projection.py`, `test_bom_multitable_embed_token.py`, `test_integration_capabilities.py`, `test_entitlement_service.py`, `test_ci_contracts_compose_sku_profiles.py`, `test_approval_automation_capabilities.py` | **61 passed** |
| Base flag-OFF surface unchanged | `test_metasheet_bridge_flag_contracts.py` | **5 passed** |

**Plus two purpose-built proofs (run here):**

- **Signer self-verify:** the signer's output passes the deployment's own `verify_license` (same offline
  EdDSA path) — printed `self-verification: PASS`.
- **Entitled-seed / perpetual / tenant-scope proof:** a signed **perpetual** `plm.bom_multitable` license,
  imported, gives `is_entitled("bom_multitable") == True` for its tenant and `False` for another, with the
  `AppLicense` row carrying `expires_at = None`. This **doubles as the S0-3 gating proof** (see §3).

> **79 in-process tests green + signer self-verify + the entitled-seed proof.** This is the real evidence
> base; it is what CI re-runs.

**Written but NOT run here (honest):** the two `scripts/dev/smoke_*.sh` need a live combined deployment
(docker), so they were syntax-checked only — their in-process equivalents (projection/embed/profile/bridge
tests above) are the CI-grade proof. The pact verification needs `pact-python` (absent here) → it is a CI
step (§3).

---

## 3. V1.1 — first external pilot (specified; the metasheet2 half is owner-gated)

V1.1 adds exactly **one thing over V1: the modern-surface contract pact for the two Path-A surfaces**
(capability manifest + BOM context). **Not** multi-`kid` (V2), **not** the embed-token pact (V1.2 — Path A
never calls embed-token). Its value is **safe-to-patch-during-trial**: a fixed version pin protects the
frozen deployment, but the pact catches modern-surface drift in CI when you ship a fix.

### 3.1 Provider seed (Yuantus) — mechanism PROVEN, ready to apply

The pact provider harness (`src/yuantus/api/tests/test_pact_provider_yuantus_plm.py`) seeds ItemTypes/ECO/CAD
but **no license**, so today the manifest/context interactions would render `entitled:false`. Add an active
perpetual license for `PACT_TENANT_ID` so `entitled:true` is deterministic — the §2 proof shows this works:

Use the **proven import path** (the §2 entitled-seed proof — exactly what ran), **not** a raw `AppLicense`
insert (which would touch fields no passing test exercised, e.g. whether `id` is server-defaulted):

```python
# new seed phase in _seed_meta_engine_data -- sign + import a perpetual license (the §2 proven path)
import base64, uuid
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from yuantus.meta_engine.app_framework.license_verification import canonical_payload_bytes
from yuantus.meta_engine.app_framework.license_import_service import LicenseImportService
_priv = Ed25519PrivateKey.generate()
_pub = base64.b64encode(_priv.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
_payload = {"tenant_id": PACT_TENANT_ID, "app_names": ["plm.bom_multitable"], "features": ["bom_multitable"],
            "plan_type": "Pilot", "license_key": uuid.uuid4().hex, "subject": "Pact",
            "issued_at": "2026-06-19T00:00:00Z", "expires_at": None}
_lic = {"alg": "Ed25519", "kid": "pact-kid", "payload": _payload,
        "signature": base64.b64encode(_priv.sign(canonical_payload_bytes(_payload))).decode()}
LicenseImportService(session).import_license(_lic, {"pact-kid": _pub})
```

Send `x-tenant-id: tenant-1` on the two new interactions so `resolve_license_scope` resolves the entitled
tenant (single-mode uses the context tenant when present — confirmed by the §2 proof).

### 3.2 Consumer interactions (metasheet2 — OWNER-GATED; worktree off `origin/main`, never the local tree)

Add to `packages/core-backend/tests/contract/pacts/metasheet2-yuantus-plm.json`:

1. **Manifest** — `GET /api/v1/integrations/capabilities` → `{schema_version, provider:"yuantus-plm",
   advisory:true, features:{bom_multitable:{supported:true, api_version:"v1", entitled:true,
   scenarios:["bom_review"], cache_scope:{...}}, ...}}`; `matchingRules` type-match `entitled`.
2. **BOM context** — `GET /api/v1/bom/multitable/{part_id}/context` (the seeded `P1`) →
   `{feature_key:"bom_multitable", entitled:true, upgrade:{...}, context:{part{...}, lines[{bom_line_id,
   part_id, item_number, name, state, generation, quantity, uom, find_num, refdes, level, path[],
   path_labels[], source_version, source_updated_at, sync_status}], source_version, source_updated_at,
   sync_status, template_key:"bom_review"}}`.

**Verification = CI** (`pact-python` absent here): the sync/provider gate already exists
(`sync_metasheet2_pact.sh --check` + `test_ci_contracts_pact_sync_helper.py` +
`test_ci_contracts_pact_provider_gate.py`); these two interactions just flow through it. Provider *behaviour*
is already proven by §2.

### 3.3 Automation-entry hide (metasheet2 — OWNER-GATED) — a V1.1 concern, not V1

For internal dogfood the `approval_automation` "升级审批自动化" entry is harmless; **before external exposure**
hide/pilot-filter it so you do not over-promise an automation product. It renders when
`approval_automation.supported = true` (`IntegrationWorkbenchView.vue:279`, title at `:1937`), and the
manifest does advertise it with `action_status:"stubbed"` (`integration_capabilities_service.py:52`). Fix:
gate that capability entry behind a pilot flag, or suppress it when `action_status === "stubbed"` (or when
unentitled). One small Vue change; owner-gated.

---

## 4. Deferred (unchanged from the signed ladder)

- **V1.2 embedded pilot:** PLM parent-page host (mint → iframe → origin-pinned postMessage → re-mint on
  single-use replay) + the **embed-token pact** (V1.2 is when token/iframe is actually exercised).
- **V2 commercial:** vendor-private issuance tool, seats (design the limits model first — no `seat_limit`
  field today), grace/renewal, admin status UX, **multi-`kid`** rotation.
- Approval automation execution (Phase 5) · SSO/identity spine (Phase 6) · controlled write-back (Phase 7).

---

## 5. Reproduce

```bash
# sign a dogfood license (ephemeral key, perpetual bom_multitable)
.venv-wp13/bin/python scripts/dev/sign_dogfood_license.py --tenant-id <tenant> --out /tmp/lic.json
# re-run the V1 in-process verification base
.venv-wp13/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_license_import.py \
  src/yuantus/meta_engine/tests/test_bom_multitable_projection.py \
  src/yuantus/meta_engine/tests/test_bom_multitable_embed_token.py \
  src/yuantus/meta_engine/tests/test_integration_capabilities.py \
  src/yuantus/meta_engine/tests/test_entitlement_service.py \
  src/yuantus/meta_engine/tests/test_ci_contracts_compose_sku_profiles.py \
  src/yuantus/meta_engine/tests/test_approval_automation_capabilities.py \
  src/yuantus/api/tests/test_metasheet_bridge_flag_contracts.py
```

---

## 6. Landing

V1 artifacts are Yuantus-side, **PR-gated; merge per owner / branch-protection rules** (branch off `origin/main` `8df513af`). The V1.1
metasheet2 changes (§3.2, §3.3) are **owner-gated** — apply in a worktree off `origin/main`, never the
151-behind local checkout. The provider seed (§3.1) ships with the Yuantus V1.1 PR.

*Generated by Claude (Fable). Evidence: 79 in-process tests + signer self-verify + entitled-seed proof, run
on `main` 2026-06-19. Boundaries (no docker / no pact-python / metasheet2 owner-gated) stated honestly above.*
