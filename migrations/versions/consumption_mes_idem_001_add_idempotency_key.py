"""add idempotency_key to meta_consumption_records (Consumption R2 — MES ingestion)

Promotes the R1-derived MES idempotency key from a JSON envelope to a real,
uniquely-constrained column so a retried at-least-once MES delivery is
deduplicated at the database (the manual `/actuals` path keeps a NULL key and is
never deduped). The index name matches the model's auto-generated
``index=True, unique=True`` index so Postgres (this migration) and the test
``create_all`` path agree.

Revision ID: consumption_mes_idem_001
Revises: ecm_pub_outbox_001
Create Date: 2026-06-17 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "consumption_mes_idem_001"
down_revision: Union[str, None] = "ecm_pub_outbox_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "meta_consumption_records"
_INDEX = "ix_meta_consumption_records_idempotency_key"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "idempotency_key" not in columns:
        op.add_column(
            _TABLE,
            sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        )
    indexes = {ix["name"] for ix in inspector.get_indexes(_TABLE)}
    if _INDEX not in indexes:
        op.create_index(
            op.f(_INDEX),
            _TABLE,
            ["idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    indexes = {ix["name"] for ix in inspector.get_indexes(_TABLE)}
    if _INDEX in indexes:
        op.drop_index(op.f(_INDEX), table_name=_TABLE)
    columns = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "idempotency_key" in columns:
        op.drop_column(_TABLE, "idempotency_key")
