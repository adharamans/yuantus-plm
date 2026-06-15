import logging
from typing import Dict, Any, TYPE_CHECKING
from .base import BaseOperation
from ..models.item import Item
from yuantus.meta_engine.schemas.aml import AMLAction, GenericItem
from yuantus.exceptions.handlers import PermissionError, StateLockedError, ValidationError
from yuantus.meta_engine.lifecycle.guard import is_item_locked
from yuantus.meta_engine.events.domain_events import ItemUpdatedEvent
from yuantus.meta_engine.events.transactional import enqueue_event
from yuantus.meta_engine.services.item_number_keys import (
    ensure_item_number_aliases,
    get_item_number,
)

logger = logging.getLogger(__name__)

# WP3.2 (B3): roles permitted to override item_number immutability (with audit).
_ITEM_NUMBER_OVERRIDE_ROLES = frozenset({"admin", "superuser"})

if TYPE_CHECKING:
    from ..models.meta_schema import ItemType

class UpdateOperation(BaseOperation):
    """
    Handles AML 'update' action.
    """
    def execute(self, item_type: 'ItemType', aml: GenericItem) -> Dict[str, Any]:
        if not aml.id:
            raise ValidationError("Update requires an item id", field="id")
        
        # We access session via self.session (inherited from BaseOperation)
        item = self.session.get(Item, aml.id)
        if not item or item.item_type_id != item_type.id:
            raise ValidationError("Item not found for update", field="id")

        if not self.permission_service.check_permission(
            item_type.id,
            AMLAction.update,
            self.user_id,
            self.roles,
            item_state=item.state,
            item_owner_id=str(item.created_by_id) if item.created_by_id else None,
        ):
            raise PermissionError(action=AMLAction.update.value, resource=item_type.id)

        locked, locked_state = is_item_locked(self.session, item, item_type)
        if locked:
            raise StateLockedError(locked_state or (item.state or "unknown"), item_type.id)

        # WP3.2 (B3): item_number / number is immutable once assigned. A normal update
        # may not change a non-empty existing number to a different non-empty value;
        # only an admin / superuser may override, and the override is audited. (First
        # assignment, no-op re-submits, and edits to other fields are unaffected.)
        existing_number = get_item_number(item.properties or {})
        incoming_number = get_item_number(aml.properties or {})
        if existing_number and incoming_number and incoming_number != existing_number:
            if not (_ITEM_NUMBER_OVERRIDE_ROLES & set(self.roles or [])):
                raise ValidationError(
                    "item_number is immutable once assigned", field="item_number"
                )
            logger.warning(
                "item_number immutability overridden: item_id=%s existing=%r "
                "incoming=%r actor=%s roles=%s",
                item.id,
                existing_number,
                incoming_number,
                self.user_id,
                sorted(set(self.roles or [])),
            )

        merged = dict(item.properties or {})
        merged.update(aml.properties or {})
        alias_value = get_item_number(aml.properties or {}) or get_item_number(merged)
        merged = ensure_item_number_aliases(merged, alias_value)

        # Access engine validator for validation
        validated = self.engine.validator.validate_and_normalize(item_type, merged)
        item.properties = validated

        # Method Hook (onAfterUpdate)
        if item_type.on_after_update_method_id:
            item = self.engine.method_executor.execute_method(
                item_type.on_after_update_method_id, item, aml
            )

        # Deep update (nested relationships)
        # Mirrors Aras-style AML behavior where relationship rows can be added/updated/deleted
        # within an update call.
        if aml.relationships:
            for rel_aml in aml.relationships:
                if rel_aml.action == AMLAction.add:
                    self.engine.apply_relationship(rel_aml, source_item=item)
                else:
                    # For non-add actions, relationship items must be self-contained
                    # (typically identified by id) and can be dispatched normally.
                    self.engine.apply(rel_aml)
            
        self.session.flush()
        
        enqueue_event(
            self.session,
            ItemUpdatedEvent(
                item_id=item.id,
                item_type_id=item_type.id,
                changes=aml.properties,
                actor_id=int(self.user_id) if self.user_id.isdigit() else None,
            )
        )
        return {"id": item.id, "type": item.item_type_id, "status": "updated"}
