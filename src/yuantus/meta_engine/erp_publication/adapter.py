"""PLM->ERP publication adapter interface (G2 R2).

The abstract seam an external-ERP adapter implements. Repo idiom is ABC +
@abstractmethod (cf. StorageProvider, BaseOperation); there is no typing.Protocol
interface in src.

NOTE on the name: in this repo `*Adapter` otherwise denotes a CONCRETE delegator
(e.g. EcoPermissionAdapter). Here `ErpPublicationAdapter` is the ABSTRACT base —
a deliberate, documented departure that honors the #666/#667 "adapter interface"
vocabulary. The concrete real-ERP implementation (e.g. an Odoo/HTTP client in the
DedupVisionClient mold) is a LATER, separate taskbook.

`dry_run` is intentionally NOT a method here. Dry-run is an outbox-SERVICE
operation that calls build_payload + validate_contract only and never `send` —
structurally guaranteeing dry-run produces no external side effect (R2 build
taskbook §2/§3).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)


@dataclass
class SendResult:
    ok: bool
    remote_id: Optional[str] = None
    error: Optional[str] = None
    # When ok is False: which reason the outbox should record. Defaults to
    # remote_error; an exception escaping send() is classified adapter_error.
    error_kind: Optional[str] = None


class ErpPublicationAdapter(ABC):
    """Abstract base for an external-ERP publication adapter (see module docstring)."""

    @abstractmethod
    def build_payload(self, snapshot: dict) -> dict:
        """Build the target-ERP payload from the outbox publication snapshot."""

    @abstractmethod
    def validate_contract(self, payload: dict) -> ValidationResult:
        """Validate the payload against the target contract WITHOUT any external call."""

    @abstractmethod
    def send(self, payload: dict) -> SendResult:
        """Dispatch the payload to the target ERP (the only external-write entry point)."""


class NullErpPublicationAdapter(ErpPublicationAdapter):
    """In-repo, no-external-I/O adapter.

    The ONLY path that can reach `sent` in connector-less R2: `send` records the
    dispatch LOCALLY (no network, no external write — honoring the no-external-
    write boundary) so the full outbox state machine is exercisable end to end.
    `sent` via this adapter explicitly does NOT mean a real ERP received anything;
    production `sent` requires the later real-connector taskbook.
    """

    def build_payload(self, snapshot: dict) -> dict:
        item = snapshot.get("item") or {}
        return {
            "target_system": snapshot.get("target_system"),
            "publication_kind": snapshot.get("publication_kind"),
            "item_id": item.get("item_id"),
            "snapshot": snapshot,
        }

    def validate_contract(self, payload: dict) -> ValidationResult:
        errors: List[str] = []
        if not payload.get("item_id"):
            errors.append("missing item_id")
        if not payload.get("target_system"):
            errors.append("missing target_system")
        return ValidationResult(ok=not errors, errors=errors)

    def send(self, payload: dict) -> SendResult:
        # No external I/O: acknowledge locally with a deterministic local id.
        return SendResult(ok=True, remote_id=f"null:{payload.get('item_id')}")
