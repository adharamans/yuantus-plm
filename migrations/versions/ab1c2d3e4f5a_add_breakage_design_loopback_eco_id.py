"""add breakage design-loopback eco_id link column

Revision ID: ab1c2d3e4f5a
Revises: aa1b2c3d4e7b0
Create Date: 2026-05-19 00:00:00.000000

Tier-B #3 §3.2 (taskbook `3e5104f`). Adds a durable
design-loopback ECO link column to `meta_breakage_incidents`.

`eco_id` is a bare `String` column (NO ForeignKey — see the
taskbook §4.1/§4.3 for why: matches the table's existing
soft-link convention and sidesteps the tenant-baseline
FK-ordering problem). The UNIQUE index is a cross-incident
data-integrity backstop only; the race-safety mechanism is the
compare-and-swap UPDATE in the service layer.

Idempotent inspector pattern mirrors
`aa1b2c3d4e7b0_add_workorder_doc_version_lock.py`. Inherits the
repo-wide `alembic upgrade head --sql` offline-mode caveat
(`sa.inspect(bind)` is incompatible with offline mode); live-DB
upgrade is the verification gate.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ab1c2d3e4f5a"
down_revision: Union[str, None] = "aa1b2c3d4e7b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "meta_breakage_incidents"
_NEW_COLUMN = "eco_id"
# Name matches SQLAlchemy's `unique=True, index=True` convention as
# used by the existing `incident_code` column on this same table
# (baseline index `ix_meta_breakage_incidents_incident_code`,
# unique=True). Using the same `ix_...` form keeps model autogen,
# this migration, and the tenant baseline in agreement.
_NEW_UNIQUE = "ix_meta_breakage_incidents_eco_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE)}
    if _NEW_COLUMN not in existing_columns:
        op.add_column(
            _TABLE,
            sa.Column(_NEW_COLUMN, sa.String(), nullable=True),
        )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes(_TABLE)}
    if _NEW_UNIQUE not in existing_indexes:
        op.create_index(
            op.f(_NEW_UNIQUE),
            _TABLE,
            [_NEW_COLUMN],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return

    existing_indexes = {ix["name"] for ix in inspector.get_indexes(_TABLE)}
    if _NEW_UNIQUE in existing_indexes:
        op.drop_index(op.f(_NEW_UNIQUE), table_name=_TABLE)

    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE)}
    if _NEW_COLUMN in existing_columns:
        op.drop_column(_TABLE, _NEW_COLUMN)
