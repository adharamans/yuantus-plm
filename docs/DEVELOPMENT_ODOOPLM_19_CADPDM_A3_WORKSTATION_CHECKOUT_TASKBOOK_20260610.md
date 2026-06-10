# Development Taskbook: OdooPLM 19 CAD-PDM A3 workstation checkout context

Date: 2026-06-10
Type: doc-only scope lock. This taskbook authorizes a later implementation PR,
not implementation in this PR.

## 1. Need / No-Need Decision

A3 is now worth scoping because the repository already has a desktop CAD helper
checkout surface:

- `clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` exposes
  `YUANTUS_CHECKOUT` / `YUANTUS_UNDO_CHECKOUT`.
- `clients/cad-desktop-helper/Helper/HelperRuntime.cs` proxies
  `/document/checkout` to backend `POST /cad/{item_id}/checkout`.
- The backend CAD checkout route delegates to `CheckinManager.checkout`, which
  delegates to `VersionService.checkout`.

The current backend lock records only user/time at the version/file surfaces.
It does not record workstation identity or local workspace path. That means two
sessions from the same user but different machines/workspaces are indistinguishable
to the server. OdooPLM's `plm.checkout` stores `hostname` and `hostpws`; the
Yuantus equivalent should be a small extension of the existing checkout locks,
not a new checkout subsystem.

## 2. Grounded Facts

- `ItemVersion` has version-level lock fields: `checked_out_by_id` and
  `checked_out_at`.
- `VersionFile` has file-level lock fields: `checked_out_by_id` and
  `checked_out_at`.
- `VersionService.checkout()` is the item/version checkout path used by the CAD
  check-out facade.
- `VersionFileService.checkout_file()` is the file-level checkout path.
- `cad_checkin_router.checkout_document()` currently accepts no body and returns
  only `locked_by_id`.
- `HelperRuntime.ProxyDocumentLockAsync()` forwards an empty JSON body to
  backend `/cad/{item_id}/{checkout|undo-checkout}`.
- The existing e-sign model already uses a nullable JSON `client_info` envelope.

## 3. Locked Decisions

### D1 - Extend Existing Lock Rows, No New Checkout Table

Add nullable workstation context fields to both lock-owning rows:

- `ItemVersion`: version-level checkout context.
- `VersionFile`: file-level checkout context.

Fields:

- `checkout_client_host: String, nullable`
- `checkout_workspace_path: String, nullable`
- `checkout_client_info: JSON/JSONB, nullable`

No new `plm_checkout` table in R1. The existing lock rows are the source of truth.

### D2 - Backward-Compatible API Inputs

Add optional inputs to the existing checkout paths:

- `VersionService.checkout(..., client_host=None, client_workspace_path=None,
  client_info=None)`
- `VersionFileService.checkout_file(..., client_host=None,
  client_workspace_path=None, client_info=None)`
- `CheckinManager.checkout(..., client_host=None, client_workspace_path=None,
  client_info=None)`
- `POST /cad/{item_id}/checkout` accepts an optional JSON body carrying the same
  three fields; an empty body remains valid.
- Existing `/versions/items/{item_id}/checkout` and
  `/versions/{version_id}/files/{file_id}/checkout` accept the same optional
  fields. Route count stays unchanged.

### D3 - Same User / Different Workstation Semantics

Preserve old clients while making workstation-aware clients safer:

- If no lock exists, checkout stores the supplied context, if any.
- If the same user already owns the lock and both stored and incoming context are
  present, the checkout is idempotent only when `client_host` and
  `client_workspace_path` match.
- If the same user already owns the lock but both contexts are present and differ,
  return a 409-style conflict (`same_user_different_workspace` / message text is
  implementation-defined but must be test-pinned).
- If either side lacks workstation context, preserve legacy same-user idempotency
  and return the current lock context in the response. R1 must not break old API
  clients that still send empty checkout bodies.
- Another user remains a conflict exactly as today.

### D4 - Clear Context On Undo / Checkin

Undo-checkout and checkin clear the workstation fields together with
`checked_out_by_id` and `checked_out_at` at both version and file lock levels.

### D5 - Read Surface

Expose lock context in existing lock/status responses:

- `cad_checkin_router.checkout_document()` response includes `lock_context`.
- `cad_checkin_status` should include the version lock context when available.
- `VersionFileService.get_file_lock()` includes the file lock context.
- Existing response fields remain backward compatible.

### D6 - Desktop Helper Context

The CAD helper should enrich document-lock requests before forwarding:

- `client_host`: `Environment.MachineName` in the helper.
- `client_workspace_path`: request-supplied `workspace_path` when present. The
  LISP command should send the current drawing directory/workspace path when it
  can get one; otherwise omit it for legacy-compatible behavior.
- `client_info`: small JSON envelope such as helper route name/version and any
  safe local metadata already available to the helper. Do not include secrets.

The helper remains a proxy; backend semantics stay authoritative.

### D7 - Migration / Route Count

Implementation adds one additive migration for the six nullable columns across
`meta_item_versions` and `meta_version_files`.

No new route. Route-count baseline remains 708 unless another PR moves main first;
implementation must live-recheck route count and Alembic head before editing.

### D8 - Non-Goals

- No native-signoff gate.
- No file-content sync or local workspace manager.
- No background cleanup of stale locks.
- No new lock table.
- No CAD SDK-dependent build requirement beyond existing helper/LISP static and
  dotnet tests already used by the CAD helper line.

## 4. Implementation Sketch

1. Recheck live route count and Alembic head.
2. Add migration + model columns.
3. Thread optional context through `VersionService.checkout`,
   `VersionFileService.checkout_file`, `CheckinManager.checkout`, and existing
   checkout routers.
4. Implement same-user/different-workspace conflict helper.
5. Clear context on checkin/undo paths.
6. Add response `lock_context` to checkout/status/lock surfaces.
7. Update helper/LISP request shaping to send host/workspace when available.
8. Add DEV/V and CI registrations for any new tests.

## 5. Required Tests

- Version checkout stores context and returns it.
- Same user + same context is idempotent.
- Same user + different nonempty context conflicts.
- Legacy empty-body same-user checkout remains idempotent.
- Checkin/undo clears version checkout context.
- File checkout stores/returns context and clears it on undo.
- CAD checkout route accepts empty body and context body.
- CAD helper forwards host/workspace context to backend.
- LISP static guard proves checkout request includes workspace path when available.
- Migration/model lockstep proves columns exist on both `ItemVersion` and
  `VersionFile`.
- Route-count pins remain unchanged.

## 6. Status

Drafted after CAD-PDM B2b assembly promotion merged (#749). Awaiting doc-only PR
review before implementation.
