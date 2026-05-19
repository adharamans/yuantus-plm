"""
Domain Event Definitions for PLM Meta Engine.
These events represent significant changes in the system state.
"""

from datetime import datetime
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field  # Import Field
import uuid


class DomainEvent(BaseModel):
    """Base class for all domain events."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    actor_id: Optional[int] = None  # User who initiated the event
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ItemCreatedEvent(DomainEvent):
    event_type: str = "item.created"
    item_id: str
    item_type_id: str
    properties: Dict[str, Any]


class ItemUpdatedEvent(DomainEvent):
    event_type: str = "item.updated"
    item_id: str
    item_type_id: str
    changes: Dict[str, Any]  # Dictionary of changed properties/fields


class ItemDeletedEvent(DomainEvent):
    event_type: str = "item.deleted"
    item_id: str
    item_type_id: str


class ItemStateChangedEvent(DomainEvent):
    event_type: str = "item.state_changed"
    item_id: str
    item_type_id: str
    old_state: str
    new_state: str
    transition_name: Optional[str] = None


class EcoCreatedEvent(DomainEvent):
    event_type: str = "eco.created"
    eco_id: str
    eco_type: str
    state: str
    product_id: Optional[str] = None


class EcoUpdatedEvent(DomainEvent):
    event_type: str = "eco.updated"
    eco_id: str
    changes: Dict[str, Any] = Field(default_factory=dict)
    state: Optional[str] = None


class EcoDeletedEvent(DomainEvent):
    event_type: str = "eco.deleted"
    eco_id: str


class BreakageDesignLoopbackEcoEvent(DomainEvent):
    """Tier-B #3 §3.6 (taskbook ``61ce226``). Emitted when a
    breakage design-loopback ECO result converges — a CAS-winner
    creation (``created=True``) or a durable-dedupe reuse
    (``created=False``). Never emitted on the §3.2 CAS-loser or
    unrecoverable arms (those roll back; the transactional outbox
    drops queued events), so ``eco_id`` is always populated.
    """

    event_type: str = "breakage.design_loopback_eco"
    incident_id: str
    eco_id: str
    created: bool
    trigger_source: str  # "route" | "update_status" | "helpdesk_sync"
    incident_status: str
    sync_status: Optional[str] = None  # helpdesk_sync source only (§3.F)
    provider_ticket_status: Optional[str] = None  # helpdesk_sync only (§3.F)


class FileUploadedEvent(DomainEvent):
    event_type: str = "file.uploaded"
    file_id: str
    file_name: str
    file_size: int
    storage_key: str


class FileCheckedInEvent(DomainEvent):
    event_type: str = "file.checked_in"
    item_id: str
    file_id: Optional[str] = None  # Made optional as not all items have files
    new_version_id: Optional[str] = None


class CadAttributesSyncedEvent(DomainEvent):
    event_type: str = "cad.attributes_synced"
    item_id: str
    file_id: str
    synced_attributes: Dict[str, Any]
