"""PLM-COLLAB-P1-A: tenant/org scoping columns on meta_app_licenses (D0-3)

Revision ID: p1a_applic_tenant_001
Revises: erp_pub_outbox_002
Create Date: 2026-06-03 00:00:00.000000

Adds tenant_id + org_id to meta_app_licenses so entitlement licenses are
tenant-scoped (D0-3). Additive only, both columns nullable: existing rows are
NOT backfilled to a default tenant -- the store_service resolver + tenant-scoped
query intentionally refuse to honor a legacy NULL-tenant license, rather than
whitewashing it to "default" (PLM-COLLAB-P1-A acceptance #4). org_id is recorded
only and is not an entitlement filter in P1-A. The tenant baseline
(migrations_tenant/t1_initial_tenant_baseline.py) carries these columns directly;
no separate tenant ALTER revision (schema-per-tenant is rehearsal-only, no live
tenant schema).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p1a_applic_tenant_001"
down_revision: Union[str, None] = "erp_pub_outbox_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "meta_app_licenses"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns(_TABLE)}
    existing_idx = {i["name"] for i in inspector.get_indexes(_TABLE)}

    if "tenant_id" not in existing_cols:
        op.add_column(_TABLE, sa.Column("tenant_id", sa.String(length=64), nullable=True))
    if "org_id" not in existing_cols:
        op.add_column(_TABLE, sa.Column("org_id", sa.String(length=64), nullable=True))
    if "ix_meta_app_licenses_tenant_id" not in existing_idx:
        op.create_index("ix_meta_app_licenses_tenant_id", _TABLE, ["tenant_id"])
    if "ix_meta_app_licenses_org_id" not in existing_idx:
        op.create_index("ix_meta_app_licenses_org_id", _TABLE, ["org_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_idx = {i["name"] for i in inspector.get_indexes(_TABLE)}
    existing_cols = {c["name"] for c in inspector.get_columns(_TABLE)}

    if "ix_meta_app_licenses_org_id" in existing_idx:
        op.drop_index("ix_meta_app_licenses_org_id", table_name=_TABLE)
    if "ix_meta_app_licenses_tenant_id" in existing_idx:
        op.drop_index("ix_meta_app_licenses_tenant_id", table_name=_TABLE)
    if "org_id" in existing_cols:
        op.drop_column(_TABLE, "org_id")
    if "tenant_id" in existing_cols:
        op.drop_column(_TABLE, "tenant_id")
