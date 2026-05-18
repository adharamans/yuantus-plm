"""Tests for the automation-rule predicate pure-contract (R1).

Covers DTO validation, the evaluator match matrix, workflow_map_id
independence, the fail-open pin (illegal stored match_predicates ->
empty predicate but workflow_map_id preserved), a real service-parity
matrix vs `_rule_matches_runtime_scope`, an AST purity guard, and a
drift guard vs the service constants.
"""

from __future__ import annotations

import ast
import inspect
from unittest.mock import MagicMock

import pytest

# Register ORM mappers (instantiating WorkflowCustomActionRule for the
# parity matrix triggers SQLAlchemy mapper configuration which needs
# Item registered).
from yuantus.meta_engine.models.item import Item  # noqa: F401
from yuantus.meta_engine.models.parallel_tasks import WorkflowCustomActionRule
from yuantus.meta_engine.services import (
    automation_rule_predicate_contract as mod,
)
from yuantus.meta_engine.services.automation_rule_predicate_contract import (
    WorkflowRuleFacts,
    WorkflowRulePredicate,
    evaluate_rule_predicate,
    normalize_workflow_rule_predicate,
)
from yuantus.meta_engine.services.parallel_tasks_service import (
    WorkflowCustomActionService,
)


def _facts(**kw):
    return WorkflowRuleFacts.from_context(kw)


# --------------------------------------------------------------------------
# DTO
# --------------------------------------------------------------------------


def test_predicate_frozen_and_extra_forbid():
    p = WorkflowRulePredicate(eco_priority="high")
    with pytest.raises(Exception):
        p.eco_priority = "low"
    with pytest.raises(ValueError):
        WorkflowRulePredicate(bogus=1)


def test_predicate_rejects_unknown_enum():
    with pytest.raises(ValueError, match="eco_priority must be one of"):
        WorkflowRulePredicate(eco_priority="whenever")
    with pytest.raises(ValueError, match="eco_type must be one of"):
        WorkflowRulePredicate(eco_type="widget")


def test_normalize_dedups_and_lowercases_actor_roles():
    p = normalize_workflow_rule_predicate(
        None, {"actor_roles": ["QA", "qa", " Eng ", "eng"]}
    )
    assert p.actor_roles == ("qa", "eng")


def test_normalize_drops_blank_and_empties():
    p = normalize_workflow_rule_predicate("  ", {"stage_id": "  "})
    assert p.workflow_map_id is None
    assert p.stage_id is None
    assert p.is_empty() is True


def test_facts_normalization_matches_context_rules():
    f = WorkflowRuleFacts.from_context(
        {"eco_priority": "HIGH", "eco_type": "BOM", "actor_roles": ["QA", "qa"]}
    )
    assert f.eco_priority == "high"
    assert f.eco_type == "bom"
    assert f.actor_roles == ("qa",)


def test_direct_dto_construction_normalizes_like_factory():
    # Medium fix: direct construction must normalize, not just validate
    # membership. The public DTO path must not diverge from the factory /
    # from_context path.
    p_direct = WorkflowRulePredicate(
        eco_priority="HIGH", eco_type="BOM", actor_roles=("QA", "qa", " Eng ")
    )
    p_factory = normalize_workflow_rule_predicate(
        None, {"eco_priority": "high", "eco_type": "bom", "actor_roles": ["qa", "eng"]}
    )
    assert p_direct.eco_priority == "high"
    assert p_direct.eco_type == "bom"
    assert p_direct.actor_roles == ("qa", "eng")
    assert p_direct == p_factory

    f_direct = WorkflowRuleFacts(eco_priority="HIGH", eco_type="BOM")
    f_ctx = WorkflowRuleFacts.from_context({"eco_priority": "HIGH", "eco_type": "BOM"})
    assert f_direct == f_ctx
    assert f_direct.eco_priority == "high"

    # The misuse the reviewer flagged is now impossible: a mixed-case
    # direct predicate matches lowercased facts.
    assert evaluate_rule_predicate(
        WorkflowRulePredicate(actor_roles=("QA",)),
        WorkflowRuleFacts(actor_roles=("qa",)),
    ) is True
    assert evaluate_rule_predicate(
        WorkflowRulePredicate(eco_priority="HIGH"),
        WorkflowRuleFacts.from_context({"eco_priority": "HIGH"}),
    ) is True


def test_facts_direct_construction_does_not_enforce_enum_domain():
    # _normalize_runtime_context only lowercases; it does NOT validate
    # the runtime value against the enum domain. Mirror that.
    f = WorkflowRuleFacts(eco_priority="exotic")
    assert f.eco_priority == "exotic"


# --------------------------------------------------------------------------
# Evaluator matrix
# --------------------------------------------------------------------------


def test_empty_predicate_matches_everything():
    p = normalize_workflow_rule_predicate(None, None)
    assert p.is_empty() is True
    assert evaluate_rule_predicate(p, _facts()) is True
    assert evaluate_rule_predicate(p, _facts(eco_type="bom", stage_id="s1")) is True


@pytest.mark.parametrize(
    "key,pval,good,bad",
    [
        ("stage_id", "s1", {"stage_id": "s1"}, {"stage_id": "s2"}),
        ("eco_priority", "high", {"eco_priority": "high"}, {"eco_priority": "low"}),
        ("product_id", "p1", {"product_id": "p1"}, {"product_id": "p9"}),
        ("eco_type", "bom", {"eco_type": "bom"}, {"eco_type": "document"}),
    ],
)
def test_single_key_truthy_equality(key, pval, good, bad):
    p = normalize_workflow_rule_predicate(None, {key: pval})
    assert evaluate_rule_predicate(p, _facts(**good)) is True
    assert evaluate_rule_predicate(p, _facts(**bad)) is False
    # absent fact for a constrained key -> no match
    assert evaluate_rule_predicate(p, _facts()) is False


def test_actor_roles_intersection_semantics():
    p = normalize_workflow_rule_predicate(None, {"actor_roles": ["qa", "eng"]})
    assert evaluate_rule_predicate(p, _facts(actor_roles=["eng"])) is True
    assert evaluate_rule_predicate(p, _facts(actor_roles=["pm"])) is False
    assert evaluate_rule_predicate(p, _facts()) is False


def test_multi_key_is_and():
    p = normalize_workflow_rule_predicate(
        None, {"eco_type": "bom", "eco_priority": "high"}
    )
    assert evaluate_rule_predicate(p, _facts(eco_type="bom", eco_priority="high")) is True
    assert evaluate_rule_predicate(p, _facts(eco_type="bom", eco_priority="low")) is False


# --------------------------------------------------------------------------
# workflow_map_id independence
# --------------------------------------------------------------------------


def test_workflow_map_id_matches_independently():
    p = normalize_workflow_rule_predicate("wf1", None)
    assert p.is_empty() is False
    assert evaluate_rule_predicate(p, _facts(workflow_map_id="wf1")) is True
    assert evaluate_rule_predicate(p, _facts(workflow_map_id="wf2")) is False
    assert evaluate_rule_predicate(p, _facts()) is False


def test_workflow_map_id_and_predicates_are_anded():
    p = normalize_workflow_rule_predicate("wf1", {"eco_type": "bom"})
    assert evaluate_rule_predicate(
        p, _facts(workflow_map_id="wf1", eco_type="bom")
    ) is True
    assert evaluate_rule_predicate(
        p, _facts(workflow_map_id="wf1", eco_type="document")
    ) is False
    assert evaluate_rule_predicate(
        p, _facts(workflow_map_id="wf2", eco_type="bom")
    ) is False


# --------------------------------------------------------------------------
# Fail-open pin (mirrors the contract's normalize_workflow_rule_predicate
# fail-open — historically lived inside the service's
# _rule_match_predicates helper, now deleted as dead code post-#599)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_mp",
    [
        {"bogus": 1},  # unsupported key
        {"eco_priority": "whenever"},  # bad enum
        {"eco_type": "widget"},  # bad enum
        {"actor_roles": "qa"},  # non-array
        ["not", "a", "dict"],  # non-dict
    ],
)
def test_fail_open_drops_predicates_but_keeps_workflow_map_id(bad_mp):
    # An illegal stored match_predicates degrades to the empty predicate
    # (match-all) -- this is the CURRENT behavior, originally implemented
    # by WorkflowCustomActionService._rule_match_predicates (deleted as
    # dead code post-#599; the fail-open now lives solely in the
    # contract's normalize_workflow_rule_predicate). It must NOT be
    # hardened in this PR. workflow_map_id (a rule column read before
    # the fail-open path) is preserved.
    with_wf = normalize_workflow_rule_predicate("wfX", bad_mp)
    assert with_wf.workflow_map_id == "wfX"
    assert with_wf.stage_id is None and with_wf.eco_priority is None
    assert with_wf.actor_roles == () and with_wf.product_id is None
    assert with_wf.eco_type is None
    # only workflow_map_id constrains; everything else matches all.
    assert evaluate_rule_predicate(with_wf, _facts(workflow_map_id="wfX")) is True
    assert evaluate_rule_predicate(with_wf, _facts(workflow_map_id="other")) is False

    no_wf = normalize_workflow_rule_predicate(None, bad_mp)
    assert no_wf.is_empty() is True
    assert evaluate_rule_predicate(no_wf, _facts(eco_type="bom")) is True


# --------------------------------------------------------------------------
# Spec-derived frozen parity snapshot — the single regression net
# --------------------------------------------------------------------------
#
# Each `expected` value is hand-derived from the documented matcher
# semantics in `_rule_matches_runtime_scope` (steps 1–7: workflow_map_id
# eq → stage_id eq → eco_priority eq → product_id eq → eco_type eq →
# actor_roles set-intersection; absent predicate value = wildcard;
# normalization fail-open keeps `workflow_map_id` but zeros
# match_predicates-derived keys).
#
# This is NOT a runtime-captured snapshot — it is derived from the
# spec independently of any service implementation, so post-substitution
# (where the service delegates to the contract) the snapshot remains a
# meaningful regression net rather than becoming circular.
#
# Single source of truth: both `test_contract_matches_spec_derived_parity_snapshot`
# (regression net) and `test_service_delegates_to_contract` (wiring smoke)
# parametrize over this same constant. A future case addition lands in
# exactly one place.
#
# See `docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_AUTOMATION_ENGINE_SUBSTITUTION_20260518.md`
# §5 for the per-row derivation.

_AUTOMATION_PARITY_SNAPSHOT = (
    # (workflow_map_id, raw_match_predicates, runtime_context, expected)
    (None, None, {}, True),
    (None, {}, {"eco_type": "bom"}, True),
    (None, {"eco_type": "bom"}, {"eco_type": "bom"}, True),
    (None, {"eco_type": "bom"}, {"eco_type": "document"}, False),
    (None, {"eco_type": "bom"}, {}, False),
    (None, {"eco_priority": "High"}, {"eco_priority": "high"}, True),
    (
        None,
        {"actor_roles": ["QA", "Eng"]},
        {"actor_roles": ["eng", "pm"]},
        True,
    ),
    (None, {"actor_roles": ["qa"]}, {"actor_roles": ["pm"]}, False),
    (
        None,
        {"stage_id": "s1", "product_id": "p1"},
        {"stage_id": "s1", "product_id": "p1"},
        True,
    ),
    (
        None,
        {"stage_id": "s1", "product_id": "p1"},
        {"stage_id": "s1", "product_id": "p9"},
        False,
    ),
    ("wf1", None, {"workflow_map_id": "wf1"}, True),
    ("wf1", None, {"workflow_map_id": "wf2"}, False),
    (
        "wf1",
        {"eco_type": "bom"},
        {"workflow_map_id": "wf1", "eco_type": "bom"},
        True,
    ),
    (
        "wf1",
        {"eco_type": "bom"},
        {"workflow_map_id": "wf2", "eco_type": "bom"},
        False,
    ),
    # mixed-case actor_roles on BOTH sides: bit-for-bit lowercasing is
    # load-bearing (service _normalize_match_predicates +
    # _normalize_runtime_context both lowercase).
    (
        None,
        {"actor_roles": ["QA", "Eng"]},
        {"actor_roles": ["ENG", "Pm"]},
        True,
    ),
    (None, {"actor_roles": ["QA"]}, {"actor_roles": ["Pm"]}, False),
    # illegal/fail-open cases
    (None, {"bogus": 1}, {"eco_type": "bom"}, True),
    ("wfX", {"bogus": 1}, {"eco_type": "bom"}, False),
    ("wfX", {"bogus": 1}, {"workflow_map_id": "wfX"}, True),
    (None, {"eco_priority": "nope"}, {"eco_priority": "high"}, True),
    (None, {"actor_roles": "qa"}, {"actor_roles": ["qa"]}, True),
)


@pytest.mark.parametrize(
    "wf,mp,ctx,expected", _AUTOMATION_PARITY_SNAPSHOT
)
def test_contract_matches_spec_derived_parity_snapshot(wf, mp, ctx, expected):
    """The contract's
    ``evaluate_rule_predicate(normalize_workflow_rule_predicate(wf,
    mp), WorkflowRuleFacts.from_context(ctx))`` must match the
    spec-derived ``expected`` value for every case in the 21-row
    snapshot — bit-for-bit.

    This is the regression net post-substitution: a contract
    semantic drift on any one of the 21 cases fails loudly here.
    """

    decision = evaluate_rule_predicate(
        normalize_workflow_rule_predicate(wf, mp),
        WorkflowRuleFacts.from_context(ctx),
    )
    assert decision == expected, (
        f"snapshot drift: wf={wf} mp={mp} ctx={ctx} "
        f"expected={expected} actual={decision}"
    )


@pytest.mark.parametrize(
    "wf,mp,ctx,_expected", _AUTOMATION_PARITY_SNAPSHOT
)
def test_service_delegates_to_contract(wf, mp, ctx, _expected):
    """Post-substitution (#598 `397422e`), the service's
    `_rule_matches_runtime_scope` body delegates to the contract;
    both sides compute via the same code path, so this assertion
    is **tautological by construction** — it verifies the
    delegation is wired end-to-end (rule + context flow through
    the contract's API and back), not an independent-implementation
    parity check. The regression-net role transferred to
    `test_contract_matches_spec_derived_parity_snapshot` above,
    which pins outputs to spec-derived truth.

    Parametrizes over the same snapshot constant — single source
    of truth. The ``_expected`` column is bound but unused here;
    it's verified by the snapshot test above.
    """

    svc = WorkflowCustomActionService(MagicMock())
    rule = WorkflowCustomActionRule(
        name="parity-rule",
        workflow_map_id=wf,
        action_params=({"match_predicates": mp} if mp is not None else {}),
    )
    service_decision = svc._rule_matches_runtime_scope(
        rule=rule, context=svc._normalize_runtime_context(ctx)
    )
    contract_decision = evaluate_rule_predicate(
        normalize_workflow_rule_predicate(wf, mp),
        WorkflowRuleFacts.from_context(ctx),
    )
    assert service_decision == contract_decision, (
        f"delegation drift: wf={wf} mp={mp} ctx={ctx} "
        f"service={service_decision} contract={contract_decision}"
    )


def test_runtime_scope_delegates_to_contract():
    """AST pin: `WorkflowCustomActionService._rule_matches_runtime_scope`
    must delegate to the merged contract.

    Positive: its source must reference each of the three contract
    surfaces (the import line, the predicate normalizer, the facts
    builder, the evaluator). Note: this relies on the local-import
    pattern (`from … import …` inside the method body, per
    `_rule_matches_runtime_scope`'s implementation). If the import
    is ever hoisted to module level, the import-string check still
    holds against the module-qualified contract identifiers
    (`normalize_workflow_rule_predicate(` etc.) — those are unique
    to the contract surface.

    Negative: the method body must NOT call
    `self._rule_match_predicates`. That helper was the
    pre-substitution matcher's predicate accessor; its only caller
    was `_rule_matches_runtime_scope`. A future refactor that
    re-introduces local matcher arithmetic via that helper would
    have to call it again, which this AST check forbids.
    """

    import textwrap as _textwrap

    from yuantus.meta_engine.services import parallel_tasks_service as svc_mod

    method_src = inspect.getsource(
        svc_mod.WorkflowCustomActionService._rule_matches_runtime_scope
    )

    # Positive: contract surfaces present.
    assert "automation_rule_predicate_contract" in method_src
    assert "normalize_workflow_rule_predicate(" in method_src
    assert "WorkflowRuleFacts.from_context(" in method_src
    assert "evaluate_rule_predicate(" in method_src

    # Negative: must not call self._rule_match_predicates anywhere
    # in this method body — precise semantic inverse of the
    # substitution. Parse the dedented source; walk for Call nodes
    # whose func is an Attribute named `_rule_match_predicates`.
    tree = ast.parse(_textwrap.dedent(method_src))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "_rule_match_predicates", (
                "_rule_matches_runtime_scope must not call "
                "_rule_match_predicates; if matcher arithmetic is needed, "
                "delegate to the contract via evaluate_rule_predicate."
            )


def test_rule_match_predicates_helper_is_deleted():
    """Drift guard for the mechanical cleanup after #599: the
    `_rule_match_predicates` helper was dead code post-substitution
    (its only caller, `_rule_matches_runtime_scope`, now delegates
    to the contract) and was removed in the cleanup PR. A future
    re-introduction — for example, an accidental revert of the
    substitution that brings back local matcher arithmetic and
    needs this helper again — must fail loudly here so it is
    caught before landing.

    Companion to `test_runtime_scope_delegates_to_contract` which
    pins **no call from inside the substituted body**; this test
    pins the helper's **complete removal from the class**.
    """

    from yuantus.meta_engine.services import parallel_tasks_service as svc_mod

    assert not hasattr(
        svc_mod.WorkflowCustomActionService, "_rule_match_predicates"
    ), (
        "_rule_match_predicates was deleted as dead code in the "
        "mechanical cleanup after #599; re-introducing it suggests "
        "the substitution to the merged contract has been reverted "
        "or that local matcher arithmetic is creeping back in. "
        "Delegate via evaluate_rule_predicate instead."
    )


# --------------------------------------------------------------------------
# Purity guard
# --------------------------------------------------------------------------


def test_module_is_pure():
    tree = ast.parse(inspect.getsource(mod))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    joined = " ".join(imported)
    for forbidden in (
        "yuantus.database",
        "sqlalchemy",
        "parallel_tasks_service",
        "WorkflowCustomActionService",
        "_router",
        "plugins",
    ):
        assert forbidden not in joined, f"must stay pure: imports {forbidden!r}"
    # enum domains come from the model layer
    assert "yuantus.meta_engine.models.eco" in joined


# --------------------------------------------------------------------------
# Drift guard vs the live service constants
# --------------------------------------------------------------------------


def test_drift_predicate_key_set_matches_service():
    assert set(mod._ALLOWED_MATCH_PREDICATE_KEYS) == set(
        WorkflowCustomActionService._ALLOWED_MATCH_PREDICATES
    )


def test_drift_enum_domains_match_service():
    assert set(mod._ALLOWED_ECO_PRIORITIES) == set(
        WorkflowCustomActionService._ALLOWED_ECO_PRIORITIES
    )
    assert set(mod._ALLOWED_ECO_TYPES) == set(
        WorkflowCustomActionService._ALLOWED_ECO_TYPES
    )
