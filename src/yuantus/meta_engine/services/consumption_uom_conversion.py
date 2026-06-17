"""Unit-of-measure conversion for MES consumption ingestion (Consumption R2.4).

A small, versioned, in-code conversion table. Two units are convertible iff they
share a *dimension* (mass / length / volume / count). The MES ingestion route uses
this to convert a declared ``event.uom`` to ``plan.uom`` before persisting, instead
of R2.1's flat reject; genuinely unconvertible units (different dimension or unknown)
still reject (422). See
``docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_CONSUMPTION_MES_UOM_CONVERSION_TASKBOOK_20260617.md``.
"""
from __future__ import annotations

from typing import Dict, Tuple

CONVERSION_TABLE_VERSION = "uom-conversion.v1"

# Round converted quantities so float noise (e.g. 1000 * 0.001) can't make
# equivalent values compare unequal in the R2 conflict check.
_ROUND_DP = 6

# dimension -> {UNIT (upper-cased): factor to the dimension's base unit}
_TABLE: Dict[str, Dict[str, float]] = {
    "mass": {  # base: G
        "G": 1.0, "KG": 1000.0, "MG": 0.001, "T": 1_000_000.0,
        "LB": 453.59237, "OZ": 28.349523125,
    },
    "length": {  # base: MM
        "MM": 1.0, "CM": 10.0, "M": 1000.0, "KM": 1_000_000.0,
        "IN": 25.4, "FT": 304.8,
    },
    "volume": {  # base: ML
        "ML": 1.0, "L": 1000.0, "M3": 1_000_000.0,
    },
    "count": {  # base: EA
        "EA": 1.0, "PCS": 1.0, "PC": 1.0, "DOZEN": 12.0,
    },
}

# UNIT (upper) -> dimension
_UNIT_DIMENSION: Dict[str, str] = {
    unit: dimension for dimension, units in _TABLE.items() for unit in units
}


class UnconvertibleUnitsError(ValueError):
    """Raised when ``from_uom`` and ``to_uom`` cannot be converted — an unknown
    unit on either side, or two units in different dimensions."""

    def __init__(self, *, from_uom: str, to_uom: str) -> None:
        self.from_uom = from_uom
        self.to_uom = to_uom
        super().__init__(f"cannot convert {from_uom!r} to {to_uom!r}")


def _norm(unit: str) -> str:
    return (unit or "").strip().upper()


def convert_quantity(
    quantity: float, from_uom: str, to_uom: str
) -> Tuple[float, float]:
    """Convert ``quantity`` from ``from_uom`` to ``to_uom`` within one dimension.

    Returns ``(converted_quantity, factor)`` where
    ``converted_quantity == round(quantity * factor, 6)``. An identical unit (after
    normalization) is a no-op (factor 1.0). Raises ``UnconvertibleUnitsError`` if a
    unit is unknown or the two units are in different dimensions.
    """
    f = _norm(from_uom)
    t = _norm(to_uom)
    if f == t:
        return round(float(quantity), _ROUND_DP), 1.0
    dim_f = _UNIT_DIMENSION.get(f)
    dim_t = _UNIT_DIMENSION.get(t)
    if dim_f is None or dim_t is None or dim_f != dim_t:
        raise UnconvertibleUnitsError(from_uom=f, to_uom=t)
    factor = _TABLE[dim_f][f] / _TABLE[dim_t][t]
    return round(float(quantity) * factor, _ROUND_DP), factor
