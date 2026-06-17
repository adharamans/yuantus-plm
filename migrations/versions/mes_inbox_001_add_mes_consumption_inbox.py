"""add meta_mes_consumption_inbox table (Consumption R2.5 — async ingestion, default-off)

The durable inbound queue for async MES consumption ingestion. Mechanism-only in this slice
(no route wiring, no worker daemon yet), so adding the table changes no runtime behavior.

Revision ID: mes_inbox_001
Revises: consumption_mes_idem_001
Create Date: 2026-06-17 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "mes_inbox_001"
down_revision: Union[str, None] = "consumption_mes_idem_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "meta_mes_consumption_inbox"


def _j() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "meta_mes_consumption_inbox",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("mes_event_id", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=60), nullable=False),
        sa.Column("source_id", sa.String(length=120), nullable=True),
        sa.Column("actual_quantity", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("uom", sa.String(length=20), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=True),
        sa.Column("attributes", _j(), nullable=True),
        sa.Column("state", sa.String(length=30), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("worker_id", sa.String(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("record_id", sa.String(), nullable=True),
        sa.Column("properties", _j(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_mes_consumption_inbox_idempotency_key"),
    )
    op.create_index(
        op.f("ix_meta_mes_consumption_inbox_plan_id"), _TABLE, ["plan_id"], unique=False
    )
    op.create_index(op.f("ix_meta_mes_consumption_inbox_state"), _TABLE, ["state"], unique=False)
    op.create_index(
        op.f("ix_meta_mes_consumption_inbox_next_attempt_at"),
        _TABLE,
        ["next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    op.drop_index(op.f("ix_meta_mes_consumption_inbox_next_attempt_at"), table_name=_TABLE)
    op.drop_index(op.f("ix_meta_mes_consumption_inbox_state"), table_name=_TABLE)
    op.drop_index(op.f("ix_meta_mes_consumption_inbox_plan_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
