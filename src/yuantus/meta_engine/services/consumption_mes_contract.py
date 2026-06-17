"""MES → ConsumptionRecord ingestion contract (R1, contract-only).

This module defines a *boundary*: the typed shape an external MES is
expected to send, plus a pure mapper into the exact keyword arguments the
existing ``ConsumptionPlanService.add_actual`` already accepts.

This module is the pure boundary only. It does NOT:

- expose any route or wire any runtime ingestion (that is the R2 router/service);
- enforce idempotency -- it derives the key and now carries it as a first-class
  ``ConsumptionRecordInputs`` field (R2); the unique ``ConsumptionRecord.idempotency_key``
  column + ``ConsumptionPlanService.ingest_mes_consumption`` are what *enforce* it;
- change ``add_actual`` (manual path) or ``variance`` semantics.

See ``docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_CONSUMPTION_MES_INGESTION_CONTRACT_20260515.md``
(R1) and ``docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_CONSUMPTION_MES_INGESTION_RUNTIME_R2_TASKBOOK_20260617.md`` (R2).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "mes-consumption.v1"

# Reserved key the mapper injects into ``properties``. A caller-supplied
# ``attributes`` dict that already contains this key is rejected so the
# ingestion envelope can never be silently spoofed.
RESERVED_PROPERTIES_KEY = "_ingestion"

# Small allowlist for source_type. This is the MES ingestion boundary, so
# only MES-legitimate sources are allowed: "mes" (direct) and "workorder"
# (MES attributing consumption to a work order). "manual" is deliberately
# excluded — human entry is not a MES event. Widening is a follow-up, not
# an R1 concern.
ALLOWED_SOURCE_TYPES = frozenset({"mes", "workorder"})

DEFAULT_SOURCE_TYPE = "mes"

# Field-separator used when deriving the idempotency key. Unit separator
# (0x1f) so plan/source/event ids cannot collide via concatenation.
_KEY_SEP = "\x1f"


class MesConsumptionEvent(BaseModel):
    """Inbound MES consumption event.

    Frozen so a validated event cannot be mutated between validation and
    mapping.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    mes_event_id: str
    actual_quantity: float
    source_type: str = DEFAULT_SOURCE_TYPE
    source_id: Optional[str] = None
    recorded_at: Optional[datetime] = None
    uom: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("plan_id", "mes_event_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("must be a non-empty string")
        return cleaned

    @field_validator("actual_quantity")
    @classmethod
    def _finite_non_negative(cls, value: float) -> float:
        numeric = float(value)
        if math.isnan(numeric) or math.isinf(numeric):
            raise ValueError("actual_quantity must be a finite number")
        if numeric < 0:
            raise ValueError("actual_quantity must be >= 0")
        return numeric

    @field_validator("source_type")
    @classmethod
    def _source_type_allowed(cls, value: str) -> str:
        normalized = (value or DEFAULT_SOURCE_TYPE).strip().lower()
        if normalized not in ALLOWED_SOURCE_TYPES:
            raise ValueError(
                f"source_type must be one of {sorted(ALLOWED_SOURCE_TYPES)}"
            )
        return normalized

    @field_validator("recorded_at")
    @classmethod
    def _naive_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        # The ConsumptionRecord.recorded_at column is a naive DateTime and
        # the rest of the codebase stores naive-UTC. Convert tz-aware input
        # to UTC and drop tzinfo so the contract never injects an
        # offset-aware datetime into a naive column.
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    @field_validator("source_id", "uom")
    @classmethod
    def _blank_to_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


@dataclass(frozen=True)
class ConsumptionRecordInputs:
    """The exact keyword arguments ``add_actual`` accepts.

    Intentionally mirrors the ``add_actual`` signature 1:1 so the drift
    test can assert alignment.
    """

    plan_id: str
    actual_quantity: float
    source_type: str
    source_id: Optional[str]
    recorded_at: Optional[datetime]
    properties: Dict[str, Any]
    idempotency_key: str

    def as_kwargs(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "actual_quantity": self.actual_quantity,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "recorded_at": self.recorded_at,
            "properties": self.properties,
            "idempotency_key": self.idempotency_key,
        }


def derive_consumption_idempotency_key(event: MesConsumptionEvent) -> str:
    """Deterministic key for a single MES consumption event.

    Stable across retries of the same event; distinct across different
    plan / source_type / mes_event_id. R1 only derives and records this —
    it does NOT enforce uniqueness (no DB constraint, no dedupe). Dedupe
    enforcement is a separate, later opt-in.
    """

    material = _KEY_SEP.join(
        (event.plan_id, event.source_type, event.mes_event_id)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def map_mes_event_to_consumption_record_inputs(
    event: MesConsumptionEvent,
) -> ConsumptionRecordInputs:
    """Pure map: validated MES event -> add_actual kwargs.

    No DB reads. The idempotency key is recorded inside ``properties``
    under the reserved ``_ingestion`` envelope; a caller-supplied
    ``attributes`` dict that already uses that key is rejected.
    """

    if RESERVED_PROPERTIES_KEY in event.attributes:
        raise ValueError(
            f"attributes must not contain the reserved key "
            f"{RESERVED_PROPERTIES_KEY!r}"
        )

    idempotency_key = derive_consumption_idempotency_key(event)
    properties: Dict[str, Any] = dict(event.attributes)
    properties[RESERVED_PROPERTIES_KEY] = {
        "contract_version": CONTRACT_VERSION,
        "idempotency_key": idempotency_key,
        "mes_event_id": event.mes_event_id,
        "source_type": event.source_type,
        # uom is echoed for observability. This pure mapper does NOT reconcile
        # it against plan.uom (that needs a DB read); the ingestion route does
        # that reconciliation (R2.1) and rejects a declared-uom mismatch (422).
        "uom": event.uom,
    }

    return ConsumptionRecordInputs(
        plan_id=event.plan_id,
        actual_quantity=event.actual_quantity,
        source_type=event.source_type,
        source_id=event.source_id,
        recorded_at=event.recorded_at,
        properties=properties,
        # R2: the key is also carried as a first-class field so the runtime can
        # persist it on the unique column. It remains echoed inside
        # `properties._ingestion` above for observability/back-compat.
        idempotency_key=idempotency_key,
    )
