"""add publication outbox worker columns (G2 R2 worker daemon)

Revision ID: erp_pub_outbox_002
Revises: erp_pub_outbox_001
Create Date: 2026-05-29 00:00:00.000000

Adds the worker claim/scheduling columns to meta_erp_publication_outbox:
worker_id, claimed_at, next_attempt_at. Additive only (no new table).

next_attempt_at is NOT NULL with a due-immediately default. SQLite ALTER TABLE
ADD COLUMN rejects a non-constant (CURRENT_TIMESTAMP) default, so on SQLite we
add nullable -> backfill -> batch-alter to NOT NULL (guard #2). On PostgreSQL a
single NOT NULL + server_default add backfills existing rows with now().
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "erp_pub_outbox_002"
down_revision: Union[str, None] = "erp_pub_outbox_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "meta_erp_publication_outbox"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(_TABLE)}

    if "worker_id" not in existing:
        op.add_column(_TABLE, sa.Column("worker_id", sa.String(), nullable=True))
    if "claimed_at" not in existing:
        op.add_column(
            _TABLE, sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
        )

    if "next_attempt_at" not in existing:
        if bind.dialect.name == "postgresql":
            op.add_column(
                _TABLE,
                sa.Column(
                    "next_attempt_at",
                    sa.DateTime(timezone=True),
                    server_default=sa.func.now(),
                    nullable=False,
                ),
            )
        else:
            # SQLite (and any dialect rejecting non-constant ADD COLUMN defaults):
            # add nullable -> backfill from created_at -> batch-alter to NOT NULL.
            op.add_column(
                _TABLE,
                sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
            )
            op.execute(
                f"UPDATE {_TABLE} SET next_attempt_at = created_at "
                "WHERE next_attempt_at IS NULL"
            )
            with op.batch_alter_table(_TABLE) as batch_op:
                batch_op.alter_column(
                    "next_attempt_at",
                    existing_type=sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.func.now(),
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(_TABLE)}
    for col in ("next_attempt_at", "claimed_at", "worker_id"):
        if col in existing:
            op.drop_column(_TABLE, col)
