"""
Effectivity Service - ADR-005 Implementation.

Provides effectivity checking for Items and Relationships.
Supports Date (v1.2), Lot/Serial (v1.4+), and Unit (v2.0+) effectivity types.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from ..models.effectivity import Effectivity
from .latest_released_guard import assert_latest_released
from .suspended_guard import assert_not_suspended


@dataclass
class EffectivityContext:
    """
    Context for effectivity checking.
    Provides all parameters needed for different effectivity types.
    """

    reference_date: Optional[datetime] = None  # Date Effectivity
    lot_number: Optional[str] = None  # Lot Effectivity (v1.4+)
    serial_number: Optional[str] = None  # Serial Effectivity (v1.4+)
    unit_position: Optional[str] = None  # Unit Effectivity (v2.0+)


class EffectivityService:
    """
    Service for managing and checking Item/Relationship Effectivity.
    ADR-005 Implementation.
    """

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _normalize_utc_naive(dt: datetime) -> datetime:
        """
        Normalize datetime to UTC, dropping timezone info (naive UTC).

        The codebase historically uses naive UTC (datetime.utcnow()).
        Incoming API values may be timezone-aware (e.g. '...Z'), so we normalize
        to avoid "can't compare offset-naive and offset-aware" errors.
        """
        if dt.tzinfo is None:
            return dt
        from datetime import timezone

        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    # ========== CRUD Operations ==========

    def create_effectivity(
        self,
        item_id: Optional[str] = None,
        version_id: Optional[str] = None,
        effectivity_type: str = "Date",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        payload: Optional[dict] = None,
        created_by_id: Optional[int] = None,
    ) -> Effectivity:
        """
        Create a new effectivity record.
        """
        for target_id in (item_id, version_id):
            if target_id:
                assert_latest_released(self.session, target_id, context="effectivity")
                assert_not_suspended(self.session, target_id, context="effectivity")

        start_date = self._normalize_utc_naive(start_date) if start_date else None
        end_date = self._normalize_utc_naive(end_date) if end_date else None

        effectivity = Effectivity(
            id=str(uuid.uuid4()),
            item_id=item_id,
            version_id=version_id,
            effectivity_type=effectivity_type,
            start_date=start_date,
            end_date=end_date,
            payload=payload or {},
            created_by_id=created_by_id,
        )
        self.session.add(effectivity)
        self.session.flush()
        return effectivity

    def get_effectivity(self, effectivity_id: str) -> Optional[Effectivity]:
        """Get effectivity by ID."""
        return (
            self.session.query(Effectivity)
            .filter(Effectivity.id == effectivity_id)
            .first()
        )

    def get_item_effectivities(self, item_id: str) -> List[Effectivity]:
        """Get all effectivities for an item."""
        return (
            self.session.query(Effectivity).filter(Effectivity.item_id == item_id).all()
        )

    def get_version_effectivities(self, version_id: str) -> List[Effectivity]:
        """Get all effectivities for a version."""
        return (
            self.session.query(Effectivity)
            .filter(Effectivity.version_id == version_id)
            .all()
        )

    def get_expired_date_effectivities(
        self, *, now: Optional[datetime] = None, version_scoped_only: bool = True
    ) -> List[Effectivity]:
        """Date effectivities whose window has closed (``end_date < now``).

        This is the negation of the *end-date* half of effectivity (``end_date IS NULL
        OR end_date >= now``): an open-ended effectivity (``end_date IS NULL``) is never
        expired. It deliberately does NOT mirror ``VersionService.find_effective_version``
        on the *start* side (that helper excludes ``NULL start_date`` rows; the canonical
        ``_check_date`` treats a null start as -infinity) — start has no bearing on
        whether a window has *closed*. ``now`` defaults to
        naive-UTC ``utcnow()`` to match the column's storage. ``version_scoped_only``
        (default) restricts to effectivities bound to an ``ItemVersion`` — the C3
        date-BOM auto-obsolete scope; BOM-line (``item_id``-scoped) effectivities are
        a separate follow-up.
        """
        reference = self._normalize_utc_naive(now) if now is not None else datetime.utcnow()
        query = (
            self.session.query(Effectivity)
            .filter(Effectivity.effectivity_type == "Date")
            .filter(Effectivity.end_date.isnot(None))
            .filter(Effectivity.end_date < reference)
        )
        if version_scoped_only:
            query = query.filter(Effectivity.version_id.isnot(None))
        return query.all()

    def delete_effectivity(self, effectivity_id: str) -> bool:
        """Delete an effectivity record."""
        effectivity = self.get_effectivity(effectivity_id)
        if effectivity:
            self.session.delete(effectivity)
            return True
        return False

    # ========== Effectivity Checking ==========

    def check_effectivity(
        self, item_id: str, context: Optional[EffectivityContext] = None
    ) -> bool:
        """
        Check if Item is effective in the given context.
        Returns True if item has no effectivity defined, or if any effectivity matches.

        Args:
            item_id: The item to check
            context: EffectivityContext with check parameters (defaults to current date)
        """
        if context is None:
            context = EffectivityContext(reference_date=datetime.now())

        effectivities = self.get_item_effectivities(item_id)

        if not effectivities:
            return True  # No effectivity defined = always effective

        # OR logic: any matching effectivity makes item effective
        for eff in effectivities:
            if self._check_single(eff, context):
                return True

        return False

    def check_date_effectivity(self, item_id: str, target_date: datetime) -> bool:
        """
        Check if the item is effective on the given date.
        Convenience method for date-only checking.
        """
        context = EffectivityContext(reference_date=target_date)
        return self.check_effectivity(item_id, context)

    def _check_single(self, eff: Effectivity, ctx: EffectivityContext) -> bool:
        """Check a single effectivity record against context."""
        if eff.effectivity_type == "Date":
            return self._check_date(eff, ctx)
        elif eff.effectivity_type == "Lot":
            return self._check_lot(eff, ctx)
        elif eff.effectivity_type == "Serial":
            return self._check_serial(eff, ctx)
        elif eff.effectivity_type == "Unit":
            return self._check_unit(eff, ctx)
        return False

    def _check_date(self, eff: Effectivity, ctx: EffectivityContext) -> bool:
        """
        Date Effectivity check (v1.2 MVP).
        Open start/end means infinite.
        """
        ref_date = ctx.reference_date or datetime.now()
        ref_date = self._normalize_utc_naive(ref_date)
        start_date = self._normalize_utc_naive(eff.start_date) if eff.start_date else None
        end_date = self._normalize_utc_naive(eff.end_date) if eff.end_date else None

        if start_date and ref_date < start_date:
            return False
        if end_date and ref_date > end_date:
            return False
        return True

    def _check_lot(self, eff: Effectivity, ctx: EffectivityContext) -> bool:
        """
        Lot Effectivity check (v1.4+).
        Uses string comparison for lot numbers.
        """
        if not ctx.lot_number:
            return False

        payload = eff.payload or {}
        lot_start = payload.get("lot_start")
        lot_end = payload.get("lot_end")

        # String comparison (assumes consistent lot number format)
        if lot_start and ctx.lot_number < lot_start:
            return False
        if lot_end and ctx.lot_number > lot_end:
            return False
        return True

    def _check_serial(self, eff: Effectivity, ctx: EffectivityContext) -> bool:
        """
        Serial Effectivity check (v1.4+).
        Checks if serial number is in the allowed list.
        """
        if not ctx.serial_number:
            return False

        payload = eff.payload or {}
        serials = payload.get("serials", [])
        return ctx.serial_number in serials

    def _check_unit(self, eff: Effectivity, ctx: EffectivityContext) -> bool:
        """
        Unit Effectivity check (v2.0+).
        Checks if unit position is in the allowed list.
        """
        if not ctx.unit_position:
            return False

        payload = eff.payload or {}
        positions = payload.get("unit_positions", [])
        return ctx.unit_position in positions

    # ========== BOM Integration ==========

    def filter_bom_by_effectivity(
        self, bom_lines: List[dict], context: Optional[EffectivityContext] = None
    ) -> List[dict]:
        """
        Filter BOM lines by effectivity.
        Returns only lines that are effective in the given context.

        Args:
            bom_lines: List of BOM line dicts with 'item_id' key
            context: EffectivityContext for checking
        """
        if context is None:
            context = EffectivityContext(reference_date=datetime.now())

        return [
            line
            for line in bom_lines
            if self.check_effectivity(line.get("item_id"), context)
        ]
