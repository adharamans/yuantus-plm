from typing import Optional, Sequence
from .models import (
    LifecycleMap,
    LifecycleState,
    LifecycleTransition,
    LifecycleTransitionHistory,
)
from ..models.item import Item
from ..models.meta_schema import ItemType
from dataclasses import dataclass
import json
import logging

# from yuantus.security.permission_service import PermissionService # Removed to avoid IrRule dependency
from yuantus.meta_engine.lifecycle.hooks import hook_registry, HookContext, HookType
from yuantus.meta_engine.lifecycle.condition_evaluator import ConditionEvaluator

logger = logging.getLogger(__name__)


@dataclass
class PromoteResult:
    """提升结果"""

    success: bool
    error: str = ""
    from_state: Optional[str] = None
    to_state: Optional[str] = None


class LifecycleService:
    def __init__(self, session):
        self.session = session
        self.hook_registry = hook_registry
        self.condition_evaluator = ConditionEvaluator(session)
        # self.permission_service = PermissionService(session)

    def _get_lifecycle_map(self, item_type: ItemType) -> Optional[LifecycleMap]:
        if not item_type or not item_type.lifecycle_map_id:
            return None
        # Prefer loaded relationship to avoid extra round-trips
        if getattr(item_type, "lifecycle_map", None):
            return item_type.lifecycle_map
        return (
            self.session.query(LifecycleMap)
            .filter(LifecycleMap.id == item_type.lifecycle_map_id)
            .first()
        )

    @staticmethod
    def _role_satisfies(role, allowed_role_id) -> bool:
        """True if ``role`` is the transition's allowed role or inherits from it — i.e.
        ``allowed_role_id`` is ``role`` itself or one of its ancestors up the ``parent``
        chain. Mirrors ``RBACRole.has_permission`` (which walks UP to the parent: a role
        inherits its parent's rights), so a descendant of the allowed role inherits the
        transition right. Iterative + cycle-safe against a malformed parent loop.
        """
        seen: set = set()
        current = role
        while current is not None and getattr(current, "id", None) not in seen:
            if current.id == allowed_role_id:
                return True
            seen.add(current.id)
            current = getattr(current, "parent", None)
        return False

    @staticmethod
    def _user_allowed_for_transition(user, transition_obj) -> bool:
        """True if ``user`` may perform the transition's role-gated step. A transition with
        no ``role_allowed_id`` is unrestricted; a superuser always passes; otherwise any of
        the user's roles must satisfy (be, or inherit from) the allowed role. This is the
        gate's whole role decision, factored out so it is behaviorally testable without the
        full promote()/session machinery.
        """
        if not getattr(transition_obj, "role_allowed_id", None):
            return True
        if getattr(user, "is_superuser", False):
            return True
        return any(
            LifecycleService._role_satisfies(role, transition_obj.role_allowed_id)
            for role in (getattr(user, "roles", None) or [])
        )

    def promote(
        self,
        item: Item,
        target_state_name: str,
        user_id: int,  # Changed from user_roles to user_id for permission_service
        comment: str = "",
        force: bool = False,
    ) -> PromoteResult:
        """
        执行状态提升 (Phase 2.3)

        Args:
            item: 目标Item
            target_state_name: 目标状态名
            user_id: 操作用户ID
            comment: 提升备注
            force: 强制提升（跳过条件检查）

        Returns:
            PromoteResult包含成功/失败信息
        """
        item_type_obj = self.session.get(ItemType, item.item_type_id)
        lifecycle_map = (
            self._get_lifecycle_map(item_type_obj) if item_type_obj else None
        )
        if not lifecycle_map:
            return PromoteResult(
                success=False,
                error="Lifecycle map is not configured for this item type.",
            )

        current_state_obj = None
        if item.current_state:
            current_state_obj = self.session.get(LifecycleState, item.current_state)

        # Fallback to item.state if current_state_obj is None or inconsistent
        if not current_state_obj or current_state_obj.name != item.state:
            current_state_obj = (
                self.session.query(LifecycleState)
                .filter(
                    LifecycleState.lifecycle_map_id == lifecycle_map.id,
                    LifecycleState.name == item.state,
                )
                .first()
            )

        if not current_state_obj:
            return PromoteResult(
                success=False,
                error=f"Current lifecycle state '{item.state}' not found in map '{lifecycle_map.name}'.",
            )

        target_state_obj = (
            self.session.query(LifecycleState)
            .filter(
                LifecycleState.lifecycle_map_id == lifecycle_map.id,
                LifecycleState.name == target_state_name,
            )
            .first()
        )
        if not target_state_obj:
            self._record_transition_attempt(
                outcome="denied", reason_code="target_state_not_found", item=item,
                actor_user_id=user_id, from_state=current_state_obj, to_state_name=target_state_name,
                lifecycle_map=lifecycle_map, from_permission_id=getattr(item, "permission_id", None),
            )
            return PromoteResult(
                success=False,
                error=f"Target lifecycle state '{target_state_name}' not found in map '{lifecycle_map.name}'.",
            )

        transition_obj = (
            self.session.query(LifecycleTransition)
            .filter(
                LifecycleTransition.lifecycle_map_id == lifecycle_map.id,
                LifecycleTransition.from_state_id == current_state_obj.id,
                LifecycleTransition.to_state_id == target_state_obj.id,
            )
            .first()
        )

        if not transition_obj:
            self._record_transition_attempt(
                outcome="denied", reason_code="transition_missing", item=item, actor_user_id=user_id,
                from_state=current_state_obj, to_state=target_state_obj, lifecycle_map=lifecycle_map,
                from_permission_id=getattr(item, "permission_id", None),
            )
            return PromoteResult(
                success=False,
                error=f"No valid transition found from '{current_state_obj.name}' to '{target_state_name}'.",
            )

        # 2. 构建Hook上下文
        context = HookContext(
            item=item,
            user_id=user_id,
            from_state=current_state_obj,
            to_state=target_state_obj,
            transition=transition_obj,
            extra_data={"comment": comment},
        )

        # 3. 权限检查 (Native RBAC Check for Transition)
        # Check if transition requires a specific role
        if transition_obj.role_allowed_id:
            from yuantus.security.rbac.models import RBACUser  # Lazy import

            user = self.session.get(RBACUser, user_id)
            if not user:
                self._record_transition_attempt(
                    outcome="denied", reason_code="actor_missing", item=item, actor_user_id=user_id,
                    from_state=current_state_obj, to_state=target_state_obj, transition=transition_obj,
                    lifecycle_map=lifecycle_map, from_permission_id=getattr(item, "permission_id", None),
                )
                return PromoteResult(success=False, error="User not found.")

            # Superuser bypass, else any of the user's roles must satisfy (be, or inherit
            # from) the allowed role — the whole decision is in _user_allowed_for_transition.
            if not self._user_allowed_for_transition(user, transition_obj):
                self._record_transition_attempt(
                    outcome="denied", reason_code="permission_denied", item=item, actor_user_id=user_id,
                    from_state=current_state_obj, to_state=target_state_obj, transition=transition_obj,
                    lifecycle_map=lifecycle_map, from_permission_id=getattr(item, "permission_id", None),
                )
                return PromoteResult(
                    success=False,
                    error=f"Permission denied. Role requirement not met for transition '{transition_obj.id}'.",
                )

        # Note: We removed the generic self.permission_service.check_permission() call
        # because it drags in legacy dependencies (IrRule) not present in the Meta-Engine kernel.
        # AMLEngine already performs a robust 'promote' action permission check before calling this.

        # 3.5 B2 assembly release hard gate (precondition): a parent entering
        # "Released" must not reference unreleased direct ASSEMBLY children (the
        # WP1.2 CAD product structure). Runs BEFORE any state mutation / hooks /
        # workflow start below, so a blocked release fires no "entered Released"
        # side effects and needs no rollback. Keyed on the same name=="Released"
        # convention as the version-release block, the release hook, and the
        # workflow service (the seeded released state is named "Released").
        if target_state_obj.name == "Released":
            from yuantus.meta_engine.services.item_release_service import (
                ItemReleaseService,
            )

            child_errors = ItemReleaseService(self.session).assert_children_released(
                item.id
            )
            if child_errors:
                self._record_transition_attempt(
                    outcome="blocked", reason_code="assembly_release_blocked", item=item,
                    actor_user_id=user_id, from_state=current_state_obj, to_state=target_state_obj,
                    transition=transition_obj, lifecycle_map=lifecycle_map,
                    from_permission_id=getattr(item, "permission_id", None),
                    public_message=f"{len(child_errors)} unreleased assembly child(ren)",
                )
                return PromoteResult(success=False, error="; ".join(child_errors))

        # 4. 执行 before_transition hooks
        context = self.hook_registry.execute(
            item.item_type_id,  # item_type_id is the string name like "Part"
            HookType.BEFORE_TRANSITION,
            context,
        )
        if context.abort:
            self._record_transition_attempt(
                outcome="aborted", reason_code="before_transition_aborted", item=item,
                actor_user_id=user_id, from_state=current_state_obj, to_state=target_state_obj,
                transition=transition_obj, lifecycle_map=lifecycle_map,
                from_permission_id=getattr(item, "permission_id", None),
            )
            return PromoteResult(success=False, error=context.abort_reason)

        # 5. 条件检查
        if (
            not force and transition_obj.condition
        ):  # Assuming Condition field exists on LifecycleTransition
            try:
                condition_json = json.loads(
                    transition_obj.condition
                )  # Condition is stored as JSON string
            except (json.JSONDecodeError, TypeError):
                logger.error(
                    f"Invalid condition JSON for transition {transition_obj.id}: {transition_obj.condition}"
                )
                condition_json = {}

            if not self.condition_evaluator.evaluate(
                condition_json, item, user_id, context.extra_data
            ):
                # 执行失败Hook
                context = self.hook_registry.execute(
                    item.item_type_id, HookType.ON_PROMOTE_FAIL, context
                )
                self._record_transition_attempt(
                    outcome="aborted", reason_code="condition_failed", item=item,
                    actor_user_id=user_id, from_state=current_state_obj, to_state=target_state_obj,
                    transition=transition_obj, lifecycle_map=lifecycle_map,
                    from_permission_id=getattr(item, "permission_id", None),
                )
                return PromoteResult(
                    success=False, error="Transition condition not met."
                )

        # 6. 执行 on_exit_state hooks
        context = self.hook_registry.execute(
            item.item_type_id, HookType.ON_EXIT_STATE, context
        )
        if context.abort:
            self._record_transition_attempt(
                outcome="aborted", reason_code="on_exit_aborted", item=item, actor_user_id=user_id,
                from_state=current_state_obj, to_state=target_state_obj, transition=transition_obj,
                lifecycle_map=lifecycle_map, from_permission_id=getattr(item, "permission_id", None),
            )
            return PromoteResult(success=False, error=context.abort_reason)

        # --- 执行状态变更 ---
        old_state_id = item.current_state
        old_state_name = item.state
        # capture the pre-transition permission so a rolled-back transition also restores
        # permission (state-driven permission is set below; on failure it must be undone
        # together with state, else a stale permission can be committed by a caller).
        old_permission_id = item.permission_id

        item.state = target_state_obj.name
        item.current_state = target_state_obj.id
        item.modified_by_id = user_id  # Using newly added audit field
        # Note: item.modified_on is handled by SQLAlchemy's onupdate

        # 更新权限 (状态驱动权限)
        if (
            target_state_obj.default_permission_id
        ):  # Using new default_permission_id field from LifecycleState
            item.permission_id = target_state_obj.default_permission_id
        elif (
            target_state_obj.permission_id
        ):  # Fallback to old permission_id if new one not set
            item.permission_id = target_state_obj.permission_id

        # 7. 执行 on_enter_state hooks
        context = self.hook_registry.execute(
            item.item_type_id, HookType.ON_ENTER_STATE, context
        )
        if context.abort:
            # 回滚状态变更 (in-memory, requires session rollback or explicit setting if commit not yet done)
            item.state = old_state_name
            item.current_state = old_state_id
            item.permission_id = old_permission_id
            self._record_transition_attempt(
                outcome="aborted", reason_code="on_enter_aborted", item=item, actor_user_id=user_id,
                from_state=current_state_obj, to_state=target_state_obj, transition=transition_obj,
                lifecycle_map=lifecycle_map, from_permission_id=old_permission_id, rolled_back=True,
            )
            return PromoteResult(success=False, error=context.abort_reason)

        # 7.5 Workflow Integration (Phase 2.5)
        # Check if new state has a linked workflow
        if target_state_obj.workflow_map_id:
            try:
                from yuantus.meta_engine.workflow.service import WorkflowService
                from yuantus.meta_engine.workflow.models import WorkflowMap

                # Get Map Name (WorkflowService currently expects Name)
                wf_map = self.session.get(WorkflowMap, target_state_obj.workflow_map_id)
                if wf_map:
                    wf_svc = WorkflowService(self.session)
                    wf_svc.start_workflow(item.id, wf_map.name, user_id)
                    logger.info(
                        f"Auto-started workflow '{wf_map.name}' for item {item.id}"
                    )
                else:
                    logger.warning(
                        f"State {target_state_obj.name} points to missing Workflow Map ID {target_state_obj.workflow_map_id}"
                    )

            except Exception as e:
                # Log error but don't blocking promotion? Or block?
                # Usually workflow failure should block state entry.
                logger.error(f"Failed to start workflow: {e}")
                item.state = old_state_name
                item.current_state = old_state_id
                item.permission_id = old_permission_id
                self._record_transition_attempt(
                    outcome="failed", reason_code="workflow_start_failed", item=item,
                    actor_user_id=user_id, from_state=current_state_obj, to_state=target_state_obj,
                    transition=transition_obj, lifecycle_map=lifecycle_map,
                    from_permission_id=old_permission_id, rolled_back=True,
                    public_message="workflow start failed",
                )
                return PromoteResult(
                    success=False, error=f"Failed to start workflow: {str(e)}"
                )

        # 8. Version Control Integration (Phase 5)
        # If entering 'Released' state, mark version as Released
        if target_state_obj.name == "Released" and getattr(
            item, "is_versionable", False
        ):
            try:
                from yuantus.meta_engine.version.service import VersionService

                ver_svc = VersionService(self.session)
                if not item.current_version_id:
                    ver_svc.create_initial_version(item, user_id)
                ver_svc.release(item.id, user_id)
            except Exception as e:
                logger.error(f"Failed to release version: {e}")
                item.state = old_state_name
                item.current_state = old_state_id
                item.permission_id = old_permission_id
                self._record_transition_attempt(
                    outcome="failed", reason_code="version_release_failed", item=item,
                    actor_user_id=user_id, from_state=current_state_obj, to_state=target_state_obj,
                    transition=transition_obj, lifecycle_map=lifecycle_map,
                    from_permission_id=old_permission_id, rolled_back=True,
                    public_message="version release failed",
                )
                return PromoteResult(
                    success=False, error=f"Version release failed: {str(e)}"
                )

        # 9. 记录历史 — durable audit row for this successful transition. Written here, after
        # all three rollback returns, so only committed transitions are recorded; best-effort
        # (a write failure is logged, never fails the transition).
        self._record_transition_history(
            item, current_state_obj, target_state_obj, transition_obj,
            user_id, comment, old_permission_id,
        )

        # 9. 执行 after_transition hooks
        context = self.hook_registry.execute(
            item.item_type_id, HookType.AFTER_TRANSITION, context
        )

        # The service itself does not commit. The caller of the service should manage the session and commit.
        # This keeps the service composable within a larger transaction.
        # self.session.commit() # DO NOT COMMIT HERE

        return PromoteResult(
            success=True,
            from_state=current_state_obj.name,
            to_state=target_state_obj.name,
        )

    def get_transition_history(
        self,
        item_id: str,
        *,
        limit: Optional[int] = None,
        success_only: bool = False,
        outcomes: Optional[Sequence[str]] = None,
        reason_codes: Optional[Sequence[str]] = None,
    ):
        """Return an item's lifecycle transition-history rows, most-recent first.

        Read surface (Slice 2) for the audit table written by ``promote()``. Ordered by
        ``created_at`` descending, with an ``id`` tiebreak; optional ``limit``. Does NOT check
        item existence — the route does that.

        ``success_only=True`` returns only ``outcome == "success"`` rows — the **item-scoped**
        read surface, which must NOT leak failed/denied/blocked/aborted attempts (those are a
        forensic-tier signal). The **forensic** (superuser) route passes ``success_only=False``
        to see every outcome.
        """
        query = self.session.query(LifecycleTransitionHistory).filter(
            LifecycleTransitionHistory.item_id == item_id
        )
        if success_only:
            query = query.filter(LifecycleTransitionHistory.outcome == "success")
        if outcomes:
            # forensic-route filter: restrict to the requested outcome set (e.g. denied/blocked
            # for failed-attempt triage). AND-combines with success_only, though no route passes
            # both. SQL-level IN keeps it portable (sqlite/postgres) and respects limit ordering.
            query = query.filter(LifecycleTransitionHistory.outcome.in_(tuple(outcomes)))
        if reason_codes:
            # forensic-route filter: restrict to rows whose properties JSON ``reason_code`` is in
            # the requested set (e.g. ?reason_code=permission_denied for cause-of-denial triage).
            # ANY string is accepted — unknown codes simply match nothing (no whitelist, no 400),
            # so the surface stays robust as the reason-code vocabulary grows. ``.as_string()`` maps
            # to a portable JSON text-extract (JSON_EXTRACT on sqlite, ->> on postgres); a WHERE
            # clause so it composes with order_by + limit below.
            query = query.filter(
                LifecycleTransitionHistory.properties["reason_code"].as_string().in_(
                    tuple(reason_codes)
                )
            )
        query = query.order_by(
            LifecycleTransitionHistory.created_at.desc(),
            LifecycleTransitionHistory.id.desc(),
        )
        if limit is not None:
            query = query.limit(limit)
        return query.all()

    def _record_transition_history(
        self, item, from_state, to_state, transition, actor_user_id, comment, old_permission_id
    ) -> None:
        """Best-effort audit of a successful transition (Slice 1).

        The history INSERT runs inside a SAVEPOINT (``begin_nested``) so its failure rolls back
        only the audit row, never poisons the session, and never breaks the caller's commit.

        IMPORTANT: the already-applied business state is flushed FIRST, *outside* the audit
        guard. ``flush()`` (and ``begin_nested()``'s own pre-flush) flush the WHOLE pending set
        — outer business state included — so doing the business flush inside the try/except
        would let a genuine business flush error be mislabeled "history write failed" and
        swallowed, leaving the session rollback-pending under a success return. Flushing it
        outside lets its error propagate normally and scopes best-effort to the history row
        only. Default-on, gated by ``LIFECYCLE_TRANSITION_HISTORY_ENABLED``.
        """
        from yuantus.config import get_settings  # lazy: avoid an import cycle at module load

        if not getattr(get_settings(), "LIFECYCLE_TRANSITION_HISTORY_ENABLED", True):
            return
        # Flush the (already-applied) business state OUTSIDE the audit guard: a real business
        # flush error must propagate, not be swallowed as a history-write failure.
        self.session.flush()
        try:
            with self.session.begin_nested():
                self.session.add(
                    LifecycleTransitionHistory(
                        item_id=item.id,
                        from_state_id=getattr(from_state, "id", None),
                        from_state_name=getattr(from_state, "name", None),
                        to_state_id=getattr(to_state, "id", None),
                        to_state_name=getattr(to_state, "name", None),
                        from_permission_id=old_permission_id,
                        to_permission_id=getattr(item, "permission_id", None),
                        transition_id=getattr(transition, "id", None),
                        lifecycle_map_id=getattr(transition, "lifecycle_map_id", None),
                        actor_user_id=actor_user_id,
                        comment=comment or None,
                        outcome="success",
                    )
                )
                self.session.flush()  # surface any DB error inside the savepoint
        except Exception as exc:  # best-effort: an audit write must never break the promote
            logger.warning(
                "transition-history write failed for item %s: %s",
                getattr(item, "id", "?"),
                exc,
            )

    def _record_transition_attempt(
        self,
        *,
        outcome: str,
        reason_code: str,
        item,
        actor_user_id,
        from_state=None,
        to_state=None,
        transition=None,
        lifecycle_map=None,
        to_state_name: Optional[str] = None,
        from_permission_id=None,
        to_permission_id=None,
        public_message: Optional[str] = None,
        rolled_back: bool = False,
    ) -> None:
        """Best-effort audit of a FAILED / denied / blocked / aborted ``promote()`` attempt.

        Unlike the success write (same-session ``begin_nested``), this writes through a SEPARATE
        ``get_db_session()`` that commits independently, so the row SURVIVES the caller rolling
        back the failed attempt: ``operations/promote_op.py`` raises ``ValidationError`` on a
        failed ``PromoteResult``, so the AML apply transaction never commits — a same-session
        attempt row would vanish. ``get_db_session`` is tenant-aware (it binds the tenant schema
        via its ``after_begin`` ``SET LOCAL search_path`` listener), so the row lands in the right
        schema. The history table's reference columns (``item_id`` / ``from_state_id`` /
        ``transition_id`` / ``lifecycle_map_id`` ...) are all **FK-free**, so this independent
        INSERT can never block on an FK the caller's uncommitted transaction is holding.

        Best-effort: never touches / flushes / commits ``self.session``; never raises; never
        changes the ``PromoteResult`` (the attempt already failed). With no tenant context (a
        non-request caller) ``get_db_session`` raises and we swallow it — no row, promote
        unaffected. Default-on, gated by ``LIFECYCLE_TRANSITION_HISTORY_ENABLED``. Records only a
        bounded ``reason_code`` + optional sanitized ``public_message`` — never a raw exception.
        """
        from yuantus.config import get_settings  # lazy: avoid an import cycle at module load

        if not getattr(get_settings(), "LIFECYCLE_TRANSITION_HISTORY_ENABLED", True):
            return

        # Read plain values now, while the ORM objects are live on self.session; the separate
        # session is handed values only, never an object bound to another session.
        properties = {"reason_code": reason_code}
        if public_message:
            properties["public_message"] = public_message
        if rolled_back:
            properties["rolled_back"] = True
        values = dict(
            item_id=getattr(item, "id", None),
            from_state_id=getattr(from_state, "id", None),
            from_state_name=getattr(from_state, "name", None),
            to_state_id=getattr(to_state, "id", None),
            to_state_name=to_state_name or getattr(to_state, "name", None),
            from_permission_id=from_permission_id,
            to_permission_id=to_permission_id,
            transition_id=getattr(transition, "id", None),
            lifecycle_map_id=getattr(transition, "lifecycle_map_id", None)
            or getattr(lifecycle_map, "id", None),
            actor_user_id=actor_user_id,
            outcome=outcome,
            properties=properties,
        )
        try:
            from yuantus.database import get_db_session

            with get_db_session() as audit_session:
                audit_session.add(LifecycleTransitionHistory(**values))
                audit_session.commit()
        except Exception as exc:  # best-effort: an attempt audit must never break the promote
            logger.warning(
                "transition-attempt audit failed for item %s (outcome=%s): %s",
                values["item_id"],
                outcome,
                exc,
            )

    def attach_lifecycle(self, item_type: ItemType, item: Item):
        """
        在 Item 创建时调用 (Action=Add)，设置初始状态
        """
        lifecycle_map = self._get_lifecycle_map(item_type)
        if not lifecycle_map:
            return

        start_state = (
            self.session.query(LifecycleState)
            .filter(
                LifecycleState.lifecycle_map_id == lifecycle_map.id,
                LifecycleState.is_start_state.is_(True),
            )
            .first()
        )
        if not start_state:
            return

        item.state = start_state.name
        item.current_state = start_state.id
        if start_state.default_permission_id:  # Prefer new default_permission_id
            item.permission_id = start_state.default_permission_id
        elif start_state.permission_id:  # Fallback
            item.permission_id = start_state.permission_id
