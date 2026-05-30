# OdooPLM Gap Ledger Refresh

Date: 2026-05-30

Scope: lightweight status refresh for
`DEVELOPMENT_ODOOPLM_GROUNDED_COMPARISON_20260525.md` after the G1/G2/G3/G4/G5
follow-up slices landed on `main`.

This document is **doc-only**. It does not authorize implementation. It also
does not rewrite the original 2026-05-25 comparison; that file remains the
historical evidence snapshot. This refresh records how the gap ledger should be
read after the subsequent PR chain.

## 1. Why this refresh exists

The original grounded comparison correctly identified the investable gaps, but
several of those gaps have since moved from "gap" to "implemented" or "software
closed". Reading the original table without a current ledger now overstates the
remaining delta against OdooPLM.

This refresh keeps the original analysis intact and adds a dated current-state
ledger.

## 2. Current gap ledger

| Gap | Original concern | Current state on `main` | Residual / next opt-in |
|---|---|---|---|
| G1 CAD helper last mile | CAD-side command layer was missing; helper had no checkout/checkin routes; LISP was display-only. | **Software-side closed.** Helper exposes checkout/undo/status/checkin/bom-import; LISP has six commands; `yuantus-helper-upload` supplies multipart upload. Closeout: `DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_LAST_MILE_CLOSEOUT_20260527.md` (#662). | Native-CAD operational signoff remains hardware/operator evidence; productized installer / command-list packaging remains separate. |
| G2 PLM to ERP publication | No downstream ERP transaction/publication surface for released items. | **Functional publication spine closed.** R1 readiness API, R2 outbox/routes/worker, R3 generic HTTP connector, and R4 read-only export are on `main` (#663-#676). | Vendor-specific adapters only when a concrete ERP target exists. |
| G3 3D visual explode | Overlay/markup existed, but spatial explode config was missing. | **Thin server surface implemented.** Explode config is validated and persisted in `meta_3d_overlays.properties["explode"]`; route count is 690; no migration or geometry computation. See `DEV_AND_VERIFICATION_ODOOPLM_G3_3D_EXPLODE_IMPL_20260530.md` (#682). | BOM-derived auto-layout and multiple preset tables remain deferred; geometry/rendering stay client-side. |
| G4 numbering pattern vocabulary | Numbering was prefix + zero-padded counter; token vocabulary was narrow. | **v1 token pattern implemented.** Literal + UTC date + trailing `{seq}` render into the existing prefix slot, with no migration/route/schema change. See `DEV_AND_VERIFICATION_ODOOPLM_G4_NUMBERING_PATTERN_IMPL_20260529.md` (#680). | Category/property token remains deferred pending add-time ordering, sanitization, and row-cardinality decisions. |
| G5 spare parts | `plm_spare` equivalent was absent. | **Implemented.** Spare relationships use existing `ItemType(is_relationship=True)` precedent, no bespoke table and no migration. See `DEV_AND_VERIFICATION_ODOOPLM_G5_SPARE_PARTS_IMPL_20260529.md` (#678). | Optional future tightening: release guards or MBOM-aware explode only with a grounded need. |
| G6 production installations / scale validation | OdooPLM has years of production installs; Yuantus cannot close this with code. | **Still open, non-code.** Code completeness is separate from deployment evidence. | Pilot deployments, reference evidence, and live-scale validation. |
| minor finishing/treatment / project integration | Process attributes and project integration were partial. | **Still open / lower priority.** No follow-up slice has been authorized in this line. | Start with a grounding taskbook if product priority rises. |

## 3. How to read the original comparison now

- `DEVELOPMENT_ODOOPLM_GROUNDED_COMPARISON_20260525.md` remains the source for
  original evidence and license posture. Do not retroactively rewrite its
  module-by-module table as if the later work existed on 2026-05-25.
- The current state for priority planning should use this ledger plus the
  DEV/verification records listed above.
- Status words are deliberately narrower than marketing claims:
  - "software-side closed" for G1 does not mean native-CAD operational signoff is
    complete.
  - "functional publication spine closed" for G2 means generic publication
    contract, outbox, worker, HTTP connector, and export exist; it does not mean a
    named vendor ERP adapter has been certified.
  - "thin server surface implemented" for G3 means server-side config storage and
    validation only; it does not claim client rendering or geometry auto-layout.

## 4. Recommended next decisions

1. If continuing OdooPLM parity work, choose one deferred follow-up explicitly:
   G3 BOM-derived auto-layout, G4 category/property token, finishing/treatment
   attributes, or `plm_project`.
2. If moving toward market validation, prioritize G1 native-CAD operational
   signoff and G6 pilot evidence over new parity slices.
3. If targeting a real ERP, start a vendor-specific adapter taskbook behind the
   G2 registry seam; do not add vendor behavior to the generic connector without
   a scoped taskbook.

## 5. Non-goals

- No code changes.
- No new Odoo/OdooPLM code reuse.
- No change to the GPL/AGPL-safe semantic-only posture from the original
  comparison.
- No claim that the deferred items are authorized for implementation.
