"""CAD-PDM C3 — date-BOM auto-obsolete + upward where-used flag (mechanism, unwired).

When a date effectivity expires (``end_date < now``), the affected Item is promoted to
the lifecycle **Obsolete** end-state **only if it has no remaining currently-effective
version** (owner decision); otherwise it is merely marked. In both cases the Item's
**depth-1** where-used parents are **flagged** for review (``DateObsoleteImpact``) — C3
never cascades an obsolete up the BOM.

This module is mechanism-only: nothing calls it at runtime yet (no worker, no route), so
landing it changes no behavior. The polling worker + ops routes + the default-off setting
are the R-next wiring slice.

Decisions (owner-ratified): obsolete = Item lifecycle ``Obsolete`` via
``LifecycleService.promote`` (NOT version ``is_superseded``, which is orthogonal); upward
propagation = FLAG, never cascade; depth = 1; scheduler = a polling worker, no cron;
default-off.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from yuantus.meta_engine.lifecycle.service import LifecycleService
from yuantus.meta_engine.models.date_obsolete import DateObsoleteImpact
from yuantus.meta_engine.models.effectivity import Effectivity
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.services.bom_service import BOMService
from yuantus.meta_engine.services.effectivity_service import EffectivityService
from yuantus.meta_engine.version.models import ItemVersion

_OBSOLETE_STATE = "Obsolete"


class DateEffectivityObsoleteService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.effectivity = EffectivityService(session)
        self.bom = BOMService(session)
        self.lifecycle = LifecycleService(session)

    # -- detection -----------------------------------------------------------
    def scan_expired(self, *, now: Optional[datetime] = None) -> List[Effectivity]:
        return self.effectivity.get_expired_date_effectivities(now=now)

    def _affected_item_id(self, eff: Effectivity) -> Optional[str]:
        # v1 scope: version-scoped date effectivities -> the version's item.
        if not eff.version_id:
            return None
        version = self.session.get(ItemVersion, eff.version_id)
        return version.item_id if version else None

    def _has_effective_version(self, item_id: str, now: datetime) -> bool:
        """True if the Item still has a currently-effective version, under the
        codebase's canonical date rule (mirrors ``EffectivityService._check_date`` +
        ``check_effectivity``): a version with **no** Date effectivity is unbounded
        (always effective), and a version **with** a Date effectivity is effective
        when it is in window — ``NULL start_date`` = -infinity, ``NULL end_date`` =
        +infinity.

        Computed directly here, NOT via ``VersionService.find_effective_version``,
        which wrongly excludes open-start (``NULL start_date``) and no-effectivity
        versions (a pre-existing narrowness in that shared helper — left untouched to
        avoid blast radius). Erring toward "effective" honours the owner rule:
        obsolete ONLY when the Item has NO currently-effective version.
        """
        norm = self.effectivity._normalize_utc_naive
        for version in (
            self.session.query(ItemVersion).filter(ItemVersion.item_id == item_id).all()
        ):
            date_effs = [
                e
                for e in self.effectivity.get_version_effectivities(version.id)
                if e.effectivity_type == "Date"
            ]
            if not date_effs:
                return True  # unbounded version => always effective
            for e in date_effs:
                start = norm(e.start_date) if e.start_date else None
                end = norm(e.end_date) if e.end_date else None
                if (start is None or start <= now) and (end is None or end >= now):
                    return True
        return False

    # -- process one expired effectivity ------------------------------------
    def process_expired(
        self,
        eff: Effectivity,
        *,
        user_id: int,
        now: Optional[datetime] = None,
        apply_obsolete: bool = True,
    ) -> Dict[str, Any]:
        """Resolve the affected Item, obsolete-or-mark it, then flag depth-1 parents.

        Idempotent: re-running on the same expired effectivity is a no-op (the Item is
        already Obsolete or still effective; parent flags are upserted on a unique key).
        """
        now = self.effectivity._normalize_utc_naive(now) if now else datetime.utcnow()
        item_id = self._affected_item_id(eff)
        if not item_id:
            return {"status": "skipped", "reason": "not_version_scoped", "effectivity_id": eff.id}
        item = self.session.get(Item, item_id)
        if item is None:
            return {"status": "skipped", "reason": "item_not_found", "item_id": item_id}

        has_effective = self._has_effective_version(item_id, now)
        already_obsolete = (item.state == _OBSOLETE_STATE)
        child_obsoleted = already_obsolete
        obsolete_error: Optional[str] = None

        if apply_obsolete and not has_effective and not already_obsolete:
            try:
                result = self.lifecycle.promote(
                    item,
                    _OBSOLETE_STATE,
                    user_id,
                    comment="date effectivity expired (C3 auto-obsolete)",
                )
                child_obsoleted = bool(getattr(result, "success", False))
                if not child_obsoleted:
                    obsolete_error = getattr(result, "error", "promote_failed")
            except Exception as exc:  # never let one item crash the sweep
                obsolete_error = f"{type(exc).__name__}: {exc}"

        # Distinct, durable reasons so a reviewer can tell apart: the child was
        # obsoleted; a deliberate mark (the item still has an effective version);
        # or a FAILED obsolete (no effective version but promote could not run, e.g.
        # the lifecycle map / transition is unconfigured) — the last must not look
        # identical to a deliberate mark.
        if child_obsoleted:
            reason = "child_obsoleted"
        elif obsolete_error is not None:
            reason = "child_obsolete_failed"
        else:
            reason = "child_effectivity_expired"

        flagged = self._flag_parents(
            eff, item_id, reason=reason, child_obsoleted=child_obsoleted,
            obsolete_error=obsolete_error, now=now,
        )
        return {
            "status": "processed",
            "effectivity_id": eff.id,
            "item_id": item_id,
            "has_effective_version": has_effective,
            "child_obsoleted": child_obsoleted,
            "obsolete_error": obsolete_error,
            "reason": reason,
            "flagged_parents": flagged,
        }

    # -- depth-1 upward flag (never cascade) ---------------------------------
    def _flag_parents(
        self,
        eff: Effectivity,
        child_item_id: str,
        *,
        reason: str,
        child_obsoleted: bool,
        obsolete_error: Optional[str],
        now: datetime,
    ) -> int:
        parents = self.bom.get_where_used(child_item_id, recursive=False)  # depth-1 only
        props = {"obsolete_error": obsolete_error} if obsolete_error else None
        flagged = 0
        for entry in parents:
            parent = entry.get("parent") or {}
            parent_id = parent.get("id")
            if not parent_id:
                continue
            existing = (
                self.session.query(DateObsoleteImpact)
                .filter(
                    DateObsoleteImpact.effectivity_id == eff.id,
                    DateObsoleteImpact.parent_item_id == parent_id,
                )
                .one_or_none()
            )
            if existing is not None:
                # idempotent: refresh the outcome together (child_obsoleted AND reason
                # AND the error payload) so a re-scan after a fix never leaves a
                # contradictory row (e.g. child_obsoleted=True with a stale "expired"
                # reason).
                existing.child_obsoleted = child_obsoleted
                existing.reason = reason
                existing.properties = props
                continue
            self.session.add(
                DateObsoleteImpact(
                    effectivity_id=eff.id,
                    child_item_id=child_item_id,
                    parent_item_id=parent_id,
                    child_obsoleted=child_obsoleted,
                    reason=reason,
                    state="open",
                    detected_at=now,
                    properties=props,
                )
            )
            flagged += 1
        self.session.flush()
        return flagged
