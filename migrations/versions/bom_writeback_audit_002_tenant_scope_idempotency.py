"""scope meta_bom_writeback_audit single-use guard per tenant (composite idempotency unique)

Phase-7 follow-up. The P2 single-use / replay guard's UNIQUE was a single global
``idempotency_key``, so the SAME key under two tenants collided -- tenant B replayed or
conflicted on tenant A's key (a cross-tenant correctness defect). This rescopes uniqueness
to ``(tenant_id, idempotency_key)``; the model (``__table_args__``) and the service's replay
re-query change in lockstep so create_all and the migration stay in sync.

Dialect-aware: native ALTER on PostgreSQL, batch (table-rebuild) on SQLite.

Revision ID: bom_writeback_audit_002
Revises: bom_writeback_audit_001
Create Date: 2026-06-29 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bom_writeback_audit_002"
down_revision: Union[str, None] = "bom_writeback_audit_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "meta_bom_writeback_audit"
_OLD = "uq_meta_bom_writeback_audit_idempotency_key"
_NEW = "uq_meta_bom_writeback_audit_tenant_idem"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    if bind.dialect.name == "sqlite":
        # SQLite cannot ALTER a constraint in place -> batch rebuilds the table.
        with op.batch_alter_table(_TABLE, schema=None) as batch_op:
            batch_op.drop_constraint(_OLD, type_="unique")
            batch_op.create_unique_constraint(_NEW, ["tenant_id", "idempotency_key"])
    else:
        op.drop_constraint(_OLD, _TABLE, type_="unique")
        op.create_unique_constraint(_NEW, _TABLE, ["tenant_id", "idempotency_key"])


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in set(sa.inspect(bind).get_table_names()):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, schema=None) as batch_op:
            batch_op.drop_constraint(_NEW, type_="unique")
            batch_op.create_unique_constraint(_OLD, ["idempotency_key"])
    else:
        op.drop_constraint(_NEW, _TABLE, type_="unique")
        op.create_unique_constraint(_OLD, _TABLE, ["idempotency_key"])
