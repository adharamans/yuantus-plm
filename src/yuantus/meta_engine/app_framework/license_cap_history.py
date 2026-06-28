"""Read-only seat-cap change-history query (L4 Fork B), shared by the admin HTTP
read surface (``license_cap_change_history_router``) and the ``yuantus license
cap-history`` operator CLI.

Both surfaces derive a tenant's seat-cap change history from the LICENSE audit trail
that ``record_seat_cap_audit`` writes (``AuditLog`` method=``"LICENSE"``,
path=``"cli:license/seat-cap?max_users={N|cleared}"``). Read-only; append-only
history; no mutation and no route of its own. Extracting the query here keeps the
HTTP route and the CLI from drifting in how they parse that trail.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from yuantus.models.audit import AuditLog

# record_seat_cap_audit (license_import_service.py) writes this exact shape.
_SEAT_CAP_PATH_LIKE = "cli:license/seat-cap%"


def collect_seat_cap_history(
    session: Session, tenant_id: str, *, limit: Optional[int] = None
) -> Dict[str, Any]:
    """Return a tenant's seat-cap change history, newest first (read-only).

    Shape: ``{"tenant_id", "changes": [{"created_at", "max_users", "cleared"}], "count"}``.
    ``max_users`` is the int the cap was set to, or ``None`` with ``cleared=True`` for an
    explicit clear (``seats: null`` -> unlimited). An unknown tenant returns an empty
    list (**no existence leak**), not an error. Blank ``tenant_id`` raises ``ValueError``
    so callers can map it to their own surface error (HTTP 400 / CLI exit 1).
    """
    tid = (tenant_id or "").strip()
    if not tid:
        raise ValueError("tenant_id is required (got blank/whitespace)")
    query = (
        session.query(AuditLog)
        .filter(AuditLog.method == "LICENSE")
        .filter(AuditLog.tenant_id == tid)
        .filter(AuditLog.path.like(_SEAT_CAP_PATH_LIKE))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    changes = []
    for row in query.all():
        raw = row.path.split("max_users=", 1)[1] if "max_users=" in row.path else ""
        cleared = raw == "cleared"
        try:
            max_users = None if (cleared or not raw) else int(raw)
        except ValueError:
            max_users = None
        changes.append(
            {
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "max_users": max_users,
                "cleared": cleared,
            }
        )
    return {"tenant_id": tid, "changes": changes, "count": len(changes)}
