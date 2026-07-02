from typing import Optional
from sqlalchemy.orm import Session
from .models import Method, MethodType
from .sandbox import AuditContext, run_module, run_script
from ..models.item import Item
from ..schemas.aml import GenericItem


class MethodExecutor:
    def __init__(self, session: Session):
        self.session = session

    def execute_method(
        self, method_id: str, context_item: Item, payload: Optional[GenericItem] = None
    ) -> Item:
        """
        执行方法
        Args:
            method_id: 方法ID
            context_item: 当前操作的 Item 对象 (会被修改)
            payload: 原始 AML 请求
        Returns:
            修改后的 Item
        """
        method_def = self.session.get(Method, method_id)
        if not method_def:
            # 容错：如果找不到方法，忽略而不阻断
            return context_item

        if (
            method_def.type == MethodType.PYTHON_MODULE
            or method_def.type == "python_module"
        ):
            return self._run_module(
                method_def, method_def.content, context_item, payload
            )

        if (
            method_def.type == MethodType.PYTHON_SCRIPT
            or method_def.type == "python_script"
        ):
            return self._run_script(method_def, context_item, payload)

        return context_item

    def _audit_ctx(self, method: Method, kind: str) -> AuditContext:
        return AuditContext(
            user_id=None,
            method_id=getattr(method, "id", None),
            method_name=getattr(method, "name", None),
            kind=kind,
        )

    def _run_script(
        self, method: Method, item: Item, payload: Optional[GenericItem]
    ) -> Item:
        """
        Execute raw Python code stored in DB, sandboxed (P0-8a).
        Vars in scope: 'session', 'item', 'payload'. The script mutates
        'item' in place; a sandbox violation blocks the transaction.
        """
        code = method.content
        if not code:
            return item

        run_script(
            code,
            {
                "session": self.session,
                "item": item,
                "payload": payload,
            },
            session=self.session,
            audit=self._audit_ctx(method, "script"),
        )
        # Hooks mutate 'item' in place; return the same (mutated) object.
        return item

    def _run_module(
        self, method: Method, module_path: str, item: Item, payload: GenericItem
    ) -> Item:
        """
        Import an allowlisted module and run its entry point (P0-8a).
        约定入口函数签名为: def run(session, item, payload) -> Item.
        Non-allowlisted / import-failing modules fail closed (violation),
        not a silent print as before.
        """
        result = run_module(
            module_path,
            entry="run",
            invoke=lambda fn: fn(self.session, item, payload),
            session=self.session,
            audit=self._audit_ctx(method, "module"),
        )
        return result or item
