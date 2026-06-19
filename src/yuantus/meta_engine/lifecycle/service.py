from typing import Optional
from .models import LifecycleMap, LifecycleState, LifecycleTransition
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
                return PromoteResult(success=False, error="User not found.")

            # Superuser bypass, else any of the user's roles must satisfy (be, or inherit
            # from) the allowed role — the whole decision is in _user_allowed_for_transition.
            if not self._user_allowed_for_transition(user, transition_obj):
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
                return PromoteResult(success=False, error="; ".join(child_errors))

        # 4. 执行 before_transition hooks
        context = self.hook_registry.execute(
            item.item_type_id,  # item_type_id is the string name like "Part"
            HookType.BEFORE_TRANSITION,
            context,
        )
        if context.abort:
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
                return PromoteResult(
                    success=False, error="Transition condition not met."
                )

        # 6. 执行 on_exit_state hooks
        context = self.hook_registry.execute(
            item.item_type_id, HookType.ON_EXIT_STATE, context
        )
        if context.abort:
            return PromoteResult(success=False, error=context.abort_reason)

        # --- 执行状态变更 ---
        old_state_id = item.current_state
        old_state_name = item.state

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
            # item.permission_id = ... (restore old permission if necessary)
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
                return PromoteResult(
                    success=False, error=f"Version release failed: {str(e)}"
                )

        # 9. 记录历史 (Placeholder - actual history logging to a table or event stream)
        # self._record_history(item, current_state_obj, target_state_obj, user_id, comment)

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
