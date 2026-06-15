# DEV & Verification: WP3.2 (B3) item_number immutability (impl)

Date: 2026-06-15

Implements **WP3.2 / gap B3** from
`ODOOPLM_BORROW_DEVELOPMENT_PLAN_AND_TODO_20260604.md` §WP3.2: `item_number` (and its
`number` alias) is **immutable once assigned**. **No route, no migration, no schema
change** — a single guard in the AML update path.

## As built (against the plan §WP3.2 design)

- **Guard in `operations/update_op.py`**, placed after the permission + lifecycle-lock
  checks and before the property merge. Using the canonical alias reader
  `get_item_number` (reads `item_number` then `number`):
  ```python
  existing_number = get_item_number(item.properties or {})
  incoming_number = get_item_number(aml.properties or {})
  if existing_number and incoming_number and incoming_number != existing_number:
      if not (_ITEM_NUMBER_OVERRIDE_ROLES & set(self.roles or [])):
          raise ValidationError("item_number is immutable once assigned", field="item_number")
      logger.warning("item_number immutability overridden: ...", ...)
  ```
- **Scope is exactly "change a non-empty number to a different non-empty value".**
  First assignment (`existing_number` is `None`), no-op re-submits
  (`incoming == existing`), and updates that don't touch the number
  (`incoming_number` is `None`) all pass through unchanged — the existing
  `ensure_item_number_aliases` merge then keeps `item_number`/`number` in sync.
- **Override: `admin` / `superuser`** (`_ITEM_NUMBER_OVERRIDE_ROLES`, the same
  privileged set `MetaPermissionService` already treats as bypass at
  `meta_permission_service.py:46`), read from the operation's RBAC `self.roles`.
- **Audit: a `WARNING` log** (`item_id` / existing / incoming / actor / roles). A
  structured log (not a new `DomainEvent`) was chosen deliberately: a new event type
  would have to be reconciled with the search-indexer event-coverage contract
  (`test_phase4_search_closeout_contracts` `EXPECTED_INDEXED/UNINDEXED_EVENTS`); the
  override is a rare privileged action, and the normal `ItemUpdatedEvent` still fires.
- **Add path untouched.** Immutability is an *update*-only concern; creation still
  assigns the number freely.

## Verification (Python 3.11, no-DB; injected-engine unit test)

`test_item_number_immutable.py` → **7 passed** (drives `UpdateOperation.execute` over
in-memory SQLite with a `SimpleNamespace` engine; lifecycle-lock + event-bus seams
monkeypatched so the test pins immutability, not wiring):
- change assigned number as a normal role → `ValidationError("immutable")`, DB unchanged;
- change via the `number` alias → also rejected;
- **first assignment** allowed (+ alias synced);
- **re-submitting the same number** while editing another field → allowed;
- editing other fields with no number in the payload → allowed, number preserved;
- **admin / superuser override** (parametrized) → allowed, both aliases move to the new
  value, and the override `WARNING` is emitted (audited).

Blast radius — the guard is in the hot `update` path, so the **full CI `contracts`
list** was run locally: no legitimate item_number-update flow is blocked (see PR for
the green run). Test **dual-registered** (`ci.yml` contracts list + `conftest.py`
no-DB allowlist). `create_app()` route count unchanged at **709**.

## Not in this PR

- No clearing-to-empty policy (the plan's guard is "non-empty → different non-empty"
  only).
- No migration / no `item_number` history table; the audit is the override log.
- No change to the Add path, numbering service, or alias semantics.
