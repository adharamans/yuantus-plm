"""Lifecycle transition role gate — role-hierarchy support.

The transition role gate previously flat-matched ``role.id == role_allowed_id`` (with a
``# Handle role hierarchy if needed (not implemented here yet)`` placeholder), so a role
that *inherits* the allowed role's rights was wrongly denied. ``RBACRole`` already models
hierarchy (``parent`` + ``has_permission`` walking UP the parent chain). The gate now uses
``LifecycleService._role_satisfies``, which mirrors that inheritance: a descendant of the
allowed role inherits the transition right.

These are pure unit tests of ``_role_satisfies`` (no DB/session) + a source contract pinning
that the gate calls it.
"""
from __future__ import annotations

import pathlib
from types import SimpleNamespace

import yuantus.meta_engine.lifecycle.service as _lifecycle_service_mod
from yuantus.meta_engine.lifecycle.service import LifecycleService
from yuantus.security.rbac.models import RBACRole

_satisfies = LifecycleService._role_satisfies
_allowed = LifecycleService._user_allowed_for_transition
_SVC_TEXT = pathlib.Path(_lifecycle_service_mod.__file__).read_text(encoding="utf-8")


def _role(rid, name, parent=None):
    r = RBACRole(id=rid, name=name)
    r.parent = parent
    return r


# -- source contract ----------------------------------------------------------
def test_gate_wires_hierarchy_aware_decision():
    # the gate delegates the role decision to _user_allowed_for_transition, which walks the
    # role hierarchy via _role_satisfies; the old placeholder comment is gone.
    assert "self._user_allowed_for_transition(user, transition_obj)" in _SVC_TEXT
    assert "_role_satisfies(role, transition_obj.role_allowed_id)" in _SVC_TEXT
    assert "Handle role hierarchy if needed (not implemented" not in _SVC_TEXT


# -- gate decision (behavioral: _user_allowed_for_transition) -----------------
def _user(*, is_superuser=False, roles=()):
    return SimpleNamespace(is_superuser=is_superuser, roles=list(roles))


def _transition(role_allowed_id):
    return SimpleNamespace(role_allowed_id=role_allowed_id, id="t-1")


def test_gate_unrestricted_when_no_role_required():
    # role_allowed_id is None -> the transition is open; any user passes.
    assert _allowed(_user(roles=[_role(9, "Whatever")]), _transition(None)) is True


def test_gate_superuser_bypasses_role_requirement():
    assert _allowed(_user(is_superuser=True, roles=[]), _transition(1)) is True


def test_gate_direct_role_passes():
    assert _allowed(_user(roles=[_role(1, "Approver")]), _transition(1)) is True


def test_gate_descendant_role_passes():
    approver = _role(1, "Approver")
    senior = _role(2, "SeniorApprover", parent=approver)
    # the headline behavior end-to-end through the gate decision: an inheriting role is
    # allowed where the old flat == would have denied.
    assert _allowed(_user(roles=[senior]), _transition(1)) is True


def test_gate_unrelated_role_denied():
    assert _allowed(_user(roles=[_role(9, "Other")]), _transition(1)) is False


def test_gate_no_roles_denied():
    assert _allowed(_user(roles=[]), _transition(1)) is False


# -- _role_satisfies ----------------------------------------------------------
def test_direct_role_match():
    approver = _role(1, "Approver")
    assert _satisfies(approver, 1) is True


def test_descendant_inherits_ancestor_transition_right():
    approver = _role(1, "Approver")
    senior = _role(2, "SeniorApprover", parent=approver)
    # senior inherits approver's rights (mirrors RBACRole.has_permission walking up)
    assert _satisfies(senior, 1) is True


def test_deep_chain_inherits():
    a = _role(1, "A")
    b = _role(2, "B", parent=a)
    c = _role(3, "C", parent=b)
    assert _satisfies(c, 1) is True


def test_ancestor_does_not_inherit_descendant_right():
    approver = _role(1, "Approver")
    senior = _role(2, "SeniorApprover", parent=approver)
    # the ancestor does NOT inherit a descendant's right (inheritance is one-directional)
    assert _satisfies(approver, 2) is False


def test_unrelated_role_denied():
    other = _role(9, "Other")
    assert _satisfies(other, 1) is False


def test_cycle_in_parent_chain_is_safe():
    a = _role(1, "A")
    b = _role(2, "B", parent=a)
    a.parent = b  # malformed cycle a -> b -> a
    # terminates (no infinite loop) and still resolves reachable ids
    assert _satisfies(b, 99) is False
    assert _satisfies(b, 1) is True
    assert _satisfies(a, 2) is True
