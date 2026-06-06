"""B1: ItemVersion.is_superseded + concurrent-revision open-current partial-unique guard

Revision ID: b1_supersede_001
Revises: wp13_cad_stale_001
Create Date: 2026-06-06 00:00:00.000000

Adds the version-replacement signal ``is_superseded`` and the D4b concurrent-revision
guard as a NULL-safe partial-unique index ("at most one open/unreleased current
version per item line + branch"). Forward-only (D8): existing released-not-current
versions are NOT retro-superseded. Normalizes any pre-existing NULLs in
``branch_name``/``is_current``/``is_released`` before tightening them to NOT NULL +
server_default (defensive -- the ORM ``default=`` has always applied, so the
normalize is typically a no-op). The partial-unique index here is kept lock-step
with ``ItemVersion.__table_args__`` (same name/columns/predicate).

NULL-normalize assumption (stated, not silent): it is a provable no-op for any row
``VersionService`` created (the ORM column ``default=`` has always applied); it only
touches pre-ORM-default / raw-inserted rows. An ``is_current``-NULL row normalized to
``false`` would, if ``item.current_version_id`` still points at it, leave that item
with ZERO current versions -- a surfaced data-shape change, NOT a constraint
violation. Forward-only (D8): not auto-repaired here.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b1_supersede_001"
down_revision: Union[str, None] = "wp13_cad_stale_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "meta_item_versions"
_COL = "is_superseded"
_UQ = "uq_itemversion_open_current_per_line"
_IX = "ix_meta_item_versions_is_superseded"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns(_TABLE)}
    if _COL in cols:
        return  # idempotent

    is_sqlite = bind.dialect.name == "sqlite"
    false_lit = "0" if is_sqlite else "false"

    # 1. Normalize pre-existing NULLs BEFORE tightening to NOT NULL. is_current /
    #    is_released NULL -> false keeps anomalous rows OUT of the partial predicate
    #    (constraint-safe); branch_name NULL -> 'main'.
    op.execute(
        sa.text(f"UPDATE {_TABLE} SET branch_name = 'main' WHERE branch_name IS NULL")
    )
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET is_released = {false_lit} WHERE is_released IS NULL"
        )
    )
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET is_current = {false_lit} WHERE is_current IS NULL"
        )
    )

    new_col = sa.Column(_COL, sa.Boolean(), nullable=False, server_default=sa.false())
    if is_sqlite:
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            batch.add_column(new_col)
            batch.alter_column(
                "is_current",
                existing_type=sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
            batch.alter_column(
                "is_released",
                existing_type=sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
            batch.alter_column(
                "branch_name",
                existing_type=sa.String(length=100),
                nullable=False,
                server_default="main",
            )
    else:
        op.add_column(_TABLE, new_col)
        op.alter_column(
            _TABLE,
            "is_current",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        )
        op.alter_column(
            _TABLE,
            "is_released",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )
        op.alter_column(
            _TABLE,
            "branch_name",
            existing_type=sa.String(length=100),
            nullable=False,
            server_default="main",
        )

    # Simple index on is_superseded (matches model index=True; perf for the
    # "active released" = is_released AND NOT is_superseded query).
    op.create_index(_IX, _TABLE, ["is_superseded"], unique=False)

    # 2. D4b guard: at most one open (unreleased) current version per item line +
    #    branch. NULL-safe via COALESCE; partial predicate per dialect. Lock-step
    #    with ItemVersion.__table_args__.
    op.create_index(
        _UQ,
        _TABLE,
        ["item_id", sa.text("coalesce(branch_name, 'main')")],
        unique=True,
        sqlite_where=sa.text("is_current = 1 AND is_released = 0"),
        postgresql_where=sa.text("is_current IS TRUE AND is_released IS NOT TRUE"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns(_TABLE)}
    if _COL not in cols:
        return

    op.drop_index(_UQ, table_name=_TABLE)
    op.drop_index(_IX, table_name=_TABLE)

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            batch.drop_column(_COL)
    else:
        op.drop_column(_TABLE, _COL)
