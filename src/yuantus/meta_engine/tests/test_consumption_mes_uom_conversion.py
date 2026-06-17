"""Pure unit-conversion tests for MES consumption ingestion (Consumption R2.4)."""
from __future__ import annotations

import pytest

from yuantus.meta_engine.services.consumption_uom_conversion import (
    CONVERSION_TABLE_VERSION,
    UnconvertibleUnitsError,
    convert_quantity,
)


def test_identity_is_noop():
    assert convert_quantity(5.0, "KG", "KG") == (5.0, 1.0)
    # normalization: case/space-insensitive identity
    assert convert_quantity(5.0, " kg ", "KG") == (5.0, 1.0)


@pytest.mark.parametrize(
    "qty,frm,to,expected",
    [
        (1000.0, "G", "KG", 1.0),
        (1.0, "KG", "G", 1000.0),
        (2.0, "KG", "LB", 4.409245),   # 2*1000/453.59237
        (1.0, "M", "MM", 1000.0),
        (12.0, "IN", "FT", 1.0),
        (1.0, "DOZEN", "EA", 12.0),
        (2.0, "L", "ML", 2000.0),
    ],
)
def test_same_dimension_conversions(qty, frm, to, expected):
    converted, _factor = convert_quantity(qty, frm, to)
    assert converted == pytest.approx(expected)


def test_factor_returned():
    converted, factor = convert_quantity(3.0, "KG", "G")
    assert converted == 3000.0 and factor == 1000.0


def test_rounding_to_6dp():
    # 1 oz -> g is 28.349523125 -> rounded to 6 dp
    converted, _ = convert_quantity(1.0, "OZ", "G")
    assert converted == 28.349523


@pytest.mark.parametrize(
    "frm,to",
    [
        ("KG", "M"),      # mass vs length (different dimension)
        ("L", "EA"),      # volume vs count
        ("KG", "BOGUS"),  # unknown target
        ("BOGUS", "KG"),  # unknown source
    ],
)
def test_unconvertible_raises(frm, to):
    with pytest.raises(UnconvertibleUnitsError):
        convert_quantity(1.0, frm, to)


def test_table_version_is_present():
    assert CONVERSION_TABLE_VERSION
