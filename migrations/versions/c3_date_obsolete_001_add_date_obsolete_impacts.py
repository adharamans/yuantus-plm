"""add meta_date_obsolete_impacts table (CAD-PDM C3 — date-BOM auto-obsolete, default-off)

Where-used impact flags raised when a date effectivity expires. Mechanism-only in this slice
(no worker / route wiring), so the table is created but nothing writes to it at runtime yet.

Revision ID: c3_date_obsolete_001
Revises: mes_inbox_001
Create Date: 2026-06-18 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3_date_obsolete_001"
down_revision: Union[str, None] = "mes_inbox_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "meta_date_obsolete_impacts"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "meta_date_obsolete_impacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("effectivity_id", sa.String(), nullable=False),
        sa.Column("child_item_id", sa.String(), nullable=False),
        sa.Column("parent_item_id", sa.String(), nullable=False),
        sa.Column("child_obsoleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("state", sa.String(length=30), nullable=False, server_default=sa.text("'open'")),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("acknowledged_by_id", sa.Integer(), nullable=True),
        sa.Column("properties", sa.JSON().with_variant(postgresql.JSONB, "postgresql"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["acknowledged_by_id"], ["rbac_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "effectivity_id", "parent_item_id", name="uq_date_obsolete_impact_eff_parent"
        ),
    )
    op.create_index(
        op.f("ix_meta_date_obsolete_impacts_effectivity_id"), _TABLE, ["effectivity_id"], unique=False
    )
    op.create_index(
        op.f("ix_meta_date_obsolete_impacts_child_item_id"), _TABLE, ["child_item_id"], unique=False
    )
    op.create_index(
        op.f("ix_meta_date_obsolete_impacts_parent_item_id"), _TABLE, ["parent_item_id"], unique=False
    )
    op.create_index(
        op.f("ix_meta_date_obsolete_impacts_state"), _TABLE, ["state"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    op.drop_index(op.f("ix_meta_date_obsolete_impacts_state"), table_name=_TABLE)
    op.drop_index(op.f("ix_meta_date_obsolete_impacts_parent_item_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_meta_date_obsolete_impacts_child_item_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_meta_date_obsolete_impacts_effectivity_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
