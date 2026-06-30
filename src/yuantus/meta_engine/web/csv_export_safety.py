"""CSV export safety helpers."""
from __future__ import annotations

from typing import Any


_FORMULA_PREFIXES = ("=", "+", "-", "@")
_CONTROL_PREFIXES = ("\t", "\r", "\n")


def neutralize_csv_formula(value: Any) -> Any:
    """Return a CSV-cell value safe to open in spreadsheet apps.

    Python's csv writer quotes delimiter/newline characters, but spreadsheet apps still
    evaluate quoted cells whose value begins with formula prefixes. Prefix a single quote
    for text that would become a formula after leading whitespace/control trimming.
    """

    if not isinstance(value, str) or not value:
        return value
    stripped = value.lstrip(" \t\r\n")
    if value[0] in _CONTROL_PREFIXES or (stripped and stripped[0] in _FORMULA_PREFIXES):
        return "'" + value
    return value
