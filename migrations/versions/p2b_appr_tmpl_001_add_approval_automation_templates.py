"""PLM-COLLAB-P2-B: meta_approval_automation_templates (provisioned draft templates)

Revision ID: p2b_appr_tmpl_001
Revises: p1a_applic_tenant_001
Create Date: 2026-06-03 00:00:00.000000

New tenant-scoped table holding provisioned approval-automation template DRAFTS
(config skeletons). P2-B only provisions ``draft`` rows; it does not enable or
execute. The unique ``(tenant_id, template_key)`` constraint makes provisioning
idempotent (re-provision upserts, never duplicates). The tenant baseline
(migrations_tenant/t1_initial_tenant_baseline.py) carries the same create_table
directly -- no separate tenant revision (schema-per-tenant is rehearsal-only, no
live tenant schema). Idempotent (guards on existing table) so it is safe on a DB
already created via create_all.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "p2b_appr_tmpl_001"
down_revision: Union[str, None] = "p1a_applic_tenant_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "meta_approval_automation_templates"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        return
    # NOTE: the table name is a string LITERAL here (not the _TABLE variable) so the
    # migration-table-coverage contract's create-table scanner can detect this table.
    op.create_table(
        "meta_approval_automation_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(length=120), nullable=False),
        sa.Column("template_key", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column(
            "definition_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "template_key", name="uq_approval_automation_template_scope"
        ),
    )
    op.create_index(
        "ix_meta_approval_automation_templates_tenant_id", _TABLE, ["tenant_id"]
    )
    op.create_index(
        "ix_meta_approval_automation_templates_template_key", _TABLE, ["template_key"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        return
    op.drop_index(
        "ix_meta_approval_automation_templates_template_key", table_name=_TABLE
    )
    op.drop_index(
        "ix_meta_approval_automation_templates_tenant_id", table_name=_TABLE
    )
    op.drop_table(_TABLE)
