"""add meta_lifecycle_transition_history (lifecycle transition-history persistence, Slice 1)

A durable audit row per SUCCESSFUL lifecycle promote() (best-effort write; default-on, gated
by LIFECYCLE_TRANSITION_HISTORY_ENABLED). actor_user_id and item_id are intentionally FK-free
(audit immutability + system/automated promotes with an unvalidated user id).

Revision ID: txn_history_001
Revises: c3_date_obsolete_001
Create Date: 2026-06-19 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "txn_history_001"
down_revision: Union[str, None] = "c3_date_obsolete_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "meta_lifecycle_transition_history"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "meta_lifecycle_transition_history",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("from_state_id", sa.String(), nullable=True),
        sa.Column("from_state_name", sa.String(), nullable=True),
        sa.Column("to_state_id", sa.String(), nullable=True),
        sa.Column("to_state_name", sa.String(), nullable=True),
        sa.Column("from_permission_id", sa.String(), nullable=True),
        sa.Column("to_permission_id", sa.String(), nullable=True),
        sa.Column("transition_id", sa.String(), nullable=True),
        sa.Column("lifecycle_map_id", sa.String(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "outcome", sa.String(length=20), nullable=False, server_default=sa.text("'success'")
        ),
        sa.Column(
            "properties", sa.JSON().with_variant(postgresql.JSONB, "postgresql"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_meta_lifecycle_transition_history_item_id"), _TABLE, ["item_id"], unique=False
    )
    op.create_index(
        op.f("ix_meta_lifecycle_transition_history_created_at"),
        _TABLE,
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_meta_lifecycle_transition_history_item_created",
        _TABLE,
        ["item_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_meta_lifecycle_transition_history_item_created", table_name=_TABLE)
    op.drop_index(op.f("ix_meta_lifecycle_transition_history_created_at"), table_name=_TABLE)
    op.drop_index(op.f("ix_meta_lifecycle_transition_history_item_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
