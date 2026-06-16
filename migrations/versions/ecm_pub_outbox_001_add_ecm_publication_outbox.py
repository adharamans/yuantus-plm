"""add ecm publication outbox table (ECM-P1B)

Revision ID: ecm_pub_outbox_001
Revises: a3_checkout_context_001
Create Date: 2026-06-16 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ecm_pub_outbox_001"
down_revision: Union[str, None] = "a3_checkout_context_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _j() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "meta_ecm_publication_outbox" not in existing:
        op.create_table(
            "meta_ecm_publication_outbox",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("item_id", sa.String(), nullable=False),
            sa.Column("version_id", sa.String(), nullable=False),
            sa.Column("file_id", sa.String(), nullable=False),
            sa.Column("file_role", sa.String(length=60), nullable=False),
            sa.Column("target_system", sa.String(length=120), nullable=False),
            sa.Column("state", sa.String(length=30), nullable=False),
            sa.Column("reason", sa.String(length=30), nullable=True),
            sa.Column("snapshot", _j(), nullable=True),
            sa.Column("payload_fingerprint", sa.String(length=128), nullable=True),
            sa.Column(
                "attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column(
                "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")
            ),
            sa.Column("replay_of", sa.String(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "next_attempt_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("worker_id", sa.String(), nullable=True),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("properties", _j(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["created_by_id"], ["rbac_users.id"]),
            sa.UniqueConstraint(
                "item_id",
                "version_id",
                "file_id",
                "file_role",
                "target_system",
                name="uq_ecm_publication_outbox_identity",
            ),
        )
        op.create_index(
            op.f("ix_meta_ecm_publication_outbox_item_id"),
            "meta_ecm_publication_outbox",
            ["item_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_meta_ecm_publication_outbox_version_id"),
            "meta_ecm_publication_outbox",
            ["version_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "meta_ecm_publication_outbox" in existing:
        op.drop_index(
            op.f("ix_meta_ecm_publication_outbox_version_id"),
            table_name="meta_ecm_publication_outbox",
        )
        op.drop_index(
            op.f("ix_meta_ecm_publication_outbox_item_id"),
            table_name="meta_ecm_publication_outbox",
        )
        op.drop_table("meta_ecm_publication_outbox")
