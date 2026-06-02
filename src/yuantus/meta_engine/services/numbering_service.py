from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional
import re
import uuid

from sqlalchemy import Integer, String, and_, case, cast, func, not_, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from yuantus.context import get_request_context
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.meta_engine.models.numbering import NumberingSequence
from yuantus.meta_engine.services.item_number_keys import (
    ITEM_NUMBER_READ_KEYS,
    ensure_item_number_aliases,
    get_item_number,
)


DEFAULT_NUMBERING_RULES = {
    "Document": {"prefix": "DOC-", "width": 6, "start": 1},
    "Part": {"prefix": "PART-", "width": 6, "start": 1},
}

# G4 token-pattern vocabulary. A pattern is a closed set of `{...}` tokens; the
# non-counter portion is rendered into the existing `prefix` slot and the trailing
# `{seq}` is allocated by the unchanged sequence allocator.
_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
# Locale-INDEPENDENT numeric strftime codes only (no %B/%A/%b/%a/%p names).
_ALLOWED_DATE_CODES = frozenset("YymdHMSj")


@dataclass(frozen=True)
class NumberingRule:
    prefix: str
    width: int
    start: int = 1
    # True when `prefix` is a rendered token-pattern scope-prefix (G4): the
    # allocator then skips the historical floor scan (§6.1) and starts at `start`.
    token_mode: bool = False


@dataclass(frozen=True)
class NumberingScope:
    tenant_id: str
    org_id: str


class NumberingService:
    def __init__(self, session: Session):
        self.session = session

    def apply(self, item_type: ItemType, properties: Optional[dict]) -> dict:
        props = dict(properties or {})
        explicit = get_item_number(props)
        if explicit:
            return ensure_item_number_aliases(props, explicit)

        # Thread the incoming payload properties so a G4 {prop:<key>} token can
        # render a category code. Add-time ordering LOCK (taskbook §4): these are
        # the step-7 add_op values; validator-defaulted properties are not here.
        generated = self.generate(item_type, props)
        if not generated:
            return props
        return ensure_item_number_aliases(props, generated)

    def generate(
        self, item_type: ItemType, properties: Optional[dict] = None
    ) -> Optional[str]:
        rule = self.resolve_rule(item_type, properties)
        if rule is None:
            return None
        value = self._allocate_counter(item_type_id=item_type.id, rule=rule)
        return f"{rule.prefix}{value:0{rule.width}d}"

    def resolve_rule(
        self, item_type: ItemType, properties: Optional[dict] = None
    ) -> Optional[NumberingRule]:
        raw = self._raw_rule_config(item_type)
        if raw is None:
            default = DEFAULT_NUMBERING_RULES.get(item_type.id)
            if default is None:
                return None
            return NumberingRule(**default)

        if not isinstance(raw, dict):
            raise ValueError("ItemType.ui_layout.numbering must be an object")

        enabled = raw.get("enabled", True)
        if enabled is False:
            return None

        # G4 token mode: an explicit `pattern` switches to the token vocabulary.
        # Mutually exclusive with the legacy {prefix,width,start} path below.
        if raw.get("pattern") is not None:
            return self._resolve_token_rule(raw, properties)

        default = DEFAULT_NUMBERING_RULES.get(item_type.id, {})
        prefix = str(raw.get("prefix") or default.get("prefix") or "").strip()
        if not prefix:
            raise ValueError("numbering prefix is required")

        width = raw.get("width", default.get("width", 6))
        start = raw.get("start", default.get("start", 1))
        try:
            width_int = int(width)
            start_int = int(start)
        except (TypeError, ValueError) as exc:
            raise ValueError("numbering width/start must be integers") from exc

        if width_int <= 0:
            raise ValueError("numbering width must be > 0")
        if start_int <= 0:
            raise ValueError("numbering start must be > 0")

        return NumberingRule(prefix=prefix, width=width_int, start=start_int)

    def _resolve_token_rule(
        self, raw: dict, properties: Optional[dict] = None
    ) -> NumberingRule:
        """Resolve a G4 token pattern into a NumberingRule whose `prefix` is the
        rendered scope-prefix and whose counter (`{seq}`) is the trailing
        zero-pad. `width`/`start` are reused for `{seq}`. Enforces the §6 locks:
        non-empty rendered prefix, a non-digit immediately before `{seq}`, and
        rendered length <= 120 (the `prefix` column width). A `{prop:<key>}` token
        renders a config value->code map (`raw["props"][key]["values"]`) against
        the passed-in `properties` (taskbook #686).
        """
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("numbering pattern must be a non-empty string")

        width = raw.get("width", 6)
        start = raw.get("start", 1)
        try:
            width_int = int(width)
            start_int = int(start)
        except (TypeError, ValueError) as exc:
            raise ValueError("numbering width/start must be integers") from exc
        if width_int <= 0:
            raise ValueError("numbering width must be > 0")
        if start_int <= 0:
            raise ValueError("numbering start must be > 0")

        rendered_prefix = self._render_pattern_prefix(
            pattern, props_config=raw.get("props"), properties=properties
        )
        if not rendered_prefix:
            raise ValueError(
                "numbering pattern must render a non-empty prefix before {seq}"
            )
        if rendered_prefix[-1].isdigit():
            # §6.2: a non-digit immediately before {seq} keeps a legacy-mode floor
            # scan on the same item_type from ever miscounting a token number.
            raise ValueError(
                "numbering pattern must place a non-digit separator immediately "
                "before {seq}"
            )
        if len(rendered_prefix) > 120:
            raise ValueError("rendered numbering prefix exceeds 120 characters")

        return NumberingRule(
            prefix=rendered_prefix,
            width=width_int,
            start=start_int,
            token_mode=True,
        )

    def _render_pattern_prefix(
        self,
        pattern: str,
        *,
        props_config: Optional[dict] = None,
        properties: Optional[dict] = None,
    ) -> str:
        """Render the non-counter portion of a token pattern (everything before
        the trailing `{seq}`). Closed token set: literal text, `{date:FMT}`,
        `{prop:<key>}`, and a single trailing `{seq}`. Unknown tokens, stray
        braces, a non-final `{seq}`, or a missing `{seq}` all raise ValueError
        (G4 §4/§6).
        """
        props_config = props_config if isinstance(props_config, dict) else {}
        properties = properties if isinstance(properties, dict) else {}
        parts: list[str] = []
        seq_seen = False
        pos = 0
        for match in _TOKEN_RE.finditer(pattern):
            literal = pattern[pos:match.start()]
            self._reject_stray_braces(literal)
            if seq_seen:
                raise ValueError("{seq} must be the final numbering token")
            parts.append(literal)
            token = match.group(1).strip()
            if token == "seq":
                seq_seen = True
            elif token.startswith("date:"):
                self._validate_date_fmt(token[len("date:"):])
                parts.append(datetime.utcnow().strftime(token[len("date:"):]))
            elif token.startswith("prop:"):
                parts.append(
                    self._render_prop_token(
                        token[len("prop:"):].strip(),
                        props_config=props_config,
                        properties=properties,
                    )
                )
            else:
                raise ValueError(f"unknown numbering token: {{{token}}}")
            pos = match.end()

        trailing = pattern[pos:]
        self._reject_stray_braces(trailing)
        if not seq_seen:
            raise ValueError("numbering pattern must include the {seq} token")
        if trailing:
            raise ValueError("{seq} must be the final numbering token")
        return "".join(parts)

    @staticmethod
    def _render_prop_token(
        key: str, *, props_config: dict, properties: dict
    ) -> str:
        """Render a `{prop:<key>}` token to its config-declared CODE (taskbook
        #686 §3/§4). Fail-closed (ValueError, never normalize) on: empty key;
        undeclared / empty `values` map; duplicate output codes (output-side of
        the "merge two categories" bug); missing / empty property; multi-value
        property; or a value not in the map. The emitted code rides the §6.2/§6.4
        locks on the final rendered prefix (a code ending in a digit before
        `{seq}` is rejected there).
        """
        if not key:
            raise ValueError("prop token requires a key, e.g. {prop:category}")
        spec = props_config.get(key)
        values_map = spec.get("values") if isinstance(spec, dict) else None
        if not isinstance(values_map, dict) or not values_map:
            raise ValueError(
                f"numbering {{prop:{key}}} requires a props['{key}'].values map"
            )
        # §3 output-side lock: two source values mapping to the same code would
        # silently merge two categories into one counter scope. (str() keying:
        # `1` and `"1"` render identically, so they collapse as a duplicate.)
        codes = [str(code) for code in values_map.values()]
        if any(not code.strip() for code in codes):
            # An empty code would silently vanish the category segment.
            raise ValueError(
                f"numbering prop map for '{key}' has an empty output code"
            )
        if len(set(codes)) != len(codes):
            raise ValueError(
                f"numbering prop map for '{key}' has duplicate output codes"
            )
        # §4 add-time ordering: read ONLY the passed-in (step-7 payload) value.
        value = properties.get(key)
        if isinstance(value, (list, dict)):
            raise ValueError(f"numbering prop '{key}' must be a scalar value")
        if value is None or str(value).strip() == "":
            raise ValueError(f"numbering prop '{key}' is missing or empty")
        code = values_map.get(str(value))
        if code is None:
            raise ValueError(
                f"numbering prop '{key}' value is not in the declared map"
            )
        return str(code)

    @staticmethod
    def _reject_stray_braces(literal: str) -> None:
        # §4: no literal-brace passthrough — any `{`/`}` outside a recognized token.
        if "{" in literal or "}" in literal:
            raise ValueError("unbalanced or stray brace in numbering pattern")

    @staticmethod
    def _validate_date_fmt(fmt: str) -> None:
        if not fmt:
            raise ValueError("numbering date token requires a format, e.g. {date:%Y%m}")
        i = 0
        while i < len(fmt):
            ch = fmt[i]
            if ch == "%":
                if i + 1 >= len(fmt):
                    raise ValueError("dangling '%' in numbering date format")
                code = fmt[i + 1]
                if code == "%":
                    i += 2
                    continue
                if code not in _ALLOWED_DATE_CODES:
                    raise ValueError(f"unsupported numbering date code %{code}")
                i += 2
            else:
                if not (ch.isalnum() or ch in "-_./"):
                    raise ValueError(
                        f"unsupported character in numbering date format: {ch!r}"
                    )
                i += 1

    def _raw_rule_config(self, item_type: ItemType) -> Optional[Any]:
        ui_layout = getattr(item_type, "ui_layout", None)
        if not isinstance(ui_layout, dict):
            return None
        return ui_layout.get("numbering")

    def _scope(self) -> NumberingScope:
        ctx = get_request_context()
        tenant_id = str(ctx.tenant_id).strip() if ctx.tenant_id else "default"
        org_id = str(ctx.org_id).strip() if ctx.org_id else "default"
        return NumberingScope(tenant_id=tenant_id or "default", org_id=org_id or "default")

    def _allocate_counter(self, *, item_type_id: str, rule: NumberingRule) -> int:
        bind = self.session.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect_name == "postgresql":
            return self._allocate_counter_postgresql(item_type_id=item_type_id, rule=rule)
        if dialect_name == "sqlite":
            return self._allocate_counter_sqlite(item_type_id=item_type_id, rule=rule)
        return self._allocate_counter_generic(item_type_id=item_type_id, rule=rule)

    def _allocate_counter_postgresql(self, *, item_type_id: str, rule: NumberingRule) -> int:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return self._allocate_counter_upsert(
            dialect_insert=pg_insert,
            item_type_id=item_type_id,
            rule=rule,
            conflict_value_expr_factory=lambda floor_value: func.greatest(
                NumberingSequence.last_value + 1,
                floor_value,
            ),
        )

    def _allocate_counter_sqlite(self, *, item_type_id: str, rule: NumberingRule) -> int:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return self._allocate_counter_upsert(
            dialect_insert=sqlite_insert,
            item_type_id=item_type_id,
            rule=rule,
            conflict_value_expr_factory=lambda floor_value: func.max(
                NumberingSequence.last_value + 1,
                floor_value,
            ),
        )

    def _allocate_counter_upsert(
        self,
        *,
        dialect_insert,
        item_type_id: str,
        rule: NumberingRule,
        conflict_value_expr_factory,
    ) -> int:
        scope = self._scope()
        now = datetime.utcnow()
        floor_value = self._floor_allocated_value(item_type_id=item_type_id, rule=rule)
        stmt = dialect_insert(NumberingSequence).values(
            id=str(uuid.uuid4()),
            item_type_id=item_type_id,
            tenant_id=scope.tenant_id,
            org_id=scope.org_id,
            prefix=rule.prefix,
            width=rule.width,
            last_value=floor_value,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["item_type_id", "tenant_id", "org_id", "prefix"],
            set_={
                "last_value": conflict_value_expr_factory(floor_value),
                "width": rule.width,
                "updated_at": now,
            },
        )
        result = self.session.execute(stmt.returning(NumberingSequence.last_value))
        return int(result.scalar_one())

    def _allocate_counter_generic(self, *, item_type_id: str, rule: NumberingRule) -> int:
        scope = self._scope()
        floor_value = self._floor_allocated_value(item_type_id=item_type_id, rule=rule)
        filters = (
            NumberingSequence.item_type_id == item_type_id,
            NumberingSequence.tenant_id == scope.tenant_id,
            NumberingSequence.org_id == scope.org_id,
            NumberingSequence.prefix == rule.prefix,
        )

        for _ in range(8):
            row = (
                self.session.query(NumberingSequence)
                .filter(*filters)
                .one_or_none()
            )
            if row is None:
                nested = self.session.begin_nested()
                try:
                    row = NumberingSequence(
                        item_type_id=item_type_id,
                        tenant_id=scope.tenant_id,
                        org_id=scope.org_id,
                        prefix=rule.prefix,
                        width=rule.width,
                        last_value=floor_value,
                    )
                    self.session.add(row)
                    self.session.flush()
                    nested.commit()
                    return int(row.last_value)
                except IntegrityError:
                    nested.rollback()
                    self.session.expire_all()
                    continue

            previous = int(row.last_value)
            next_value = max(previous + 1, floor_value)
            result = self.session.execute(
                update(NumberingSequence)
                .where(*filters, NumberingSequence.last_value == previous)
                .values(
                    last_value=next_value,
                    width=rule.width,
                    updated_at=datetime.utcnow(),
                )
            )
            if result.rowcount == 1:
                self.session.flush()
                return next_value

            self.session.expire_all()

        raise RuntimeError("Failed to allocate numbering sequence after retries")

    def _floor_allocated_value(self, *, item_type_id: str, rule: NumberingRule) -> int:
        if rule.token_mode:
            # §6.1: token mode never reads historical numbers. A freshly-rendered
            # scope-prefix has no legacy data in its shape, so the counter starts
            # at `start` and relies solely on the per-prefix sequence row. This
            # also sidesteps the prefix-ambiguity edge of the LIKE-based scan.
            return rule.start
        bind = self.session.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect_name in {"postgresql", "sqlite"}:
            try:
                existing_max = self._max_allocated_value_from_db(
                    item_type_id=item_type_id,
                    rule=rule,
                    dialect_name=dialect_name,
                )
                return max(existing_max or (rule.start - 1), rule.start - 1) + 1
            except OperationalError:
                return rule.start
        return self._floor_allocated_value_python(item_type_id=item_type_id, rule=rule)

    def _floor_allocated_value_python(self, *, item_type_id: str, rule: NumberingRule) -> int:
        existing_max = rule.start - 1
        try:
            rows = (
                self.session.query(Item)
                .filter(Item.item_type_id == item_type_id)
                .all()
            )
        except OperationalError:
            return existing_max + 1
        for row in rows:
            candidate = self._parse_allocated_value(
                get_item_number(getattr(row, "properties", None)),
                prefix=rule.prefix,
            )
            if candidate is not None and candidate > existing_max:
                existing_max = candidate
        return existing_max + 1

    def _max_allocated_value_from_db(
        self,
        *,
        item_type_id: str,
        rule: NumberingRule,
        dialect_name: str,
    ) -> Optional[int]:
        candidates: list[int] = []
        for key in ITEM_NUMBER_READ_KEYS:
            numeric_expr = self._numeric_item_number_expr(
                key=key,
                prefix=rule.prefix,
                dialect_name=dialect_name,
            )
            max_value = (
                self.session.query(func.max(numeric_expr))
                .filter(Item.item_type_id == item_type_id)
                .scalar()
            )
            if max_value is not None:
                candidates.append(int(max_value))
        if not candidates:
            return None
        return max(candidates)

    def _numeric_item_number_expr(
        self,
        *,
        key: str,
        prefix: str,
        dialect_name: str,
    ):
        trimmed_expr = func.trim(self._json_text(Item.properties[key]))
        suffix_expr = func.substr(trimmed_expr, len(prefix) + 1)
        prefix_match = trimmed_expr.like(f"{prefix}%")
        if dialect_name == "postgresql":
            numeric_suffix = suffix_expr.op("~")(r"^[0-9]+$")
        else:
            numeric_suffix = and_(
                suffix_expr != "",
                not_(suffix_expr.op("GLOB")("*[^0-9]*")),
            )
        return case(
            (
                and_(prefix_match, numeric_suffix),
                cast(suffix_expr, Integer),
            ),
            else_=None,
        )

    @staticmethod
    def _json_text(expr):
        if hasattr(expr, "as_string"):
            return expr.as_string()
        if hasattr(expr, "astext"):
            return expr.astext
        return cast(expr, String)

    @staticmethod
    def _parse_allocated_value(value: Optional[str], *, prefix: str) -> Optional[int]:
        text = str(value or "").strip()
        if not text or not text.startswith(prefix):
            return None
        suffix = text[len(prefix) :].strip()
        if not suffix.isdigit():
            return None
        return int(suffix)


def apply_auto_numbering(session: Session, item_type: ItemType, properties: Optional[dict]) -> dict:
    return NumberingService(session).apply(item_type, properties)


def make_item_type(item_type_id: str, *, numbering: Optional[dict] = None) -> Any:
    ui_layout = {"numbering": numbering} if numbering is not None else None
    return SimpleNamespace(id=item_type_id, ui_layout=ui_layout)
