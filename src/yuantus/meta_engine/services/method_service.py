import logging
from typing import Dict, Any
from sqlalchemy.orm import Session
from yuantus.meta_engine.business_logic.models import Method, MethodType
from yuantus.meta_engine.business_logic.sandbox import (
    AuditContext,
    run_module,
    run_script,
)

logger = logging.getLogger(__name__)


class MethodService:
    def __init__(self, session: Session):
        self.session = session

    def execute_method(self, method_name: str, context: Dict[str, Any]) -> Any:
        """
        Execute a server method by name.

        Args:
            method_name: Name of the Method record in DB.
            context: Dictionary of objects to inject into the execution scope.
                     Expected keys: 'item', 'session', 'user_id', 'plm', etc.

        Returns:
            The return value of the script (if any), or modifies context objects in place.
        """
        method = self.session.query(Method).filter_by(name=method_name).first()
        if not method:
            logger.error(f"Method '{method_name}' not found.")
            raise ValueError(f"Method '{method_name}' not found.")

        if method.type == MethodType.PYTHON_SCRIPT or method.type == "python_script":
            return self._execute_script(method, context)
        elif method.type == MethodType.PYTHON_MODULE or method.type == "python_module":
            return self._execute_module(method, context)
        else:
            raise ValueError(f"Unknown Method Type: {method.type}")

    def _audit_ctx(
        self, method: Method, context: Dict[str, Any], kind: str
    ) -> AuditContext:
        return AuditContext(
            user_id=context.get("user_id"),
            method_id=getattr(method, "id", None),
            method_name=getattr(method, "name", None),
            kind=kind,
        )

    def _execute_script(self, method: Method, context: Dict[str, Any]) -> Any:
        """
        Execute raw Python code stored in the database, sandboxed (P0-8a).
        The script sees everything in ``context``; if it sets ``result`` that
        value is returned. A sandbox violation propagates to the caller.
        """
        code = method.content
        if not code:
            return None

        scope = context.copy()
        scope["logger"] = logger

        outputs = run_script(
            code,
            scope,
            session=self.session,
            audit=self._audit_ctx(method, context, "script"),
        )
        return outputs.get("result")

    def _execute_module(self, method: Method, context: Dict[str, Any]) -> Any:
        """
        Load and call an allowlisted ``path.to.module:function`` (P0-8a).
        Non-allowlisted / import-failing modules fail closed (violation).
        """
        content = method.content or ""
        if ":" in content:
            mod_path, func_name = content.split(":", 1)
        else:
            mod_path = content
            func_name = "main"

        return run_module(
            mod_path,
            entry=func_name,
            invoke=lambda fn: fn(**context),
            session=self.session,
            audit=self._audit_ctx(method, context, "module"),
        )
