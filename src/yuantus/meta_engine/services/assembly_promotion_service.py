"""B2b CAD-PDM assembly promotion orchestration.

Promotes a Part assembly in child-before-parent order while preserving the
existing LifecycleService.promote semantics for every item.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from yuantus.meta_engine.lifecycle.models import (
    LifecycleMap,
    LifecycleState,
    LifecycleTransition,
)
from yuantus.meta_engine.lifecycle.service import LifecycleService
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.meta_engine.schemas.aml import AMLAction
from yuantus.meta_engine.services.item_number_keys import get_item_number
from yuantus.meta_engine.services.item_release_service import ItemReleaseService
from yuantus.meta_engine.services.meta_permission_service import MetaPermissionService


ASSEMBLY = "ASSEMBLY"
MAX_PROMOTION_PATHS = 50_000


PermissionChecker = Callable[[Item], bool]


class AssemblyPromotionService:
    def __init__(self, session: Session):
        self.session = session

    def promote_assembly(
        self,
        root_id: str,
        *,
        target_state: str = "Released",
        dry_run: bool = True,
        max_depth: int = 10,
        user_id: int | str = 0,
        user_roles: Optional[List[str]] = None,
        permission_checker: Optional[PermissionChecker] = None,
        comment: str = "",
    ) -> Dict[str, Any]:
        if target_state != "Released":
            raise ValueError("target_state must be Released")

        plan, errors = self._build_plan(
            root_id,
            target_state=target_state,
            max_depth=max_depth,
            user_id=user_id,
            user_roles=user_roles or [],
            permission_checker=permission_checker,
        )
        if errors or any(row["action"] == "blocked" for row in plan):
            return self._response(
                root_id=root_id,
                target_state=target_state,
                dry_run=dry_run,
                plan=plan,
                errors=errors,
            )
        if dry_run:
            return self._response(
                root_id=root_id,
                target_state=target_state,
                dry_run=True,
                plan=plan,
                errors=[],
            )

        tx = self.session.begin_nested()
        try:
            lifecycle = LifecycleService(self.session)
            for row in plan:
                if row["action"] != "promote":
                    continue
                item = self.session.get(Item, row["item_id"])
                if item is None:
                    row["action"] = "blocked"
                    row["blocking_reason"] = "item_missing"
                    errors.append(
                        {
                            "code": "item_missing",
                            "item_id": row["item_id"],
                            "message": f"Item missing during promotion: {row['item_id']}",
                        }
                    )
                    tx.rollback()
                    return self._response(
                        root_id=root_id,
                        target_state=target_state,
                        dry_run=False,
                        plan=plan,
                        errors=errors,
                    )
                try:
                    result = lifecycle.promote(
                        item,
                        target_state,
                        int(user_id) if str(user_id).isdigit() else 0,
                        comment=comment,
                    )
                except Exception as exc:
                    row["action"] = "blocked"
                    row["blocking_reason"] = "promote_exception"
                    row["error"] = str(exc)
                    errors.append(
                        {
                            "code": "promote_exception",
                            "item_id": item.id,
                            "message": str(exc),
                        }
                    )
                    tx.rollback()
                    return self._response(
                        root_id=root_id,
                        target_state=target_state,
                        dry_run=False,
                        plan=plan,
                        errors=errors,
                    )
                if not result.success:
                    row["action"] = "blocked"
                    row["blocking_reason"] = "promote_failed"
                    row["error"] = result.error
                    errors.append(
                        {
                            "code": "promote_failed",
                            "item_id": item.id,
                            "message": result.error,
                        }
                    )
                    tx.rollback()
                    return self._response(
                        root_id=root_id,
                        target_state=target_state,
                        dry_run=False,
                        plan=plan,
                        errors=errors,
                    )
                row["from_state"] = result.from_state
                row["to_state"] = result.to_state
                self.session.flush()
            tx.commit()
        except Exception:
            tx.rollback()
            raise

        return self._response(
            root_id=root_id,
            target_state=target_state,
            dry_run=False,
            plan=plan,
            errors=[],
        )

    def _build_plan(
        self,
        root_id: str,
        *,
        target_state: str,
        max_depth: int,
        user_id: int | str,
        user_roles: List[str],
        permission_checker: Optional[PermissionChecker],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        items, parent_to_children, path_meta, errors = self._collect_graph(
            root_id, max_depth=max_depth
        )
        order = self._topological_child_first(items.keys(), parent_to_children)
        release = ItemReleaseService(self.session)
        plan: List[Dict[str, Any]] = []
        for item_id in order:
            item = items[item_id]
            meta = path_meta[item_id]
            row = {
                "item_id": item.id,
                "item_number": get_item_number(item.properties or {}),
                "state": item.state,
                "target_state": target_state,
                "depth": meta["max_depth"],
                "path": list(meta["first_path"]),
                "relationship_path": list(meta["first_relationship_path"]),
                "action": "promote",
                "blocking_reason": None,
            }
            if release._is_released(item):
                row["action"] = "skip_already_released"
            elif not self._has_promote_permission(
                item,
                user_id=user_id,
                user_roles=user_roles,
                permission_checker=permission_checker,
            ):
                row["action"] = "blocked"
                row["blocking_reason"] = "permission_denied"
            else:
                transition_error = self._transition_error(item, target_state)
                if transition_error:
                    row["action"] = "blocked"
                    row["blocking_reason"] = transition_error
            plan.append(row)
        return plan, errors

    def _collect_graph(
        self, root_id: str, *, max_depth: int
    ) -> Tuple[
        Dict[str, Item],
        Dict[str, Set[str]],
        Dict[str, Dict[str, Any]],
        List[Dict[str, Any]],
    ]:
        root = self.session.get(Item, root_id)
        if root is None:
            return {}, {}, {}, [
                {
                    "code": "item_missing",
                    "item_id": root_id,
                    "message": f"Item not found: {root_id}",
                }
            ]

        items: Dict[str, Item] = {root.id: root}
        parent_to_children: Dict[str, Set[str]] = defaultdict(set)
        path_meta: Dict[str, Dict[str, Any]] = {
            root.id: {
                "min_depth": 0,
                "max_depth": 0,
                "first_path": [root.id],
                "first_relationship_path": [],
            }
        }
        errors: List[Dict[str, Any]] = []
        path_count = 0
        queue = deque([(root.id, 0, [root.id], [])])
        while queue:
            item_id, depth, path, rel_path = queue.popleft()
            path_count += 1
            if path_count > MAX_PROMOTION_PATHS:
                errors.append(
                    {
                        "code": "traversal_budget_exceeded",
                        "item_id": item_id,
                        "message": (
                            f"assembly promotion traversal exceeded "
                            f"{MAX_PROMOTION_PATHS} paths"
                        ),
                    }
                )
                break
            edges = self._assembly_edges(item_id)
            if depth >= max_depth:
                if edges:
                    errors.append(
                        {
                            "code": "max_depth_exceeded",
                            "item_id": item_id,
                            "max_depth": max_depth,
                            "relationship_id": edges[0].id,
                            "message": (
                                "ASSEMBLY graph exceeds requested max_depth; "
                                "promotion plan was not truncated"
                            ),
                        }
                    )
                continue
            for edge in edges:
                child_id = edge.related_id
                child = self.session.get(Item, child_id)
                if child is None:
                    errors.append(
                        {
                            "code": "child_missing",
                            "item_id": item_id,
                            "child_id": child_id,
                            "relationship_id": edge.id,
                            "path": list(path),
                            "message": (
                                "Assembly child is missing "
                                f"(dangling ASSEMBLY edge {edge.id} -> {child_id})"
                            ),
                        }
                    )
                    continue
                if child_id in path:
                    errors.append(
                        {
                            "code": "cycle_detected",
                            "item_id": child_id,
                            "relationship_id": edge.id,
                            "path": path + [child_id],
                            "message": "ASSEMBLY cycle detected",
                        }
                    )
                    continue

                parent_to_children[item_id].add(child_id)
                items[child_id] = child
                next_depth = depth + 1
                next_path = path + [child_id]
                next_rel_path = rel_path + [edge.id]
                meta = path_meta.get(child_id)
                if meta is None:
                    path_meta[child_id] = {
                        "min_depth": next_depth,
                        "max_depth": next_depth,
                        "first_path": list(next_path),
                        "first_relationship_path": list(next_rel_path),
                    }
                else:
                    meta["max_depth"] = max(meta["max_depth"], next_depth)
                    if next_depth < meta["min_depth"]:
                        meta["min_depth"] = next_depth
                        meta["first_path"] = list(next_path)
                        meta["first_relationship_path"] = list(next_rel_path)
                queue.append((child_id, next_depth, next_path, next_rel_path))
        return items, parent_to_children, path_meta, errors

    def _assembly_edges(self, item_id: str) -> List[Item]:
        return (
            self.session.query(Item)
            .filter(
                Item.source_id == item_id,
                Item.item_type_id == ASSEMBLY,
                Item.is_current.is_(True),
            )
            .order_by(Item.created_at.asc(), Item.id.asc())
            .all()
        )

    def _topological_child_first(
        self, item_ids: Iterable[str], parent_to_children: Dict[str, Set[str]]
    ) -> List[str]:
        ids = set(item_ids)
        child_to_parents: Dict[str, Set[str]] = {item_id: set() for item_id in ids}
        child_count: Dict[str, int] = {item_id: 0 for item_id in ids}
        for parent, children in parent_to_children.items():
            for child in children:
                if parent in ids and child in ids:
                    child_to_parents[child].add(parent)
                    child_count[parent] += 1

        ready = deque(sorted(item_id for item_id, count in child_count.items() if count == 0))
        order: List[str] = []
        while ready:
            child = ready.popleft()
            order.append(child)
            for parent in sorted(child_to_parents.get(child, ())):
                child_count[parent] -= 1
                if child_count[parent] == 0:
                    ready.append(parent)
        if len(order) != len(ids):
            # Cycles are reported during traversal. Fall back deterministically for
            # dry-run diagnostics if an unexpected cyclic dependency survived.
            order.extend(sorted(ids - set(order)))
        return order

    def _has_promote_permission(
        self,
        item: Item,
        *,
        user_id: int | str,
        user_roles: List[str],
        permission_checker: Optional[PermissionChecker],
    ) -> bool:
        if permission_checker is not None:
            return bool(permission_checker(item))
        return MetaPermissionService(self.session).check_permission(
            item.item_type_id,
            AMLAction.promote,
            user_id=str(user_id),
            user_roles=user_roles,
            item_state=item.state,
            item_owner_id=str(item.created_by_id) if item.created_by_id else None,
            permission_id=item.permission_id,
        )

    def _transition_error(self, item: Item, target_state: str) -> Optional[str]:
        item_type = self.session.get(ItemType, item.item_type_id)
        if not item_type or not item_type.lifecycle_map_id:
            return "lifecycle_map_missing"
        lifecycle_map = self.session.get(LifecycleMap, item_type.lifecycle_map_id)
        if lifecycle_map is None:
            return "lifecycle_map_missing"
        current_state = None
        if item.current_state:
            current_state = self.session.get(LifecycleState, item.current_state)
        if not current_state or current_state.name != item.state:
            current_state = (
                self.session.query(LifecycleState)
                .filter(
                    LifecycleState.lifecycle_map_id == lifecycle_map.id,
                    LifecycleState.name == item.state,
                )
                .first()
            )
        if current_state is None:
            return "current_state_missing"
        target = (
            self.session.query(LifecycleState)
            .filter(
                LifecycleState.lifecycle_map_id == lifecycle_map.id,
                LifecycleState.name == target_state,
            )
            .first()
        )
        if target is None:
            return "target_state_missing"
        transition = (
            self.session.query(LifecycleTransition)
            .filter(
                LifecycleTransition.lifecycle_map_id == lifecycle_map.id,
                LifecycleTransition.from_state_id == current_state.id,
                LifecycleTransition.to_state_id == target.id,
            )
            .first()
        )
        if transition is None:
            return "transition_missing"
        return None

    def _response(
        self,
        *,
        root_id: str,
        target_state: str,
        dry_run: bool,
        plan: List[Dict[str, Any]],
        errors: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        summary = {"promote": 0, "skip_already_released": 0, "blocked": 0}
        for row in plan:
            summary[row["action"]] = summary.get(row["action"], 0) + 1
        return {
            "root_id": root_id,
            "target_state": target_state,
            "dry_run": dry_run,
            "ok": not errors and summary.get("blocked", 0) == 0,
            "summary": summary,
            "plan": plan,
            "errors": errors,
        }
