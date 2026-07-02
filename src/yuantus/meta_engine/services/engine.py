import json
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from jsonschema import (
    validate,
    ValidationError as JSONSchemaValidationError,
)  # Import jsonschema

from yuantus.meta_engine.events.event_bus import event_bus

from yuantus.exceptions.handlers import PermissionError, ValidationError
from ..lifecycle.service import LifecycleService  # Import LifecycleService
from ..business_logic.executor import MethodExecutor
from ..models.item import Item
from ..models.meta_schema import ItemType, Property
from ..schemas.aml import AMLAction, GenericItem
from .meta_permission_service import MetaPermissionService
from .bom_service import BOMService
from .job_service import JobService
from .checkin_service import CheckinService
from .report_service import ReportService
from .cad_service import CadService
from .search_service import SearchService
from ..web.rpc_registry import rpc_exposed


class AMLEngine:
    def __init__(
        self,
        session: Session,
        *,
        identity_id: Optional[str] = None,
        roles: Optional[List[str]] = None,
    ):
        self.session = session
        self.user_id = str(identity_id) if identity_id else "guest"
        self.roles = roles or ["guest"]

        self.permission_service = MetaPermissionService(session)
        self.lifecycle = LifecycleService(session)  # Re-initialize LifecycleService
        self.method_executor = MethodExecutor(session)
        # Initialize Validator
        from .validator import MetaValidator
        self.validator = MetaValidator()

    # ----------------------------------------------------
    #  RPC Methods
    # ----------------------------------------------------
    @rpc_exposed("Item", "create")
    def rpc_create(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        RPC wrapper for add.
        args: [GenericItem_dict] or kwargs: {item: ...}
        """
        item_data = args[0] if args else kwargs.get("item")
        if not item_data:
            raise ValueError("Missing 'item' data for create")

        # Convert dict to Pydantic if needed, or _do_add usage?
        # apply() takes GenericItem.
        # Let's support passing raw dict.
        if isinstance(item_data, dict):
            # Ensure mandatory fields
            if "type" not in item_data:
                raise ValueError("Item 'type' required")
            aml_item = GenericItem(
                type=item_data["type"],
                action=AMLAction.add,
                properties=item_data.get("properties"),
                relationships=item_data.get(
                    "relationships"
                ),  # Nested relationships handled by apply()
            )
        else:
            # Assume it's already an object? Unlikely via JSON RPC.
            raise ValueError("Item data must be a dictionary")

        return self.apply(aml_item)

    @rpc_exposed("Item", "write")
    def rpc_write(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        RPC wrapper for update.
        args: [id, data_dict] or kwargs: {id:..., data:...}
        """
        item_id = args[0] if len(args) > 0 else kwargs.get("id")
        data = args[1] if len(args) > 1 else kwargs.get("data")

        if not item_id or not data:
            raise ValueError("Missing 'id' or 'data' for write")

        # We need to know the 'type' to call apply(). Or do we lookup item first?
        # AMLEngine.apply() requires GenericItem with type.
        # But for 'update', usually we know ID.
        # The current design of _do_update() expects 'id' AND 'type' to perform a check.
        # Let's fetch the item first to get its type?
        # No, that's inefficient.
        # The Client SHOULD pass 'model' context if possible, but here 'model' in RPC is "Item".
        # Let's lookup the item to get the type.

        item = self.session.get(Item, item_id)
        if not item:
            raise ValueError(f"Item {item_id} not found")

        aml_item = GenericItem(
            id=item_id,
            type=item.item_type_id,  # Use existing type
            action=AMLAction.update,
            properties=data,
        )
        return self.apply(aml_item)

    @rpc_exposed("Item", "search")
    def rpc_search(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        RPC wrapper for get/search.
        args: [domain, fields] (Odoo style) -> we support simple property filters for now.
        domain: [['cost', '>', 50], ...]
        """
        domain = args[0] if args else kwargs.get("domain", [])
        # Ignore fields for now, return full object or standard view

        # We need a 'type' context from domain?
        # Usually RPC search is on a Model like 'Part'.
        # But here our RPC Model is 'Item'.
        # We expect one domain tuple to be ['type', '=', 'Part'].

        type_filter = None
        props = {}

        for leaf in domain:
            if len(leaf) != 3:
                continue
            field, op, val = leaf
            if field == "type" or field == "item_type_id":
                if op == "=":
                    type_filter = val
            else:
                # Assume property equality for MVP
                if op == "=":
                    props[field] = val

        if not type_filter:
            raise ValueError("Search domain must include ['type', '=', '...']")

        fields = args[1] if len(args) > 1 else kwargs.get("fields")

        aml_item = GenericItem(
            type=type_filter,
            action=AMLAction.get,
            properties=props,
            # We need to hack/extend GenericItem or pass explicit context?
            # GenericItem has no 'fields' slot.
            # But the _do_get method can check a special property?
            # Or we subclass GenericItem?
            # Let's check aml.py schema first.
        )

        # Inject fields into a temporary carrier if not supported by GenericItem
        # Actually, let's look at _do_get. It receives 'aml'.
        # We can attach '_fields' to aml object dynamically if it's a Pydantic model with extra=allow
        # aml.fields = fields
        # But wait, python dynamic attr on Pydantic v2 might fail if frozen.
        # Let's pass it via kwargs to apply if possible? apply takes only aml.

        # Pragmatic approach: Use a special key in properties that _do_get pops?
        # Or extend GenericItem definition?
        # Let's see schemas/aml.py for a moment.
        # Assuming current context, I will just attach it and hope schema allows extra.
        # If not, I'll update schema.
        if fields:
            setattr(aml_item, "_fields", fields)

        return self.apply(aml_item)

    @rpc_exposed("BOM", "get_structure")
    def rpc_get_bom_structure(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        RPC wrapper for BOMService.get_bom_structure.
        args: [item_id] or kwargs: {item_id: ...}
        """
        item_id = args[0] if args else kwargs.get("item_id") or kwargs.get("id")
        if not item_id:
            raise ValueError("Missing 'item_id' for BOM structure")

        include_substitutes = kwargs.get("include_substitutes", False)

        return BOMService(self.session).get_bom_structure(
            item_id, include_substitutes=include_substitutes
        )

    @rpc_exposed("Relationship", "add")
    def rpc_relationship_add(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Add a relationship (e.g. BOM row).
        args: [parent_id, relationship_type, related_id, properties]
        """
        parent_id = args[0] if args else kwargs.get("parent_id")
        rel_type = args[1] if len(args) > 1 else kwargs.get("type")
        related_id = args[2] if len(args) > 2 else kwargs.get("related_id")
        properties = args[3] if len(args) > 3 else kwargs.get("properties", {})

        if not parent_id or not rel_type or not related_id:
            raise ValueError("Missing arguments for relationship add")

        parent = self.session.get(Item, parent_id)
        if not parent:
            raise ValueError("Parent item not found")

        aml_rel = GenericItem(
            type=rel_type,  # e.g. "Part BOM"
            action=AMLAction.add,
            properties={"related_id": related_id, **properties},
        )

        # Use internal engine method to handle relationship logic
        self.apply_relationship(aml_rel, source_item=parent)
        return {"status": "success"}

    @rpc_exposed("Relationship", "remove")
    def rpc_relationship_remove(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Remove a relationship row.
        args: [relationship_id]
        """
        # This one is tricky: we need the ID of the relationship ROW (the Item in 'meta_items' table).
        # OR we delete by source/target query?
        # Standard PLM: delete the relationship Item ID.
        rel_id = args[0] if args else kwargs.get("id")
        if not rel_id:
            raise ValueError("Missing relationship id")

        # We need to know IT's a relationship type, but delete just needs ID.
        # But _do_delete needs item_type.

        rel_item = self.session.get(Item, rel_id)
        if not rel_item:
            raise ValueError("Relationship item not found")

        aml = GenericItem(
            id=rel_id, type=rel_item.item_type_id, action=AMLAction.delete
        )
        return self.apply(aml)

    @rpc_exposed("Method", "run")
    def rpc_run_method(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Execute server-side method via RPC.
        args: [method_name, context_dict]
        """
        method_name = args[0] if args else kwargs.get("name")
        context = args[1] if len(args) > 1 else kwargs.get("context", {})

        if not method_name:
            raise ValueError("Missing 'name'")

        # P0-8a D4: RPC Method execution is fail-closed. It is refused unless
        # explicitly enabled AND the caller holds an admin/superuser role.
        # (This does NOT fix the RPC-wide forged-admin default in rpc_router;
        # that is a separately-gated adjacent slice. With this flag off by
        # default, Method execution stays closed even while that stands.)
        from yuantus.config.settings import get_settings

        if not get_settings().METHOD_RPC_ENABLED:
            raise PermissionError(
                "RPC Method execution is disabled (set YUANTUS_METHOD_RPC_ENABLED=true)"
            )
        caller_roles = {str(r).lower() for r in (self.roles or [])}
        if not ({"admin", "superuser"} & caller_roles):
            raise PermissionError(
                "RPC Method execution requires an admin/superuser role"
            )

        # Inject current session/user into context
        from yuantus.meta_engine.services.method_service import MethodService

        full_context = context.copy()
        full_context["session"] = self.session
        full_context["user_id"] = self.user_id

        svc = MethodService(self.session)
        return svc.execute_method(method_name, full_context)

    @rpc_exposed("Version", "create_branch")
    def rpc_create_branch(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        RPC wrapper for VersionService.create_branch.
        """
        item_id = args[0] if args else kwargs.get("item_id")
        source_version_id = (
            args[1] if len(args) > 1 else kwargs.get("source_version_id")
        )
        branch_name = args[2] if len(args) > 2 else kwargs.get("branch_name")

        if not item_id or not source_version_id or not branch_name:
            raise ValueError("Missing arguments for create_branch")

        from yuantus.meta_engine.version.service import VersionService

        # User ID from internal state
        user_id_int = 1  # Default
        if self.user_id and str(self.user_id).isdigit():
            user_id_int = int(self.user_id)

        svc = VersionService(self.session)
        ver = svc.create_branch(item_id, source_version_id, branch_name, user_id_int)

        return {
            "id": ver.id,
            "version_label": ver.version_label,
            "branch": ver.branch_name,
        }

    @rpc_exposed("Version", "get_tree")
    def rpc_get_version_tree(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        RPC wrapper for VersionService.get_version_tree.
        args: [item_id]
        """
        item_id = args[0] if args else kwargs.get("item_id")
        if not item_id:
            raise ValueError("Missing 'item_id'")

        from yuantus.meta_engine.version.service import VersionService

        svc = VersionService(self.session)
        return svc.get_version_tree(item_id)

    @rpc_exposed("Workflow", "start_workflow")
    def rpc_workflow_start(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Start a workflow.
        args: [item_id, map_name]
        """
        item_id = args[0] if args else kwargs.get("item_id")
        map_name = args[1] if len(args) > 1 else kwargs.get("map_name")

        if not item_id or not map_name:
            raise ValueError("Missing 'item_id' or 'map_name'")

        from yuantus.meta_engine.workflow.service import WorkflowService

        user_id_int = (
            int(self.user_id) if self.user_id and str(self.user_id).isdigit() else 1
        )

        svc = WorkflowService(self.session)
        proc = svc.start_workflow(item_id, map_name, user_id_int)
        return {"process_id": proc.id, "state": proc.state}

    @rpc_exposed("Workflow", "vote")
    def rpc_workflow_vote(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Vote on a task.
        args: [task_id, outcome, comment]
        """
        task_id = args[0] if args else kwargs.get("task_id")
        outcome = args[1] if len(args) > 1 else kwargs.get("outcome")
        comment = args[2] if len(args) > 2 else kwargs.get("comment")

        if not task_id or not outcome:
            raise ValueError("Missing 'task_id' or 'outcome'")

        from yuantus.meta_engine.workflow.service import WorkflowService

        user_id_int = (
            int(self.user_id) if self.user_id and str(self.user_id).isdigit() else 1
        )

        svc = WorkflowService(self.session)
        success = svc.vote(task_id, outcome, user_id_int, comment)
        return {"success": success}

    @rpc_exposed("Workflow", "get_inbox")
    def rpc_workflow_inbox(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Get pending tasks for current user.
        """
        from yuantus.meta_engine.workflow.service import WorkflowService

        user_id_int = (
            int(self.user_id) if self.user_id and str(self.user_id).isdigit() else 1
        )

        svc = WorkflowService(self.session)
        tasks = svc.get_pending_tasks(user_id_int)
        return tasks

    @rpc_exposed("AppStore", "search")
    def rpc_app_store_search(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Search App Store.
        args: [query]
        """
        query = args[0] if args else kwargs.get("query", "")

        from yuantus.meta_engine.services.store_service import AppStoreService

        svc = AppStoreService(self.session)
        return svc.search_apps(query)

    @rpc_exposed("Schema", "get_definition")
    def rpc_schema_get_definition(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Get JSON Schema for a Type.
        args: [item_type_id]
        """
        type_id = args[0] if args else kwargs.get("item_type_id")
        if not type_id:
            raise ValueError("Missing item_type_id")

        from yuantus.meta_engine.services.meta_schema_service import MetaSchemaService

        svc = MetaSchemaService(self.session)
        return svc.get_json_schema(type_id)

    @rpc_exposed("Schema", "get_full_definition")
    def rpc_schema_get_full_definition(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Get full definition (Schema + Layout) for UI.
        args: [item_type_id]
        """
        type_id = args[0] if args else kwargs.get("item_type_id")
        if not type_id:
            raise ValueError("Missing item_type_id")

        from yuantus.meta_engine.services.meta_schema_service import MetaSchemaService

        svc = MetaSchemaService(self.session)
        return svc.get_full_definition(type_id)

    @rpc_exposed("AppStore", "install")
    def rpc_app_store_install(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Install App.
        args: [app_id, version]
        """
        app_id = args[0] if args else kwargs.get("app_id")
        version = args[1] if len(args) > 1 else kwargs.get("version")

        if not app_id or not version:
            raise ValueError("Missing app_id or version")

        from yuantus.meta_engine.services.store_service import AppStoreService

        svc = AppStoreService(self.session)
        return svc.install_app(app_id, version)

    @rpc_exposed("ECO", "apply")
    def rpc_eco_apply(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Apply ECO.
        args: [eco_id] or kwargs: {eco_id: ..., ignore_conflicts: bool}
        """
        eco_id = args[0] if args else kwargs.get("eco_id") or kwargs.get("id")
        if not eco_id:
            raise ValueError("Missing 'eco_id'")

        ignore_conflicts = kwargs.get("ignore_conflicts", False)

        from yuantus.meta_engine.services.eco_service import ECOService

        user_id_int = (
            int(self.user_id) if self.user_id and str(self.user_id).isdigit() else 1
        )

        svc = ECOService(self.session)
        return svc.action_apply(eco_id, user_id_int, ignore_conflicts=ignore_conflicts)

    @rpc_exposed("Job", "create")
    def rpc_create_job(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        RPC wrapper for JobService.create_job.
        args: [task_type, payload] or kwargs: {task_type: ..., payload: ...}
        """
        task_type = args[0] if args else kwargs.get("task_type")
        payload = args[1] if len(args) > 1 else kwargs.get("payload")

        if not task_type or not payload:
            raise ValueError("Missing 'task_type' or 'payload' for job creation")

        # Optional: priority
        priority = kwargs.get("priority", 10)
        dedupe_key = kwargs.get("dedupe_key")
        dedupe = bool(kwargs.get("dedupe", False))
        max_attempts = kwargs.get("max_attempts")

        # Get User ID
        user_id_int = (
            int(self.user_id) if self.user_id and str(self.user_id).isdigit() else None
        )

        svc = JobService(self.session)
        job = svc.create_job(
            task_type,
            payload,
            user_id=user_id_int,
            priority=priority,
            max_attempts=max_attempts,
            dedupe_key=dedupe_key,
            dedupe=dedupe,
        )

        return {"id": job.id, "status": job.status, "task_type": job.task_type}

    @rpc_exposed("Item", "check_out")
    def rpc_check_out(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Check out (lock) an item.
        args: [item_id]
        """
        item_id = args[0] if args else kwargs.get("item_id") or kwargs.get("id")
        if not item_id:
            raise ValueError("Missing item_id")

        user_id_int = (
            int(self.user_id) if self.user_id and str(self.user_id).isdigit() else 1
        )

        svc = CheckinService(self.session)
        item = svc.check_out(item_id, user_id_int)
        return {"status": "success", "locked_by_id": item.locked_by_id}

    @rpc_exposed("Item", "check_in")
    def rpc_check_in(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Check in (unlock and update) an item.
        args: [item_id, properties]
        """
        item_id = args[0] if args else kwargs.get("item_id") or kwargs.get("id")
        properties = args[1] if len(args) > 1 else kwargs.get("properties")

        if not item_id:
            raise ValueError("Missing item_id")

        user_id_int = (
            int(self.user_id) if self.user_id and str(self.user_id).isdigit() else 1
        )

        svc = CheckinService(self.session)
        item = svc.check_in(item_id, user_id_int, new_properties=properties)
        return {"status": "success", "current_version_id": item.current_version_id}

    @rpc_exposed("Item", "undo_check_out")
    def rpc_undo_check_out(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Undo check out (unlock without saving).
        args: [item_id]
        """
        item_id = args[0] if args else kwargs.get("item_id") or kwargs.get("id")
        if not item_id:
            raise ValueError("Missing item_id")

        user_id_int = (
            int(self.user_id) if self.user_id and str(self.user_id).isdigit() else 1
        )

        svc = CheckinService(self.session)
        svc.undo_check_out(item_id, user_id_int)
        return {"status": "success"}

    @rpc_exposed("Report", "compare_bom")
    def rpc_compare_bom(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Compare two BOMs.
        args: [item_id_a, item_id_b]
        """
        item_id_a = args[0] if args else kwargs.get("item_id_a")
        item_id_b = args[1] if len(args) > 1 else kwargs.get("item_id_b")

        if not item_id_a or not item_id_b:
            raise ValueError("Missing item_id_a or item_id_b")

        svc = ReportService(self.session)
        return svc.generate_bom_comparison(item_id_a, item_id_b)

    @rpc_exposed("Report", "flatten_bom")
    def rpc_flatten_bom(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Get flattened BOM.
        args: [item_id]
        """
        item_id = args[0] if args else kwargs.get("item_id") or kwargs.get("id")

        if not item_id:
            raise ValueError("Missing item_id")

        svc = ReportService(self.session)
        return svc.get_flattened_bom(item_id)

    @rpc_exposed("CAD", "sync_attributes")
    def rpc_cad_sync_attributes(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Manually sync attributes from a CAD file (simulation).
        args: [item_id, file_path]
        """
        item_id = args[0] if args else kwargs.get("item_id")
        file_path = args[1] if len(args) > 1 else kwargs.get("file_path")

        if not item_id or not file_path:
            raise ValueError("Missing item_id or file_path")

        user_id_int = (
            int(self.user_id) if self.user_id and str(self.user_id).isdigit() else 1
        )

        svc = CadService(self.session)
        # Extract
        attrs = svc.extract_attributes(file_path)
        # Sync
        item = svc.sync_attributes_to_item(item_id, attrs, user_id_int)

        return {"status": "success", "synced_attributes": attrs}

    @rpc_exposed("Search", "search")
    def rpc_search_advanced(self, args: List[Any], kwargs: Dict[str, Any]):
        """
        Advanced Search using Search Engine.
        args: [query_string]
        kwargs: {filters: {}, limit: 20}
        """
        query_string = args[0] if args else kwargs.get("query_string", "")
        filters = kwargs.get("filters", {})
        limit = kwargs.get("limit", 20)

        svc = SearchService(self.session)
        # Ensure index exists (lazy init)
        svc.ensure_index()

        return svc.search(query_string, filters=filters, limit=limit)

    def apply(self, aml: GenericItem) -> Dict[str, Any]:
        """
        核心入口：解释并执行 AML 语句
        Refactored to use Operation classes.
        """
        from yuantus.meta_engine.operations.add_op import AddOperation
        from yuantus.meta_engine.operations.get_op import GetOperation
        from yuantus.meta_engine.operations.update_op import UpdateOperation
        from yuantus.meta_engine.operations.delete_op import DeleteOperation
        from yuantus.meta_engine.operations.promote_op import PromoteOperation

        # 1. 验证 ItemType 是否存在
        item_type = self._get_item_type(aml.type)
        if not item_type:
            raise ValueError(f"ItemType '{aml.type}' not found.")

        # 2. 根据动作分发
        if aml.action == AMLAction.add:
            op = AddOperation(self)
            return op.execute(item_type, aml)
        elif aml.action == AMLAction.get:
            op = GetOperation(self)
            return op.execute(item_type, aml)
        elif aml.action == AMLAction.promote:
            op = PromoteOperation(self)
            return op.execute(item_type, aml)
        elif aml.action == AMLAction.update:
            op = UpdateOperation(self)
            return op.execute(item_type, aml)
        elif aml.action == AMLAction.delete:
            op = DeleteOperation(self)
            return op.execute(item_type, aml)

        return {"error": "Action not supported yet"}

    def _get_item_type(self, type_name: str) -> ItemType:
        # Cache this lookup in production!
        try:
            return self.session.execute(
                select(ItemType).where(ItemType.id == type_name)
            ).scalar_one_or_none()
        except OperationalError as exc:
            raise ValidationError(
                "Meta schema not initialized; run migrations/meta_seed first.",
                field="type",
            ) from exc

    def apply_relationship(self, rel_aml: GenericItem, source_item: Item):
        """
        专门处理嵌套关系
        rel_aml.type 必须是 'Part BOM' 这种关系类型
        """
        from yuantus.meta_engine.operations.add_op import AddOperation
        
        rel_type = self._get_item_type(rel_aml.type)
        if not rel_type or not rel_type.is_relationship:
            raise ValueError(f"{rel_aml.type} is not a valid Relationship ItemType")

        # Relationship 'add' needs a parent item context (source_item). For other actions
        # (get/update/delete/promote), dispatch normally via apply().
        if rel_aml.action != AMLAction.add:
            return self.apply(rel_aml)

        # Use AddOperation directly for consistent logic
        op = AddOperation(self)
        return op.execute(rel_type, rel_aml, parent_item=source_item)
          
