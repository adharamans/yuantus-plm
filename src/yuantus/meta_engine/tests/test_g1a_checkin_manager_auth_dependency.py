"""G1-A backend drift guard (taskbook §6.D).

The G1-A helper proxy relies on the backend's existing auth gate: the
`checkout` / `undo-checkout` routes are auth-gated *transitively* because their
dependency `get_checkin_manager` itself depends on `get_current_user`
(`cad_checkin_router.py:25`). If a future backend refactor drops that
dependency, lock/unlock would silently de-auth. This guard pins the
dependency at the source level so such a refactor fails loudly here.

Source-level check (no import side effects).
"""
from __future__ import annotations

import re
from pathlib import Path

ROUTER = (
    Path(__file__).resolve().parents[1] / "web" / "cad_checkin_router.py"
)


def _get_checkin_manager_signature() -> str:
    src = ROUTER.read_text(encoding="utf-8")
    match = re.search(r"def get_checkin_manager\(.*?\)\s*->", src, re.DOTALL)
    assert match, "get_checkin_manager definition not found in cad_checkin_router.py"
    return match.group(0)


def test_g1a_checkin_manager_dependency_pins_get_current_user():
    signature = _get_checkin_manager_signature()
    assert "Depends(get_current_user)" in signature, (
        "get_checkin_manager must Depends(get_current_user) so the backend "
        "/cad/{item_id}/checkout and /undo-checkout routes stay auth-gated. "
        "G1-A's helper proxy depends on this transitive gate (taskbook §6.D)."
    )
