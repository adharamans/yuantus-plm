from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.numbering import NumberingSequence
from yuantus.meta_engine.services.numbering_service import (
    NumberingRule,
    NumberingService,
    NumberingScope,
    make_item_type,
)
from yuantus.models.base import Base


def test_apply_generates_item_number_when_missing() -> None:
    session = MagicMock()
    service = NumberingService(session)
    item_type = make_item_type("Part")

    with patch.object(service, "_allocate_counter", return_value=1):
        props = service.apply(item_type, {"name": "Bracket"})

    assert props["item_number"] == "PART-000001"
    assert props["number"] == "PART-000001"


def test_apply_keeps_explicit_item_number() -> None:
    session = MagicMock()
    service = NumberingService(session)
    item_type = make_item_type("Part")

    with patch.object(service, "_allocate_counter") as allocate:
        props = service.apply(item_type, {"item_number": "P-100", "name": "Bracket"})

    allocate.assert_not_called()
    assert props["item_number"] == "P-100"
    assert props["number"] == "P-100"


def test_apply_promotes_explicit_legacy_number_to_canonical_item_number() -> None:
    session = MagicMock()
    service = NumberingService(session)
    item_type = make_item_type("Part")

    props = service.apply(item_type, {"number": "LEG-9", "name": "Legacy Part"})

    assert props["item_number"] == "LEG-9"
    assert props["number"] == "LEG-9"


def test_resolve_rule_rejects_missing_prefix_for_custom_type() -> None:
    session = MagicMock()
    service = NumberingService(session)

    with pytest.raises(ValueError, match="numbering prefix is required"):
        service.resolve_rule(make_item_type("Custom Part", numbering={"enabled": True}))


def test_resolve_rule_rejects_invalid_width() -> None:
    session = MagicMock()
    service = NumberingService(session)

    with pytest.raises(ValueError, match="numbering width/start must be integers"):
        service.resolve_rule(make_item_type("Part", numbering={"width": "oops"}))


def test_generate_dispatches_to_postgresql_branch() -> None:
    session = MagicMock()
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    session.get_bind.return_value = bind
    service = NumberingService(session)

    with patch.object(service, "_allocate_counter_postgresql", return_value=17) as allocate:
        value = service.generate(make_item_type("Part"))

    allocate.assert_called_once()
    assert value == "PART-000017"


def test_generate_dispatches_to_generic_branch_for_other_dialects() -> None:
    session = MagicMock()
    bind = MagicMock()
    bind.dialect.name = "mysql"
    session.get_bind.return_value = bind
    service = NumberingService(session)

    with patch.object(service, "_allocate_counter_generic", return_value=19) as allocate:
        value = service.generate(make_item_type("Part"))

    allocate.assert_called_once()
    assert value == "PART-000019"


def test_generic_allocation_retries_after_insert_race() -> None:
    session = MagicMock()
    service = NumberingService(session)
    query = session.query.return_value.filter.return_value
    query.one_or_none.side_effect = [None, None]
    savepoint1 = MagicMock()
    savepoint2 = MagicMock()
    session.begin_nested.side_effect = [savepoint1, savepoint2]
    session.flush.side_effect = [
        IntegrityError("insert", {}, Exception("duplicate")),
        None,
    ]

    with patch.object(
        service,
        "_scope",
        return_value=NumberingScope(tenant_id="tenant-1", org_id="org-1"),
    ):
        value = service._allocate_counter_generic(
            item_type_id="Part",
            rule=NumberingRule(prefix="PART-", width=6, start=1),
        )

    assert value == 1
    savepoint1.rollback.assert_called_once()
    savepoint2.commit.assert_called_once()
    session.expire_all.assert_called_once()


def test_generic_allocation_retries_after_conflicting_update() -> None:
    session = MagicMock()
    service = NumberingService(session)
    query = session.query.return_value.filter.return_value
    query.one_or_none.side_effect = [
        SimpleNamespace(last_value=4),
        SimpleNamespace(last_value=5),
    ]
    session.execute.side_effect = [
        SimpleNamespace(rowcount=0),
        SimpleNamespace(rowcount=1),
    ]

    with patch.object(
        service,
        "_scope",
        return_value=NumberingScope(tenant_id="tenant-1", org_id="org-1"),
    ):
        value = service._allocate_counter_generic(
            item_type_id="Part",
            rule=NumberingRule(prefix="PART-", width=6, start=1),
        )

    assert value == 6
    session.expire_all.assert_called_once()


def test_sqlite_allocation_is_monotonic_and_unique_under_parallel_calls(tmp_path: Path) -> None:
    db_path = tmp_path / "numbering.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine, tables=[NumberingSequence.__table__])
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    item_type = make_item_type("Part")

    def _worker() -> str:
        session = SessionLocal()
        try:
            value = NumberingService(session).generate(item_type)
            session.commit()
            return value
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _: _worker(), range(8)))

    assert sorted(values) == [f"PART-{idx:06d}" for idx in range(1, 9)]


def test_floor_allocated_value_bootstraps_from_existing_item_numbers() -> None:
    session = MagicMock()
    service = NumberingService(session)
    session.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(properties={"item_number": "PART-000017"}),
        SimpleNamespace(properties={"number": "PART-000003"}),
        SimpleNamespace(properties={"item_number": "DOC-000099"}),
        SimpleNamespace(properties={"item_number": "PART-ABC"}),
    ]

    value = service._floor_allocated_value(
        item_type_id="Part",
        rule=NumberingRule(prefix="PART-", width=6, start=1),
    )

    assert value == 18
    session.query.assert_called_once_with(Item)


def test_floor_allocated_value_uses_python_fallback_for_non_sqlite_postgresql() -> None:
    session = MagicMock()
    bind = MagicMock()
    bind.dialect.name = "mysql"
    session.get_bind.return_value = bind
    service = NumberingService(session)

    with patch.object(service, "_floor_allocated_value_python", return_value=18) as fallback:
        value = service._floor_allocated_value(
            item_type_id="Part",
            rule=NumberingRule(prefix="PART-", width=6, start=1),
        )

    fallback.assert_called_once_with(
        item_type_id="Part",
        rule=NumberingRule(prefix="PART-", width=6, start=1),
    )
    assert value == 18


def test_floor_allocated_value_sqlite_uses_db_aggregation(tmp_path: Path) -> None:
    db_path = tmp_path / "numbering-floor.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine, tables=[Item.__table__])
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        session.add_all(
            [
                Item(
                    id="item-1",
                    item_type_id="Part",
                    config_id="cfg-1",
                    properties={"item_number": "PART-000017"},
                ),
                Item(
                    id="item-2",
                    item_type_id="Part",
                    config_id="cfg-2",
                    properties={"number": "PART-000021"},
                ),
                Item(
                    id="item-3",
                    item_type_id="Part",
                    config_id="cfg-3",
                    properties={"item_number": "PART-ABC"},
                ),
                Item(
                    id="item-4",
                    item_type_id="Part",
                    config_id="cfg-4",
                    properties={"item_number": " DOC-000999 "},
                ),
                Item(
                    id="item-5",
                    item_type_id="Document",
                    config_id="cfg-5",
                    properties={"item_number": "PART-999999"},
                ),
            ]
        )
        session.commit()

        service = NumberingService(session)
        with patch.object(
            service,
            "_floor_allocated_value_python",
            side_effect=AssertionError("python fallback should not be used for sqlite"),
        ):
            value = service._floor_allocated_value(
                item_type_id="Part",
                rule=NumberingRule(prefix="PART-", width=6, start=1),
            )

        assert value == 22
    finally:
        session.close()


def test_generate_sqlite_respects_db_floor_without_python_scan(tmp_path: Path) -> None:
    db_path = tmp_path / "numbering-generate.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine, tables=[Item.__table__, NumberingSequence.__table__])
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        session.add(
            Item(
                id="item-1",
                item_type_id="Part",
                config_id="cfg-1",
                properties={"item_number": "PART-000020"},
            )
        )
        session.commit()

        service = NumberingService(session)
        with patch.object(
            service,
            "_floor_allocated_value_python",
            side_effect=AssertionError("python fallback should not be used for sqlite"),
        ):
            value = service.generate(make_item_type("Part"))
            session.commit()

        assert value == "PART-000021"
    finally:
        session.close()


def test_generic_allocation_respects_existing_item_number_floor_when_sequence_lags() -> None:
    session = MagicMock()
    service = NumberingService(session)

    row_query = MagicMock()
    item_query = MagicMock()

    def _query(model):
        if model is NumberingSequence:
            return row_query
        if model is Item:
            return item_query
        raise AssertionError(f"unexpected query model: {model}")

    session.query.side_effect = _query
    row_query.filter.return_value.one_or_none.return_value = SimpleNamespace(last_value=2)
    item_query.filter.return_value.all.return_value = [
        SimpleNamespace(properties={"item_number": "PART-000020"})
    ]
    session.execute.return_value = SimpleNamespace(rowcount=1)

    with patch.object(
        service,
        "_scope",
        return_value=NumberingScope(tenant_id="tenant-1", org_id="org-1"),
    ):
        value = service._allocate_counter_generic(
            item_type_id="Part",
            rule=NumberingRule(prefix="PART-", width=6, start=1),
        )

    assert value == 21
    session.flush.assert_called_once()


# ==========================================================================
# G4 token-pattern vocabulary (render + validation + real-SQLite floor/scope)
# ==========================================================================

_NS_DATETIME = "yuantus.meta_engine.services.numbering_service.datetime"


def _sqlite_session(tmp_path: Path, name: str):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    # Create only the tables these tests touch (mirrors the existing SQLite tests)
    # to avoid resolving FKs of unrelated, unimported models.
    Base.metadata.create_all(
        engine, tables=[Item.__table__, NumberingSequence.__table__]
    )
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_item(session, item_id: str, number: str) -> None:
    session.add(
        Item(
            id=item_id,
            item_type_id="Part",
            config_id=f"cfg-{item_id}",
            generation=1,
            is_current=True,
            state="Active",
            properties={"item_number": number},
        )
    )
    session.commit()


def test_token_pattern_renders_date_literal_and_trailing_counter() -> None:
    service = NumberingService(MagicMock())
    item_type = make_item_type(
        "Part", numbering={"pattern": "PART-{date:%Y%m}-{seq}", "width": 6, "start": 1}
    )
    with patch(_NS_DATETIME) as mdt:
        mdt.utcnow.return_value = datetime(2026, 1, 15)
        with patch.object(service, "_allocate_counter", return_value=7) as alloc:
            number = service.generate(item_type)

    assert number == "PART-202601-000007"
    rule = alloc.call_args.kwargs["rule"]
    assert rule.prefix == "PART-202601-"  # rendered scope-prefix handed to allocator
    assert rule.token_mode is True


def test_legacy_rule_is_not_token_mode() -> None:
    service = NumberingService(MagicMock())
    rule = service.resolve_rule(
        make_item_type("Part", numbering={"prefix": "PART-", "width": 6, "start": 1})
    )
    assert rule.token_mode is False
    assert rule.prefix == "PART-"


@pytest.mark.parametrize(
    "pattern, message",
    [
        ("PART-{bogus}-{seq}", "unknown numbering token"),
        ("PART-{date:%Y}", "must include the .seq. token"),
        ("PART-{seq}-{date:%Y}", "must be the final"),
        ("PART-{date:%B}-{seq}", "unsupported numbering date code"),
        ("PART-{seq}}", "stray brace"),
        ("PART-{seq", "stray brace"),  # unmatched '{' -> stray brace, not a token
    ],
)
def test_token_pattern_validation_errors(pattern, message) -> None:
    service = NumberingService(MagicMock())
    item_type = make_item_type("Part", numbering={"pattern": pattern})
    with pytest.raises(ValueError, match=message):
        service.resolve_rule(item_type)


def test_token_pattern_rejects_digit_immediately_before_seq() -> None:
    service = NumberingService(MagicMock())
    item_type = make_item_type("Part", numbering={"pattern": "P{date:%Y}{seq}"})
    with patch(_NS_DATETIME) as mdt:
        mdt.utcnow.return_value = datetime(2026, 1, 1)
        with pytest.raises(ValueError, match="non-digit separator"):
            service.resolve_rule(item_type)


def test_token_pattern_rejects_rendered_length_over_120() -> None:
    service = NumberingService(MagicMock())
    item_type = make_item_type("Part", numbering={"pattern": ("X" * 121) + "-{seq}"})
    with pytest.raises(ValueError, match="exceeds 120"):
        service.resolve_rule(item_type)


def test_token_mode_skips_floor_scan_and_starts_at_start(tmp_path: Path) -> None:
    # Seed a HIGH legacy-shaped number; a floor scan WOULD jump the counter to 501.
    session = _sqlite_session(tmp_path, "tok_floor.db")
    _seed_item(session, "p-legacy", "PART-000500")
    service = NumberingService(session)
    item_type = make_item_type(
        "Part", numbering={"pattern": "PART-{date:%Y%m}-{seq}", "width": 6, "start": 1}
    )
    with patch(_NS_DATETIME) as mdt:
        mdt.utcnow.return_value = datetime(2026, 1, 15)
        with patch.object(
            service, "_scope", return_value=NumberingScope("default", "default")
        ):
            first = service.generate(item_type)
            second = service.generate(item_type)

    # §6.1: token mode ignores the seeded legacy floor -> starts at `start`, not 501.
    assert first == "PART-202601-000001"
    assert second == "PART-202601-000002"


def test_token_mode_scopes_counter_per_rendered_prefix(tmp_path: Path) -> None:
    session = _sqlite_session(tmp_path, "tok_scope.db")
    service = NumberingService(session)
    item_type = make_item_type(
        "Part", numbering={"pattern": "PART-{date:%Y%m}-{seq}", "width": 6, "start": 1}
    )
    with patch.object(
        service, "_scope", return_value=NumberingScope("default", "default")
    ):
        with patch(_NS_DATETIME) as mdt:
            mdt.utcnow.return_value = datetime(2026, 1, 10)
            jan1 = service.generate(item_type)
            jan2 = service.generate(item_type)
        with patch(_NS_DATETIME) as mdt:
            mdt.utcnow.return_value = datetime(2026, 2, 10)
            feb1 = service.generate(item_type)

    assert jan1 == "PART-202601-000001"
    assert jan2 == "PART-202601-000002"
    # A new rendered prefix (month) gets its own independent counter.
    assert feb1 == "PART-202602-000001"


# ==========================================================================
# G4 category/property token {prop:<key>} (taskbook #686) — value->code map,
# fail-closed; properties threaded from apply (add-time step-7 payload only).
# ==========================================================================

_CAT_MAP = {
    "pattern": "PART-{prop:category}-{seq}",
    "props": {"category": {"values": {"Mechanical": "MEC", "Electrical": "ELE"}}},
    "width": 4,
}


def test_prop_token_renders_mapped_code_from_properties() -> None:
    service = NumberingService(MagicMock())
    item_type = make_item_type("Part", numbering=_CAT_MAP)
    with patch.object(service, "_allocate_counter", return_value=7) as alloc:
        number = service.generate(item_type, {"category": "Mechanical"})
    assert number == "PART-MEC-0007"
    rule = alloc.call_args.kwargs["rule"]
    assert rule.prefix == "PART-MEC-" and rule.token_mode is True
    # reads the PASSED value (not a default): a different category -> a different code
    with patch.object(service, "_allocate_counter", return_value=1):
        assert service.generate(item_type, {"category": "Electrical"}) == "PART-ELE-0001"


def test_apply_threads_properties_into_prop_token() -> None:
    service = NumberingService(MagicMock())
    item_type = make_item_type(
        "Part",
        numbering={
            "pattern": "PART-{prop:category}-{seq}",
            "props": {"category": {"values": {"Electrical": "ELE"}}},
            "width": 4,
        },
    )
    with patch.object(service, "_allocate_counter", return_value=3):
        props = service.apply(item_type, {"category": "Electrical", "name": "X"})
    assert props["item_number"] == "PART-ELE-0003"


@pytest.mark.parametrize(
    "properties, message",
    [
        ({"category": "Unknown"}, "not in the declared map"),
        ({}, "missing or empty"),
        ({"category": ""}, "missing or empty"),
        ({"category": ["a", "b"]}, "must be a scalar"),
        ({"category": {"k": "v"}}, "must be a scalar"),
    ],
)
def test_prop_token_value_errors(properties, message) -> None:
    service = NumberingService(MagicMock())
    item_type = make_item_type("Part", numbering=_CAT_MAP)
    with pytest.raises(ValueError, match=message):
        service.resolve_rule(item_type, properties)


@pytest.mark.parametrize(
    "numbering, message",
    [
        # no props[key].values map declared
        ({"pattern": "PART-{prop:category}-{seq}"}, "values map"),
        ({"pattern": "PART-{prop:category}-{seq}", "props": {"category": {}}}, "values map"),
        # duplicate output codes -> two source values share one counter scope
        (
            {
                "pattern": "PART-{prop:category}-{seq}",
                "props": {"category": {"values": {"Mechanical": "MEC", "Mech": "MEC"}}},
            },
            "duplicate output codes",
        ),
        # str() keying: int 1 and str "1" render identically -> duplicate
        (
            {
                "pattern": "PART-{prop:category}-{seq}",
                "props": {"category": {"values": {"A": 1, "B": "1"}}},
            },
            "duplicate output codes",
        ),
        # an empty code would silently vanish the category segment
        (
            {
                "pattern": "PART-{prop:category}-{seq}",
                "props": {"category": {"values": {"Mechanical": ""}}},
            },
            "empty output code",
        ),
    ],
)
def test_prop_token_config_errors(numbering, message) -> None:
    service = NumberingService(MagicMock())
    item_type = make_item_type("Part", numbering=numbering)
    with pytest.raises(ValueError, match=message):
        service.resolve_rule(item_type, {"category": "Mechanical"})


def test_prop_token_code_with_trailing_digit_before_seq_rejected() -> None:
    service = NumberingService(MagicMock())
    item_type = make_item_type(
        "Part",
        numbering={
            "pattern": "PART-{prop:category}{seq}",  # no separator before {seq}
            "props": {"category": {"values": {"Mechanical": "MEC2"}}},
        },
    )
    with pytest.raises(ValueError, match="non-digit separator"):
        service.resolve_rule(item_type, {"category": "Mechanical"})


def test_prop_token_rendered_length_over_120_rejected() -> None:
    service = NumberingService(MagicMock())
    item_type = make_item_type(
        "Part",
        numbering={
            "pattern": "{prop:category}-{seq}",
            "props": {"category": {"values": {"Mechanical": "X" * 121}}},
        },
    )
    with pytest.raises(ValueError, match="exceeds 120"):
        service.resolve_rule(item_type, {"category": "Mechanical"})


def test_prop_token_requires_seq_and_must_precede_seq() -> None:
    service = NumberingService(MagicMock())
    no_seq = make_item_type(
        "Part",
        numbering={
            "pattern": "PART-{prop:category}",
            "props": {"category": {"values": {"Mechanical": "MEC"}}},
        },
    )
    with pytest.raises(ValueError, match="must include the .seq. token"):
        service.resolve_rule(no_seq, {"category": "Mechanical"})

    after_seq = make_item_type(
        "Part",
        numbering={
            "pattern": "PART-{seq}-{prop:category}",
            "props": {"category": {"values": {"Mechanical": "MEC"}}},
        },
    )
    with pytest.raises(ValueError, match="must be the final"):
        service.resolve_rule(after_seq, {"category": "Mechanical"})
