"""add date-obsolete impact corrections (append-only review-flag revert audit, #932 DP1 i/ii)

Revision ID: date_obsolete_corr_001
Revises: bom_writeback_audit_002
Create Date: 2026-07-01 00:00:00.000000

Append-only audit for a DateObsoleteImpact review-flag revert (reopen + un-acknowledge). A
separate table on purpose: the worker overwrites DateObsoleteImpact.properties every re-scan,
so an in-row trail would be wiped. Records ONLY the review axis; never child_obsoleted /
Item lifecycle (DP1 iii deferred).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "date_obsolete_corr_001"
down_revision: Union[str, None] = "bom_writeback_audit_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "meta_date_obsolete_impact_corrections"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("impact_id", sa.String(), nullable=False),
            sa.Column("action", sa.String(length=30), nullable=False),
            sa.Column("prior_state", sa.String(length=30), nullable=False),
            sa.Column("prior_acknowledged_at", sa.DateTime(), nullable=True),
            sa.Column("prior_acknowledged_by_id", sa.Integer(), nullable=True),
            sa.Column("reason", sa.String(length=400), nullable=True),
            sa.Column("reverted_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["impact_id"], ["meta_date_obsolete_impacts.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["reverted_by_id"], ["rbac_users.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f(f"ix_{_TABLE}_impact_id"), _TABLE, ["impact_id"], unique=False
        )
        op.create_index(
            op.f(f"ix_{_TABLE}_created_at"), _TABLE, ["created_at"], unique=False
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in set(inspector.get_table_names()):
        op.drop_index(op.f(f"ix_{_TABLE}_created_at"), table_name=_TABLE)
        op.drop_index(op.f(f"ix_{_TABLE}_impact_id"), table_name=_TABLE)
        op.drop_table(_TABLE)
