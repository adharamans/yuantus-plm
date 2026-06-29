"""add meta_bom_writeback_audit table (Phase-7 BOM write-back — governed audit + replay)

One row simultaneously serves the P2 single-use/replay guard (idempotency_key NOT
NULL UNIQUE) and the P3 write-back domain audit (before/after touched-cell diff).
Mechanism table only in this slice; route wiring lands separately.

Revision ID: bom_writeback_audit_001
Revises: txn_history_001
Create Date: 2026-06-29 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bom_writeback_audit_001"
down_revision: Union[str, None] = "txn_history_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "meta_bom_writeback_audit"


def _j() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "meta_bom_writeback_audit",  # literal name (CI migration-coverage contract scans for it)
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("org_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("part_id", sa.String(), nullable=False),
        sa.Column("bom_line_id", sa.String(), nullable=False),
        sa.Column("before", _j(), nullable=True),
        sa.Column("after", _j(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'applied'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_meta_bom_writeback_audit_idempotency_key"),
    )
    # NOTE: no separate index on idempotency_key — the UniqueConstraint already
    # backs it with a unique index (enforcement + lookup), mirroring mes_inbox_001.
    op.create_index(
        op.f("ix_meta_bom_writeback_audit_tenant_id"), _TABLE, ["tenant_id"], unique=False
    )
    op.create_index(op.f("ix_meta_bom_writeback_audit_org_id"), _TABLE, ["org_id"], unique=False)
    op.create_index(op.f("ix_meta_bom_writeback_audit_user_id"), _TABLE, ["user_id"], unique=False)
    op.create_index(op.f("ix_meta_bom_writeback_audit_part_id"), _TABLE, ["part_id"], unique=False)
    op.create_index(
        op.f("ix_meta_bom_writeback_audit_bom_line_id"), _TABLE, ["bom_line_id"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    op.drop_index(op.f("ix_meta_bom_writeback_audit_bom_line_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_meta_bom_writeback_audit_part_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_meta_bom_writeback_audit_user_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_meta_bom_writeback_audit_org_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_meta_bom_writeback_audit_tenant_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
