"""Initial tenant baseline.

P3.3.3: deterministic SQLAlchemy-metadata-derived baseline created via
scripts/generate_tenant_baseline.py against the PostgreSQL dialect.
Cross-schema foreign keys (targeting GLOBAL_TABLE_NAMES tables such as
rbac_users, users) are stripped — the columns are preserved without
the FK constraint because the referenced global tables live in a
separate identity-plane schema.

Revision ID: t1_initial_tenant_baseline
Revises: 
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "t1_initial_tenant_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('cad_change_logs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('file_id', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=True),
    sa.Column('org_id', sa.String(length=64), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_3d_overlays',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('document_item_id', sa.String(), nullable=False),
    sa.Column('version_label', sa.String(length=120), nullable=True),
    sa.Column('status', sa.String(length=60), nullable=True),
    sa.Column('visibility_role', sa.String(length=120), nullable=True),
    sa.Column('part_refs', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_app_registry',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('version', sa.String(length=50), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('author', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('dependencies', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('installed_content', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('installed_at', sa.DateTime(), nullable=True),
    sa.Column('installed_by_id', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='uq_app_name')
    )
    op.create_table('meta_approval_categories',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('parent_id', sa.String(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['meta_approval_categories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_breakage_incidents',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('incident_code', sa.String(length=40), nullable=True),
    sa.Column('product_item_id', sa.String(), nullable=True),
    sa.Column('bom_id', sa.String(), nullable=True),
    sa.Column('bom_line_item_id', sa.String(), nullable=True),
    sa.Column('production_order_id', sa.String(length=120), nullable=True),
    sa.Column('version_id', sa.String(), nullable=True),
    sa.Column('mbom_id', sa.String(), nullable=True),
    sa.Column('routing_id', sa.String(length=120), nullable=True),
    sa.Column('batch_code', sa.String(length=120), nullable=True),
    sa.Column('customer_name', sa.String(length=200), nullable=True),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('responsibility', sa.String(length=120), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('severity', sa.String(length=30), nullable=False),
    sa.Column('eco_id', sa.String(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_consumption_plans',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('item_id', sa.String(), nullable=True),
    sa.Column('period_unit', sa.String(length=20), nullable=False),
    sa.Column('period_start', sa.DateTime(), nullable=True),
    sa.Column('period_end', sa.DateTime(), nullable=True),
    sa.Column('planned_quantity', sa.Float(), nullable=False),
    sa.Column('uom', sa.String(length=20), nullable=False),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_consumption_records',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('plan_id', sa.String(), nullable=False),
    sa.Column('source_type', sa.String(length=60), nullable=False),
    sa.Column('source_id', sa.String(length=120), nullable=True),
    sa.Column('actual_quantity', sa.Float(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_conversion_jobs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('task_type', sa.String(length=50), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=True),
    sa.Column('worker_id', sa.String(length=100), nullable=True),
    sa.Column('attempt_count', sa.Integer(), nullable=True),
    sa.Column('max_attempts', sa.Integer(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('dedupe_key', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('scheduled_at', sa.DateTime(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_dashboards',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('layout', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('widgets', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('auto_refresh', sa.Boolean(), nullable=True),
    sa.Column('refresh_interval', sa.Integer(), nullable=True),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.Column('is_public', sa.Boolean(), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_eco_activity_gate_events',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('eco_id', sa.String(), nullable=False),
    sa.Column('activity_id', sa.String(), nullable=False),
    sa.Column('from_status', sa.String(length=30), nullable=True),
    sa.Column('to_status', sa.String(length=30), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_eco_activity_gates',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('eco_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('is_blocking', sa.Boolean(), nullable=False),
    sa.Column('assignee_id', sa.Integer(), nullable=True),
    sa.Column('depends_on_activity_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.Column('closed_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_eco_stages',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=True),
    sa.Column('fold', sa.Boolean(), nullable=True),
    sa.Column('is_blocking', sa.Boolean(), nullable=True),
    sa.Column('approval_type', sa.String(length=20), nullable=True),
    sa.Column('approval_roles', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('min_approvals', sa.Integer(), nullable=True),
    sa.Column('sla_hours', sa.Integer(), nullable=True),
    sa.Column('auto_progress', sa.Boolean(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('company_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_extension_points',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('meta_forms',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('html_content', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_grid_views',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_lifecycle_maps',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('description', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_maintenance_categories',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('parent_id', sa.String(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['meta_maintenance_categories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_methods',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('type', sa.String(), nullable=True),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('description', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_numbering_sequences',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_type_id', sa.String(length=120), nullable=False),
    sa.Column('tenant_id', sa.String(length=120), nullable=False),
    sa.Column('org_id', sa.String(length=120), nullable=False),
    sa.Column('prefix', sa.String(length=120), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('last_value', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('item_type_id', 'tenant_id', 'org_id', 'prefix', name='uq_numbering_sequence_scope')
    )
    op.create_table('meta_permissions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_plugin_configs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('plugin_id', sa.String(length=120), nullable=False),
    sa.Column('tenant_id', sa.String(length=120), nullable=False),
    sa.Column('org_id', sa.String(length=120), nullable=False),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('plugin_id', 'tenant_id', 'org_id', name='uq_plugin_config_scope')
    )
    op.create_table('meta_remote_sites',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('endpoint', sa.String(length=500), nullable=False),
    sa.Column('auth_mode', sa.String(length=50), nullable=False),
    sa.Column('auth_secret_ciphertext', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('metadata_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('last_health_status', sa.String(length=30), nullable=True),
    sa.Column('last_health_error', sa.Text(), nullable=True),
    sa.Column('last_health_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_report_definitions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('code', sa.String(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('category', sa.String(), nullable=True),
    sa.Column('report_type', sa.String(), nullable=True),
    sa.Column('data_source', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('layout', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('parameters', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.Column('is_public', sa.Boolean(), nullable=True),
    sa.Column('allowed_roles', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('meta_report_locale_profiles',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('lang', sa.String(length=10), nullable=False),
    sa.Column('fallback_lang', sa.String(length=10), nullable=True),
    sa.Column('number_format', sa.String(length=50), nullable=True),
    sa.Column('date_format', sa.String(length=50), nullable=True),
    sa.Column('time_format', sa.String(length=50), nullable=True),
    sa.Column('timezone', sa.String(length=80), nullable=True),
    sa.Column('paper_size', sa.String(length=20), nullable=True),
    sa.Column('orientation', sa.String(length=20), nullable=True),
    sa.Column('header_text', sa.Text(), nullable=True),
    sa.Column('footer_text', sa.Text(), nullable=True),
    sa.Column('logo_path', sa.String(), nullable=True),
    sa.Column('report_type', sa.String(length=120), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_store_listings',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=True),
    sa.Column('latest_version', sa.String(length=50), nullable=True),
    sa.Column('display_name', sa.String(length=200), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=50), nullable=True),
    sa.Column('price_model', sa.String(length=50), nullable=True),
    sa.Column('price_amount', sa.Integer(), nullable=True),
    sa.Column('icon_url', sa.String(length=500), nullable=True),
    sa.Column('publisher', sa.String(length=100), nullable=True),
    sa.Column('last_synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_subcontract_approval_role_mappings',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('role_code', sa.String(length=100), nullable=False),
    sa.Column('scope_type', sa.String(length=30), nullable=False),
    sa.Column('scope_value', sa.String(length=200), nullable=True),
    sa.Column('owner', sa.String(length=200), nullable=True),
    sa.Column('team', sa.String(length=200), nullable=True),
    sa.Column('required', sa.Boolean(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('fallback_role', sa.String(length=100), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_sync_sites',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('base_url', sa.String(length=500), nullable=True),
    sa.Column('site_code', sa.String(length=60), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('auth_type', sa.String(length=30), nullable=True),
    sa.Column('auth_config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('direction', sa.String(length=30), nullable=False),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_code')
    )
    op.create_table('meta_translations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('record_type', sa.String(length=120), nullable=False),
    sa.Column('record_id', sa.String(), nullable=False),
    sa.Column('field_name', sa.String(length=120), nullable=False),
    sa.Column('lang', sa.String(length=10), nullable=False),
    sa.Column('source_value', sa.Text(), nullable=True),
    sa.Column('translated_value', sa.Text(), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=True),
    sa.Column('module', sa.String(length=120), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('record_type', 'record_id', 'field_name', 'lang', name='uq_translation_key')
    )
    op.create_table('meta_vaults',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('storage_type', sa.String(), nullable=True),
    sa.Column('base_path', sa.String(), nullable=True),
    sa.Column('url_template', sa.String(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_workcenters',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('code', sa.String(length=120), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('plant_code', sa.String(length=120), nullable=True),
    sa.Column('department_code', sa.String(length=120), nullable=True),
    sa.Column('capacity_per_day', sa.Float(), nullable=True),
    sa.Column('efficiency', sa.Float(), nullable=True),
    sa.Column('utilization', sa.Float(), nullable=True),
    sa.Column('machine_count', sa.Integer(), nullable=True),
    sa.Column('worker_count', sa.Integer(), nullable=True),
    sa.Column('cost_center', sa.String(length=120), nullable=True),
    sa.Column('labor_rate', sa.Float(), nullable=True),
    sa.Column('overhead_rate', sa.Float(), nullable=True),
    sa.Column('scheduling_type', sa.String(length=50), nullable=True),
    sa.Column('queue_time_default', sa.Float(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('meta_workflow_custom_action_rules',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('target_object', sa.String(length=60), nullable=False),
    sa.Column('workflow_map_id', sa.String(), nullable=True),
    sa.Column('from_state', sa.String(length=120), nullable=True),
    sa.Column('to_state', sa.String(length=120), nullable=True),
    sa.Column('trigger_phase', sa.String(length=30), nullable=False),
    sa.Column('action_type', sa.String(length=80), nullable=False),
    sa.Column('action_params', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('fail_strategy', sa.String(length=30), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('meta_workflow_custom_action_runs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('rule_id', sa.String(), nullable=False),
    sa.Column('object_id', sa.String(), nullable=False),
    sa.Column('target_object', sa.String(length=60), nullable=False),
    sa.Column('from_state', sa.String(length=120), nullable=True),
    sa.Column('to_state', sa.String(length=120), nullable=True),
    sa.Column('trigger_phase', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_workflow_maps',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('meta_workorder_document_links',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('routing_id', sa.String(), nullable=False),
    sa.Column('operation_id', sa.String(), nullable=True),
    sa.Column('document_item_id', sa.String(), nullable=False),
    sa.Column('inherit_to_children', sa.Boolean(), nullable=False),
    sa.Column('visible_in_production', sa.Boolean(), nullable=False),
    sa.Column('document_version_id', sa.String(), nullable=True),
    sa.Column('version_locked_at', sa.DateTime(), nullable=True),
    sa.Column('version_lock_source', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('routing_id', 'operation_id', 'document_item_id', name='uq_workorder_doc_link_scope')
    )
    op.create_table('meta_access',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('permission_id', sa.String(), nullable=True),
    sa.Column('identity_id', sa.String(), nullable=True),
    sa.Column('can_create', sa.Boolean(), nullable=True),
    sa.Column('can_get', sa.Boolean(), nullable=True),
    sa.Column('can_update', sa.Boolean(), nullable=True),
    sa.Column('can_delete', sa.Boolean(), nullable=True),
    sa.Column('can_discover', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['permission_id'], ['meta_permissions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_app_licenses',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('app_registry_id', sa.String(), nullable=True),
    sa.Column('app_name', sa.String(length=100), nullable=False),
    sa.Column('license_key', sa.String(length=100), nullable=False),
    sa.Column('plan_type', sa.String(length=50), nullable=True),
    sa.Column('issued_at', sa.DateTime(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('license_data', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['app_registry_id'], ['meta_app_registry.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('license_key')
    )
    op.create_table('meta_approval_requests',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('category_id', sa.String(), nullable=True),
    sa.Column('entity_type', sa.String(length=100), nullable=True),
    sa.Column('entity_id', sa.String(), nullable=True),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('priority', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('requested_by_id', sa.Integer(), nullable=True),
    sa.Column('assigned_to_id', sa.Integer(), nullable=True),
    sa.Column('decided_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['category_id'], ['meta_approval_categories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_extensions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('app_id', sa.String(), nullable=False),
    sa.Column('point_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=True),
    sa.Column('handler', sa.String(length=200), nullable=True),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['app_id'], ['meta_app_registry.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['point_id'], ['meta_extension_points.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_files',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('filename', sa.String(), nullable=False),
    sa.Column('file_type', sa.String(), nullable=True),
    sa.Column('mime_type', sa.String(), nullable=True),
    sa.Column('file_size', sa.BigInteger(), nullable=True),
    sa.Column('author', sa.String(), nullable=True),
    sa.Column('source_system', sa.String(), nullable=True),
    sa.Column('source_version', sa.String(), nullable=True),
    sa.Column('document_version', sa.String(), nullable=True),
    sa.Column('checksum', sa.String(), nullable=True),
    sa.Column('vault_id', sa.String(), nullable=True),
    sa.Column('system_path', sa.String(), nullable=False),
    sa.Column('document_type', sa.String(), nullable=True),
    sa.Column('is_native_cad', sa.Boolean(), nullable=True),
    sa.Column('cad_format', sa.String(), nullable=True),
    sa.Column('cad_connector_id', sa.String(), nullable=True),
    sa.Column('cad_attributes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('cad_attributes_source', sa.String(), nullable=True),
    sa.Column('cad_attributes_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cad_properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('cad_properties_source', sa.String(), nullable=True),
    sa.Column('cad_properties_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cad_view_state', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('cad_view_state_source', sa.String(), nullable=True),
    sa.Column('cad_view_state_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('preview_path', sa.String(), nullable=True),
    sa.Column('preview_data', sa.Text(), nullable=True),
    sa.Column('geometry_path', sa.String(), nullable=True),
    sa.Column('printout_path', sa.String(), nullable=True),
    sa.Column('cad_document_path', sa.String(), nullable=True),
    sa.Column('cad_manifest_path', sa.String(), nullable=True),
    sa.Column('cad_metadata_path', sa.String(), nullable=True),
    sa.Column('cad_bom_path', sa.String(), nullable=True),
    sa.Column('cad_dedup_path', sa.String(), nullable=True),
    sa.Column('cad_document_schema_version', sa.Integer(), nullable=True),
    sa.Column('cad_review_state', sa.String(), nullable=True),
    sa.Column('cad_review_note', sa.Text(), nullable=True),
    sa.Column('cad_review_by_id', sa.Integer(), nullable=True),
    sa.Column('cad_reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('conversion_status', sa.String(), nullable=True),
    sa.Column('conversion_error', sa.Text(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_file_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['source_file_id'], ['meta_files.id'], ),
    sa.ForeignKeyConstraint(['vault_id'], ['meta_vaults.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_form_fields',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('form_id', sa.String(), nullable=True),
    sa.Column('property_name', sa.String(), nullable=True),
    sa.Column('label', sa.String(), nullable=True),
    sa.Column('x_pos', sa.Integer(), nullable=True),
    sa.Column('y_pos', sa.Integer(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('control_type', sa.String(), nullable=True),
    sa.Column('on_change_handler', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['form_id'], ['meta_forms.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_grid_columns',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('grid_view_id', sa.String(), nullable=True),
    sa.Column('property_name', sa.String(), nullable=True),
    sa.Column('label', sa.String(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['grid_view_id'], ['meta_grid_views.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_item_types',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('label', sa.String(), nullable=True),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('uuid', sa.String(), nullable=True, comment='Stable UUID for ItemType'),
    sa.Column('is_relationship', sa.Boolean(), nullable=True),
    sa.Column('is_versionable', sa.Boolean(), nullable=True),
    sa.Column('version_control_enabled', sa.Boolean(), nullable=True),
    sa.Column('revision_scheme', sa.String(length=50), nullable=True, comment='Revisioning scheme (e.g., A-Z, 1.2.3)'),
    sa.Column('permission_id', sa.String(), nullable=True),
    sa.Column('lifecycle_map_id', sa.String(), nullable=True),
    sa.Column('properties_schema', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True, comment='JSON Schema definition of properties'),
    sa.Column('ui_layout', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True, comment='UI layout configuration (Form, List, Search)'),
    sa.Column('methods', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True, comment='Server-side method definitions'),
    sa.Column('on_before_add_method_id', sa.String(), nullable=True),
    sa.Column('on_after_update_method_id', sa.String(), nullable=True),
    sa.Column('source_item_type_id', sa.String(), nullable=True),
    sa.Column('related_item_type_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['lifecycle_map_id'], ['meta_lifecycle_maps.id'], ),
    sa.ForeignKeyConstraint(['related_item_type_id'], ['meta_item_types.id'], ),
    sa.ForeignKeyConstraint(['source_item_type_id'], ['meta_item_types.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_lifecycle_states',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('lifecycle_map_id', sa.String(), nullable=True),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('label', sa.String(length=100), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=True),
    sa.Column('is_start_state', sa.Boolean(), nullable=True),
    sa.Column('is_end_state', sa.Boolean(), nullable=True),
    sa.Column('is_released', sa.Boolean(), nullable=True),
    sa.Column('is_suspended', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('version_lock', sa.Boolean(), nullable=True),
    sa.Column('permission_id', sa.String(), nullable=True),
    sa.Column('default_permission_id', sa.String(), nullable=True, comment='进入此状态时Item获得的默认权限'),
    sa.Column('workflow_map_id', sa.String(), nullable=True, comment='进入此状态时自动触发的工作流'),
    sa.ForeignKeyConstraint(['default_permission_id'], ['meta_permissions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['lifecycle_map_id'], ['meta_lifecycle_maps.id'], ),
    sa.ForeignKeyConstraint(['permission_id'], ['meta_permissions.id'], ),
    sa.ForeignKeyConstraint(['workflow_map_id'], ['meta_workflow_maps.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_maintenance_equipment',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('serial_number', sa.String(length=200), nullable=True),
    sa.Column('model', sa.String(length=200), nullable=True),
    sa.Column('manufacturer', sa.String(length=200), nullable=True),
    sa.Column('category_id', sa.String(), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('location', sa.String(length=400), nullable=True),
    sa.Column('plant_code', sa.String(length=120), nullable=True),
    sa.Column('workcenter_id', sa.String(), nullable=True),
    sa.Column('purchase_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('warranty_expiry', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expected_mtbf_days', sa.Float(), nullable=True),
    sa.Column('owner_user_id', sa.Integer(), nullable=True),
    sa.Column('team_name', sa.String(length=200), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['category_id'], ['meta_maintenance_categories.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('serial_number')
    )
    op.create_table('meta_relationship_types',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('source_item_type', sa.String(length=100), nullable=False, comment='源ItemType名称'),
    sa.Column('related_item_type', sa.String(length=100), nullable=False, comment='目标ItemType名称'),
    sa.Column('is_polymorphic', sa.Boolean(), nullable=False, comment='是否允许多态（related可以是子类型）'),
    sa.Column('cascade_delete', sa.Boolean(), nullable=False, comment='删除source时是否级联删除关系'),
    sa.Column('max_quantity', sa.Integer(), nullable=True, comment='最大关系数量，null表示无限'),
    sa.Column('property_definitions', sa.JSON(), nullable=True, comment='关系自身的属性定义'),
    sa.Column('lifecycle_map_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['lifecycle_map_id'], ['meta_lifecycle_maps.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name'),
    comment='Deprecated: use ItemType.is_relationship instead.'
    )
    op.create_table('meta_report_executions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('report_id', sa.String(), nullable=False),
    sa.Column('parameters_used', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('status', sa.String(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('row_count', sa.Integer(), nullable=True),
    sa.Column('execution_time_ms', sa.Integer(), nullable=True),
    sa.Column('export_format', sa.String(), nullable=True),
    sa.Column('export_path', sa.String(), nullable=True),
    sa.Column('executed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('executed_by_id', sa.Integer(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['report_id'], ['meta_report_definitions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_sync_jobs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('site_id', sa.String(), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('direction', sa.String(length=30), nullable=False),
    sa.Column('document_filter', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('total_documents', sa.Integer(), nullable=False),
    sa.Column('synced_count', sa.Integer(), nullable=False),
    sa.Column('conflict_count', sa.Integer(), nullable=False),
    sa.Column('error_count', sa.Integer(), nullable=False),
    sa.Column('skipped_count', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['meta_sync_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_workflow_activities',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workflow_map_id', sa.String(), nullable=True),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('type', sa.String(), nullable=True),
    sa.Column('is_voting', sa.Boolean(), nullable=True),
    sa.Column('assignee_type', sa.String(), nullable=True),
    sa.Column('role_id', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('dynamic_identity', sa.String(), nullable=True),
    sa.Column('ui_data', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['workflow_map_id'], ['meta_workflow_maps.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_approval_request_events',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('request_id', sa.String(), nullable=False),
    sa.Column('event_type', sa.String(length=30), nullable=False),
    sa.Column('transition_type', sa.String(length=30), nullable=True),
    sa.Column('from_state', sa.String(length=30), nullable=True),
    sa.Column('to_state', sa.String(length=30), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['request_id'], ['meta_approval_requests.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_config_option_sets',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=True),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('value_type', sa.String(length=50), nullable=True),
    sa.Column('allow_multiple', sa.Boolean(), nullable=True),
    sa.Column('is_required', sa.Boolean(), nullable=True),
    sa.Column('default_value', sa.String(length=200), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=True),
    sa.Column('item_type_id', sa.String(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['item_type_id'], ['meta_item_types.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_dedup_rules',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('item_type_id', sa.String(), nullable=True),
    sa.Column('document_type', sa.String(), nullable=True),
    sa.Column('phash_threshold', sa.Integer(), nullable=True),
    sa.Column('feature_threshold', sa.Float(), nullable=True),
    sa.Column('combined_threshold', sa.Float(), nullable=True),
    sa.Column('detection_mode', sa.String(), nullable=True),
    sa.Column('auto_create_relationship', sa.Boolean(), nullable=True),
    sa.Column('auto_trigger_workflow', sa.Boolean(), nullable=True),
    sa.Column('workflow_map_id', sa.String(), nullable=True),
    sa.Column('exclude_patterns', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['item_type_id'], ['meta_item_types.id'], ),
    sa.ForeignKeyConstraint(['workflow_map_id'], ['meta_workflow_maps.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('meta_items',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_type_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('config_id', sa.String(), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('state', sa.String(), nullable=True),
    sa.Column('current_state', sa.String(), nullable=True),
    sa.Column('is_versionable', sa.Boolean(), nullable=True),
    sa.Column('current_version_id', sa.String(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('modified_by_id', sa.Integer(), nullable=True),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.Column('locked_by_id', sa.Integer(), nullable=True),
    sa.Column('permission_id', sa.String(), nullable=True),
    sa.Column('source_id', sa.String(), nullable=True),
    sa.Column('related_id', sa.String(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['current_state'], ['meta_lifecycle_states.id'], ),
    sa.ForeignKeyConstraint(['current_version_id'], ['meta_item_versions.id'], name='fk_item_current_version', use_alter=True),
    sa.ForeignKeyConstraint(['item_type_id'], ['meta_item_types.id'], ),
    sa.ForeignKeyConstraint(['related_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['source_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_lifecycle_transitions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('lifecycle_map_id', sa.String(), nullable=True),
    sa.Column('from_state_id', sa.String(), nullable=True),
    sa.Column('to_state_id', sa.String(), nullable=True),
    sa.Column('action_name', sa.String(length=50), nullable=True),
    sa.Column('role_allowed_id', sa.Integer(), nullable=True),
    sa.Column('condition', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['from_state_id'], ['meta_lifecycle_states.id'], ),
    sa.ForeignKeyConstraint(['lifecycle_map_id'], ['meta_lifecycle_maps.id'], ),
    sa.ForeignKeyConstraint(['to_state_id'], ['meta_lifecycle_states.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_maintenance_requests',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('equipment_id', sa.String(), nullable=False),
    sa.Column('maintenance_type', sa.String(length=30), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('priority', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('scheduled_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_hours', sa.Float(), nullable=True),
    sa.Column('assigned_user_id', sa.Integer(), nullable=True),
    sa.Column('team_name', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['equipment_id'], ['meta_maintenance_equipment.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_properties',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_type_id', sa.String(), nullable=True),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('label', sa.String(), nullable=True),
    sa.Column('data_type', sa.String(), nullable=True),
    sa.Column('length', sa.Integer(), nullable=True),
    sa.Column('is_required', sa.Boolean(), nullable=True),
    sa.Column('default_value', sa.String(), nullable=True),
    sa.Column('ui_type', sa.String(length=50), nullable=True, comment='Frontend UI widget type'),
    sa.Column('ui_options', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True, comment='Frontend UI widget options'),
    sa.Column('is_cad_synced', sa.Boolean(), nullable=True, comment='True if this property is synced from CAD data'),
    sa.Column('default_value_expression', sa.Text(), nullable=True, comment='Expression for dynamic default value'),
    sa.Column('data_source_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['data_source_id'], ['meta_item_types.id'], ),
    sa.ForeignKeyConstraint(['item_type_id'], ['meta_item_types.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_revision_schemes',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('scheme_type', sa.String(length=20), nullable=False),
    sa.Column('initial_revision', sa.String(length=10), nullable=True),
    sa.Column('max_number_before_rollover', sa.Integer(), nullable=True),
    sa.Column('item_type_id', sa.String(), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['item_type_id'], ['meta_item_types.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_saved_searches',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.Column('is_public', sa.Boolean(), nullable=True),
    sa.Column('item_type_id', sa.String(), nullable=True),
    sa.Column('criteria', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('display_columns', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('page_size', sa.Integer(), nullable=True),
    sa.Column('use_count', sa.Integer(), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['item_type_id'], ['meta_item_types.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_signing_reasons',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('code', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('meaning', sa.String(), nullable=True),
    sa.Column('regulatory_reference', sa.String(), nullable=True),
    sa.Column('item_type_id', sa.String(), nullable=True),
    sa.Column('from_state', sa.String(), nullable=True),
    sa.Column('to_state', sa.String(), nullable=True),
    sa.Column('requires_password', sa.Boolean(), nullable=True),
    sa.Column('requires_comment', sa.Boolean(), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['item_type_id'], ['meta_item_types.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('meta_state_identity_permissions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('state_id', sa.String(), nullable=False),
    sa.Column('identity_type', sa.String(length=20), nullable=False, comment='dynamic|role'),
    sa.Column('identity_value', sa.String(length=50), nullable=False, comment='动态身份名或角色ID'),
    sa.Column('can_read', sa.Boolean(), nullable=False),
    sa.Column('can_update', sa.Boolean(), nullable=False),
    sa.Column('can_delete', sa.Boolean(), nullable=False),
    sa.Column('can_promote', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['state_id'], ['meta_lifecycle_states.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_sync_records',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('job_id', sa.String(), nullable=False),
    sa.Column('document_id', sa.String(), nullable=False),
    sa.Column('source_checksum', sa.String(length=128), nullable=True),
    sa.Column('target_checksum', sa.String(length=128), nullable=True),
    sa.Column('outcome', sa.String(length=30), nullable=False),
    sa.Column('conflict_detail', sa.Text(), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['meta_sync_jobs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_view_mappings',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_type_id', sa.String(), nullable=True),
    sa.Column('form_id', sa.String(), nullable=True),
    sa.Column('grid_view_id', sa.String(), nullable=True),
    sa.Column('identity_id', sa.String(), nullable=True),
    sa.Column('device_type', sa.String(), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['form_id'], ['meta_forms.id'], ),
    sa.ForeignKeyConstraint(['grid_view_id'], ['meta_grid_views.id'], ),
    sa.ForeignKeyConstraint(['item_type_id'], ['meta_item_types.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_workflow_transitions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workflow_map_id', sa.String(), nullable=True),
    sa.Column('from_activity_id', sa.String(), nullable=True),
    sa.Column('to_activity_id', sa.String(), nullable=True),
    sa.Column('condition', sa.String(), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['from_activity_id'], ['meta_workflow_activities.id'], ),
    sa.ForeignKeyConstraint(['to_activity_id'], ['meta_workflow_activities.id'], ),
    sa.ForeignKeyConstraint(['workflow_map_id'], ['meta_workflow_maps.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_box_items',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('box_type', sa.String(length=30), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('width', sa.Float(), nullable=True),
    sa.Column('height', sa.Float(), nullable=True),
    sa.Column('depth', sa.Float(), nullable=True),
    sa.Column('dimension_unit', sa.String(length=20), nullable=False),
    sa.Column('tare_weight', sa.Float(), nullable=True),
    sa.Column('max_gross_weight', sa.Float(), nullable=True),
    sa.Column('weight_unit', sa.String(length=20), nullable=False),
    sa.Column('material', sa.String(length=200), nullable=True),
    sa.Column('barcode', sa.String(length=200), nullable=True),
    sa.Column('max_quantity', sa.Integer(), nullable=True),
    sa.Column('cost', sa.Float(), nullable=True),
    sa.Column('product_id', sa.String(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['product_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_config_options',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('option_set_id', sa.String(), nullable=True),
    sa.Column('key', sa.String(length=120), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=True),
    sa.Column('value', sa.String(length=200), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('ref_item_id', sa.String(), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('extra', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['option_set_id'], ['meta_config_option_sets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ref_item_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('option_set_id', 'key', name='uq_config_option_key')
    )
    op.create_table('meta_dedup_batches',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('scope_type', sa.String(), nullable=True),
    sa.Column('scope_config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('rule_id', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=True),
    sa.Column('total_files', sa.Integer(), nullable=True),
    sa.Column('processed_files', sa.Integer(), nullable=True),
    sa.Column('found_similarities', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('summary', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['rule_id'], ['meta_dedup_rules.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_electronic_signatures',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('item_generation', sa.Integer(), nullable=False),
    sa.Column('signer_id', sa.Integer(), nullable=False),
    sa.Column('signer_username', sa.String(), nullable=False),
    sa.Column('signer_full_name', sa.String(), nullable=False),
    sa.Column('reason_id', sa.String(), nullable=True),
    sa.Column('meaning', sa.String(), nullable=False),
    sa.Column('reason_text', sa.String(), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('signed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('signature_hash', sa.String(), nullable=False),
    sa.Column('content_hash', sa.String(), nullable=False),
    sa.Column('client_ip', sa.String(), nullable=True),
    sa.Column('client_info', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('status', sa.String(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by_id', sa.Integer(), nullable=True),
    sa.Column('revocation_reason', sa.Text(), nullable=True),
    sa.Column('workflow_instance_id', sa.String(), nullable=True),
    sa.Column('workflow_activity_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['reason_id'], ['meta_signing_reasons.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_geometric_indices',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('vector', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('algorithm_version', sa.String(), nullable=True),
    sa.Column('signature_hash', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_item_files',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('file_id', sa.String(), nullable=False),
    sa.Column('file_role', sa.String(), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=True),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['file_id'], ['meta_files.id'], ),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_item_versions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=True),
    sa.Column('revision', sa.String(length=10), nullable=True),
    sa.Column('version_label', sa.String(length=50), nullable=True),
    sa.Column('state', sa.String(length=50), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('is_released', sa.Boolean(), nullable=True),
    sa.Column('released_at', sa.DateTime(), nullable=True),
    sa.Column('released_by_id', sa.Integer(), nullable=True),
    sa.Column('checked_out_by_id', sa.Integer(), nullable=True),
    sa.Column('checked_out_at', sa.DateTime(), nullable=True),
    sa.Column('predecessor_id', sa.String(), nullable=True),
    sa.Column('branch_name', sa.String(length=100), nullable=True),
    sa.Column('branched_from_id', sa.String(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('file_count', sa.Integer(), nullable=True),
    sa.Column('primary_file_id', sa.String(), nullable=True),
    sa.Column('thumbnail_data', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['branched_from_id'], ['meta_item_versions.id'], ),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['predecessor_id'], ['meta_item_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_manufacturing_boms',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('source_item_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('version', sa.String(length=50), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=True),
    sa.Column('bom_type', sa.String(length=20), nullable=True),
    sa.Column('plant_code', sa.String(length=120), nullable=True),
    sa.Column('line_code', sa.String(length=120), nullable=True),
    sa.Column('effective_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('state', sa.String(length=50), nullable=True),
    sa.Column('structure', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('released_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['source_item_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_product_configurations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('product_item_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('selections', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('effective_bom_cache', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('cache_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('state', sa.String(length=50), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('released_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['product_item_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_quality_points',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('title', sa.String(length=400), nullable=True),
    sa.Column('check_type', sa.String(length=30), nullable=False),
    sa.Column('product_id', sa.String(), nullable=True),
    sa.Column('item_type_id', sa.String(), nullable=True),
    sa.Column('routing_id', sa.String(), nullable=True),
    sa.Column('operation_id', sa.String(), nullable=True),
    sa.Column('measure_min', sa.Float(), nullable=True),
    sa.Column('measure_max', sa.Float(), nullable=True),
    sa.Column('measure_unit', sa.String(length=50), nullable=True),
    sa.Column('measure_tolerance', sa.Float(), nullable=True),
    sa.Column('worksheet_template', sa.Text(), nullable=True),
    sa.Column('instructions', sa.Text(), nullable=True),
    sa.Column('trigger_on', sa.String(length=50), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('team_name', sa.String(length=200), nullable=True),
    sa.Column('responsible_user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['product_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_raw_materials',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('material_type', sa.String(length=30), nullable=False),
    sa.Column('grade', sa.String(length=100), nullable=True),
    sa.Column('length', sa.Float(), nullable=True),
    sa.Column('width', sa.Float(), nullable=True),
    sa.Column('thickness', sa.Float(), nullable=True),
    sa.Column('dimension_unit', sa.String(length=20), nullable=False),
    sa.Column('weight_per_unit', sa.Float(), nullable=True),
    sa.Column('weight_unit', sa.String(length=20), nullable=False),
    sa.Column('stock_quantity', sa.Float(), nullable=False),
    sa.Column('cost_per_unit', sa.Float(), nullable=True),
    sa.Column('product_id', sa.String(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['product_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_relationships',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('relationship_type_id', sa.String(), nullable=False),
    sa.Column('source_id', sa.String(), nullable=False),
    sa.Column('related_id', sa.String(), nullable=False),
    sa.Column('properties', sa.JSON(), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=50), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['related_id'], ['meta_items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['relationship_type_id'], ['meta_relationship_types.id'], ),
    sa.ForeignKeyConstraint(['source_id'], ['meta_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    comment='Deprecated: read-only compatibility table; use meta_items (ItemType.is_relationship)'
    )
    op.create_table('meta_signature_manifests',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('item_generation', sa.Integer(), nullable=False),
    sa.Column('required_signatures', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('is_complete', sa.Boolean(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('manifest_hash', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_variant_rules',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('parent_item_type_id', sa.String(), nullable=True),
    sa.Column('parent_item_id', sa.String(), nullable=True),
    sa.Column('condition', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('action_type', sa.String(length=50), nullable=False),
    sa.Column('target_item_id', sa.String(), nullable=True),
    sa.Column('target_relationship_id', sa.String(), nullable=True),
    sa.Column('action_params', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['parent_item_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['parent_item_type_id'], ['meta_item_types.id'], ),
    sa.ForeignKeyConstraint(['target_item_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['target_relationship_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_workflow_processes',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('workflow_map_id', sa.String(), nullable=True),
    sa.Column('item_id', sa.String(), nullable=True),
    sa.Column('state', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['workflow_map_id'], ['meta_workflow_maps.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_baselines',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('baseline_type', sa.String(length=50), nullable=False),
    sa.Column('baseline_number', sa.String(length=60), nullable=True),
    sa.Column('scope', sa.String(length=50), nullable=True),
    sa.Column('root_item_id', sa.String(), nullable=True),
    sa.Column('root_version_id', sa.String(), nullable=True),
    sa.Column('root_config_id', sa.String(), nullable=True),
    sa.Column('eco_id', sa.String(), nullable=True),
    sa.Column('snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('max_levels', sa.Integer(), nullable=True),
    sa.Column('effective_at', sa.DateTime(), nullable=True),
    sa.Column('include_bom', sa.Boolean(), nullable=True),
    sa.Column('include_substitutes', sa.Boolean(), nullable=True),
    sa.Column('include_effectivity', sa.Boolean(), nullable=True),
    sa.Column('include_documents', sa.Boolean(), nullable=True),
    sa.Column('include_relationships', sa.Boolean(), nullable=True),
    sa.Column('line_key', sa.String(length=50), nullable=True),
    sa.Column('item_count', sa.Integer(), nullable=True),
    sa.Column('relationship_count', sa.Integer(), nullable=True),
    sa.Column('state', sa.String(length=50), nullable=True),
    sa.Column('is_validated', sa.Boolean(), nullable=True),
    sa.Column('validation_errors', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('validated_at', sa.DateTime(), nullable=True),
    sa.Column('validated_by_id', sa.Integer(), nullable=True),
    sa.Column('is_locked', sa.Boolean(), nullable=True),
    sa.Column('locked_at', sa.DateTime(), nullable=True),
    sa.Column('released_at', sa.DateTime(), nullable=True),
    sa.Column('released_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['eco_id'], ['meta_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['root_item_id'], ['meta_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['root_version_id'], ['meta_item_versions.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('baseline_number')
    )
    op.create_table('meta_box_contents',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('box_id', sa.String(), nullable=False),
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('lot_serial', sa.String(length=200), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['box_id'], ['meta_box_items.id'], ),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_cut_plans',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('material_id', sa.String(), nullable=True),
    sa.Column('material_quantity', sa.Float(), nullable=False),
    sa.Column('total_parts', sa.Integer(), nullable=False),
    sa.Column('ok_count', sa.Integer(), nullable=False),
    sa.Column('scrap_count', sa.Integer(), nullable=False),
    sa.Column('rework_count', sa.Integer(), nullable=False),
    sa.Column('waste_pct', sa.Float(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['material_id'], ['meta_raw_materials.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_ecos',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('eco_type', sa.String(length=20), nullable=False),
    sa.Column('product_id', sa.String(), nullable=True),
    sa.Column('source_version_id', sa.String(), nullable=True),
    sa.Column('target_version_id', sa.String(), nullable=True),
    sa.Column('stage_id', sa.String(), nullable=True),
    sa.Column('state', sa.String(length=20), nullable=True),
    sa.Column('current_state', sa.String(), nullable=True),
    sa.Column('kanban_state', sa.String(length=20), nullable=True),
    sa.Column('approval_deadline', sa.DateTime(), nullable=True),
    sa.Column('product_version_before', sa.String(length=20), nullable=True),
    sa.Column('product_version_after', sa.String(length=20), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('priority', sa.String(length=10), nullable=True),
    sa.Column('effectivity_date', sa.DateTime(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('company_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['current_state'], ['meta_lifecycle_states.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['meta_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_version_id'], ['meta_item_versions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['stage_id'], ['meta_eco_stages.id'], ),
    sa.ForeignKeyConstraint(['target_version_id'], ['meta_item_versions.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_effectivities',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('item_id', sa.String(), nullable=True, comment='Deprecated, use version_id'),
    sa.Column('version_id', sa.String(), nullable=True),
    sa.Column('effectivity_type', sa.String(length=32), nullable=False, comment='Type: Date, Lot, Serial, Unit'),
    sa.Column('start_date', sa.DateTime(), nullable=True),
    sa.Column('end_date', sa.DateTime(), nullable=True),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True, comment='Extension data for Lot/Serial/Unit effectivity'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['version_id'], ['meta_item_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_item_iterations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('version_id', sa.String(), nullable=False),
    sa.Column('iteration_number', sa.Integer(), nullable=False),
    sa.Column('iteration_label', sa.String(length=50), nullable=True),
    sa.Column('is_latest', sa.Boolean(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('source_type', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('file_count', sa.Integer(), nullable=True),
    sa.Column('primary_file_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['version_id'], ['meta_item_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id', 'iteration_number', name='uq_version_iteration')
    )
    op.create_table('meta_quality_checks',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('point_id', sa.String(), nullable=False),
    sa.Column('product_id', sa.String(), nullable=True),
    sa.Column('routing_id', sa.String(), nullable=True),
    sa.Column('operation_id', sa.String(), nullable=True),
    sa.Column('check_type', sa.String(length=30), nullable=False),
    sa.Column('result', sa.String(length=20), nullable=True),
    sa.Column('measure_value', sa.Float(), nullable=True),
    sa.Column('picture_path', sa.String(), nullable=True),
    sa.Column('worksheet_data', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('source_document_ref', sa.String(length=200), nullable=True),
    sa.Column('lot_serial', sa.String(length=200), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('checked_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['point_id'], ['meta_quality_points.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_routings',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('mbom_id', sa.String(), nullable=True),
    sa.Column('item_id', sa.String(), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('routing_code', sa.String(length=120), nullable=True),
    sa.Column('version', sa.String(length=50), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('effective_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_primary', sa.Boolean(), nullable=True),
    sa.Column('plant_code', sa.String(length=120), nullable=True),
    sa.Column('line_code', sa.String(length=120), nullable=True),
    sa.Column('state', sa.String(length=50), nullable=True),
    sa.Column('total_setup_time', sa.Float(), nullable=True),
    sa.Column('total_run_time', sa.Float(), nullable=True),
    sa.Column('total_labor_time', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['mbom_id'], ['meta_manufacturing_boms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('routing_code')
    )
    op.create_table('meta_signature_audit_logs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('signature_id', sa.String(), nullable=True),
    sa.Column('item_id', sa.String(), nullable=True),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('actor_username', sa.String(), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('success', sa.Boolean(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('client_ip', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['signature_id'], ['meta_electronic_signatures.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_similarity_records',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('source_file_id', sa.String(), nullable=False),
    sa.Column('target_file_id', sa.String(), nullable=False),
    sa.Column('pair_key', sa.String(length=80), nullable=False),
    sa.Column('similarity_score', sa.Float(), nullable=False),
    sa.Column('similarity_type', sa.String(), nullable=True),
    sa.Column('detection_method', sa.String(), nullable=True),
    sa.Column('detection_params', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('status', sa.String(), nullable=True),
    sa.Column('reviewed_by_id', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_comment', sa.Text(), nullable=True),
    sa.Column('relationship_item_id', sa.String(), nullable=True),
    sa.Column('batch_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['batch_id'], ['meta_dedup_batches.id'], ),
    sa.ForeignKeyConstraint(['relationship_item_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['source_file_id'], ['meta_files.id'], ),
    sa.ForeignKeyConstraint(['target_file_id'], ['meta_files.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pair_key', name='uq_meta_similarity_records_pair_key')
    )
    op.create_table('meta_version_files',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('version_id', sa.String(), nullable=False),
    sa.Column('file_id', sa.String(), nullable=False),
    sa.Column('file_role', sa.String(), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=True),
    sa.Column('snapshot_path', sa.String(), nullable=True),
    sa.Column('is_primary', sa.Boolean(), nullable=True),
    sa.Column('checked_out_by_id', sa.Integer(), nullable=True),
    sa.Column('checked_out_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['version_id'], ['meta_item_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_version_history',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('version_id', sa.String(), nullable=True),
    sa.Column('action', sa.String(length=50), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('changes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['version_id'], ['meta_item_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_workflow_activity_instances',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('process_id', sa.String(), nullable=True),
    sa.Column('activity_id', sa.String(), nullable=True),
    sa.Column('state', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['activity_id'], ['meta_workflow_activities.id'], ),
    sa.ForeignKeyConstraint(['process_id'], ['meta_workflow_processes.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_baseline_comparisons',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('baseline_a_id', sa.String(), nullable=False),
    sa.Column('baseline_b_id', sa.String(), nullable=False),
    sa.Column('added_count', sa.Integer(), nullable=True),
    sa.Column('removed_count', sa.Integer(), nullable=True),
    sa.Column('changed_count', sa.Integer(), nullable=True),
    sa.Column('unchanged_count', sa.Integer(), nullable=True),
    sa.Column('differences', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('compared_at', sa.DateTime(), nullable=True),
    sa.Column('compared_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['baseline_a_id'], ['meta_baselines.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['baseline_b_id'], ['meta_baselines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_baseline_members',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('baseline_id', sa.String(), nullable=False),
    sa.Column('item_id', sa.String(), nullable=True),
    sa.Column('document_id', sa.String(), nullable=True),
    sa.Column('relationship_id', sa.String(), nullable=True),
    sa.Column('item_number', sa.String(), nullable=True),
    sa.Column('item_revision', sa.String(), nullable=True),
    sa.Column('item_generation', sa.Integer(), nullable=True),
    sa.Column('item_type', sa.String(), nullable=True),
    sa.Column('level', sa.Integer(), nullable=True),
    sa.Column('path', sa.String(), nullable=True),
    sa.Column('quantity', sa.String(), nullable=True),
    sa.Column('member_type', sa.String(), nullable=True),
    sa.Column('item_state', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['baseline_id'], ['meta_baselines.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['meta_files.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_cut_results',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('plan_id', sa.String(), nullable=False),
    sa.Column('part_id', sa.String(), nullable=True),
    sa.Column('length', sa.Float(), nullable=True),
    sa.Column('width', sa.Float(), nullable=True),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('scrap_weight', sa.Float(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['part_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['plan_id'], ['meta_cut_plans.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_eco_approvals',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('eco_id', sa.String(), nullable=False),
    sa.Column('stage_id', sa.String(), nullable=False),
    sa.Column('approval_type', sa.String(length=20), nullable=True),
    sa.Column('required_role', sa.String(length=100), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('approved_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['eco_id'], ['meta_ecos.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['stage_id'], ['meta_eco_stages.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_eco_bom_changes',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('eco_id', sa.String(), nullable=False),
    sa.Column('change_type', sa.String(length=20), nullable=False),
    sa.Column('relationship_item_id', sa.String(), nullable=True),
    sa.Column('parent_item_id', sa.String(), nullable=True),
    sa.Column('child_item_id', sa.String(), nullable=True),
    sa.Column('old_qty', sa.Float(), nullable=True),
    sa.Column('old_uom', sa.String(length=50), nullable=True),
    sa.Column('old_sequence', sa.Integer(), nullable=True),
    sa.Column('old_properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('new_qty', sa.Float(), nullable=True),
    sa.Column('new_uom', sa.String(length=50), nullable=True),
    sa.Column('new_sequence', sa.Integer(), nullable=True),
    sa.Column('new_properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('conflict', sa.Boolean(), nullable=True),
    sa.Column('conflict_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['child_item_id'], ['meta_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['eco_id'], ['meta_ecos.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_item_id'], ['meta_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['relationship_item_id'], ['meta_items.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_eco_routing_changes',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('eco_id', sa.String(), nullable=False),
    sa.Column('routing_id', sa.String(), nullable=True),
    sa.Column('operation_id', sa.String(), nullable=True),
    sa.Column('change_type', sa.String(length=10), nullable=False),
    sa.Column('old_snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('new_snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('conflict', sa.Boolean(), nullable=True),
    sa.Column('conflict_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['eco_id'], ['meta_ecos.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_operations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('routing_id', sa.String(), nullable=False),
    sa.Column('operation_number', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('operation_type', sa.String(length=50), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=True),
    sa.Column('workcenter_id', sa.String(), nullable=True),
    sa.Column('workcenter_code', sa.String(length=120), nullable=True),
    sa.Column('setup_time', sa.Float(), nullable=True),
    sa.Column('run_time', sa.Float(), nullable=True),
    sa.Column('queue_time', sa.Float(), nullable=True),
    sa.Column('move_time', sa.Float(), nullable=True),
    sa.Column('wait_time', sa.Float(), nullable=True),
    sa.Column('labor_setup_time', sa.Float(), nullable=True),
    sa.Column('labor_run_time', sa.Float(), nullable=True),
    sa.Column('crew_size', sa.Integer(), nullable=True),
    sa.Column('machines_required', sa.Integer(), nullable=True),
    sa.Column('overlap_quantity', sa.Integer(), nullable=True),
    sa.Column('transfer_batch', sa.Integer(), nullable=True),
    sa.Column('is_subcontracted', sa.Boolean(), nullable=True),
    sa.Column('subcontractor_id', sa.String(), nullable=True),
    sa.Column('inspection_required', sa.Boolean(), nullable=True),
    sa.Column('inspection_plan_id', sa.String(), nullable=True),
    sa.Column('tooling_requirements', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('work_instructions', sa.Text(), nullable=True),
    sa.Column('document_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('labor_cost_rate', sa.Float(), nullable=True),
    sa.Column('overhead_rate', sa.Float(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['routing_id'], ['meta_routings.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_quality_alerts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('check_id', sa.String(), nullable=True),
    sa.Column('product_id', sa.String(), nullable=True),
    sa.Column('state', sa.String(length=30), nullable=True),
    sa.Column('priority', sa.String(length=20), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('root_cause', sa.Text(), nullable=True),
    sa.Column('corrective_action', sa.Text(), nullable=True),
    sa.Column('team_name', sa.String(length=200), nullable=True),
    sa.Column('assigned_user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['check_id'], ['meta_quality_checks.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['meta_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_workflow_tasks',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('activity_instance_id', sa.String(), nullable=True),
    sa.Column('assignee_type', sa.String(), nullable=True),
    sa.Column('assigned_to_user_id', sa.Integer(), nullable=True),
    sa.Column('assigned_to_role_id', sa.Integer(), nullable=True),
    sa.Column('dynamic_identity', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=True),
    sa.Column('outcome', sa.String(), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['activity_instance_id'], ['meta_workflow_activity_instances.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_mbom_lines',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('mbom_id', sa.String(), nullable=False),
    sa.Column('parent_line_id', sa.String(), nullable=True),
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=True),
    sa.Column('level', sa.Integer(), nullable=True),
    sa.Column('quantity', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('unit', sa.String(length=50), nullable=True),
    sa.Column('ebom_relationship_id', sa.String(), nullable=True),
    sa.Column('make_buy', sa.String(length=50), nullable=True),
    sa.Column('supply_type', sa.String(length=120), nullable=True),
    sa.Column('operation_id', sa.String(), nullable=True),
    sa.Column('backflush', sa.Boolean(), nullable=True),
    sa.Column('scrap_rate', sa.Float(), nullable=True),
    sa.Column('fixed_quantity', sa.Boolean(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['ebom_relationship_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['mbom_id'], ['meta_manufacturing_boms.id'], ),
    sa.ForeignKeyConstraint(['operation_id'], ['meta_operations.id'], ),
    sa.ForeignKeyConstraint(['parent_line_id'], ['meta_mbom_lines.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_subcontract_orders',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('item_id', sa.String(), nullable=True),
    sa.Column('routing_id', sa.String(), nullable=True),
    sa.Column('source_operation_id', sa.String(), nullable=True),
    sa.Column('vendor_id', sa.String(), nullable=True),
    sa.Column('vendor_name', sa.String(length=200), nullable=True),
    sa.Column('state', sa.String(length=40), nullable=False),
    sa.Column('requested_qty', sa.Float(), nullable=False),
    sa.Column('issued_qty', sa.Float(), nullable=False),
    sa.Column('received_qty', sa.Float(), nullable=False),
    sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['meta_items.id'], ),
    sa.ForeignKeyConstraint(['source_operation_id'], ['meta_operations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('meta_subcontract_order_events',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('order_id', sa.String(), nullable=False),
    sa.Column('event_type', sa.String(length=40), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('reference', sa.String(length=200), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('properties', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['order_id'], ['meta_subcontract_orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cad_change_logs_file_id'), 'cad_change_logs', ['file_id'], unique=False)
    op.create_index(op.f('ix_cad_change_logs_org_id'), 'cad_change_logs', ['org_id'], unique=False)
    op.create_index(op.f('ix_cad_change_logs_tenant_id'), 'cad_change_logs', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_cad_change_logs_user_id'), 'cad_change_logs', ['user_id'], unique=False)
    op.create_index(op.f('ix_meta_3d_overlays_document_item_id'), 'meta_3d_overlays', ['document_item_id'], unique=True)
    op.create_index(op.f('ix_meta_3d_overlays_status'), 'meta_3d_overlays', ['status'], unique=False)
    op.create_index(op.f('ix_meta_approval_categories_parent_id'), 'meta_approval_categories', ['parent_id'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_batch_code'), 'meta_breakage_incidents', ['batch_code'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_bom_id'), 'meta_breakage_incidents', ['bom_id'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_bom_line_item_id'), 'meta_breakage_incidents', ['bom_line_item_id'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_customer_name'), 'meta_breakage_incidents', ['customer_name'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_eco_id'), 'meta_breakage_incidents', ['eco_id'], unique=True)
    op.create_index(op.f('ix_meta_breakage_incidents_incident_code'), 'meta_breakage_incidents', ['incident_code'], unique=True)
    op.create_index(op.f('ix_meta_breakage_incidents_mbom_id'), 'meta_breakage_incidents', ['mbom_id'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_product_item_id'), 'meta_breakage_incidents', ['product_item_id'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_production_order_id'), 'meta_breakage_incidents', ['production_order_id'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_routing_id'), 'meta_breakage_incidents', ['routing_id'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_severity'), 'meta_breakage_incidents', ['severity'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_status'), 'meta_breakage_incidents', ['status'], unique=False)
    op.create_index(op.f('ix_meta_breakage_incidents_version_id'), 'meta_breakage_incidents', ['version_id'], unique=False)
    op.create_index(op.f('ix_meta_consumption_plans_item_id'), 'meta_consumption_plans', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_consumption_plans_name'), 'meta_consumption_plans', ['name'], unique=False)
    op.create_index(op.f('ix_meta_consumption_plans_state'), 'meta_consumption_plans', ['state'], unique=False)
    op.create_index(op.f('ix_meta_consumption_records_plan_id'), 'meta_consumption_records', ['plan_id'], unique=False)
    op.create_index(op.f('ix_meta_consumption_records_recorded_at'), 'meta_consumption_records', ['recorded_at'], unique=False)
    op.create_index(op.f('ix_meta_consumption_records_source_id'), 'meta_consumption_records', ['source_id'], unique=False)
    op.create_index(op.f('ix_meta_conversion_jobs_dedupe_key'), 'meta_conversion_jobs', ['dedupe_key'], unique=False)
    op.create_index(op.f('ix_meta_conversion_jobs_status'), 'meta_conversion_jobs', ['status'], unique=False)
    op.create_index(op.f('ix_meta_eco_activity_gate_events_activity_id'), 'meta_eco_activity_gate_events', ['activity_id'], unique=False)
    op.create_index(op.f('ix_meta_eco_activity_gate_events_eco_id'), 'meta_eco_activity_gate_events', ['eco_id'], unique=False)
    op.create_index(op.f('ix_meta_eco_activity_gates_eco_id'), 'meta_eco_activity_gates', ['eco_id'], unique=False)
    op.create_index(op.f('ix_meta_eco_activity_gates_status'), 'meta_eco_activity_gates', ['status'], unique=False)
    op.create_index(op.f('ix_meta_maintenance_categories_parent_id'), 'meta_maintenance_categories', ['parent_id'], unique=False)
    op.create_index(op.f('ix_meta_numbering_sequences_item_type_id'), 'meta_numbering_sequences', ['item_type_id'], unique=False)
    op.create_index(op.f('ix_meta_numbering_sequences_org_id'), 'meta_numbering_sequences', ['org_id'], unique=False)
    op.create_index(op.f('ix_meta_numbering_sequences_tenant_id'), 'meta_numbering_sequences', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_meta_plugin_configs_org_id'), 'meta_plugin_configs', ['org_id'], unique=False)
    op.create_index(op.f('ix_meta_plugin_configs_plugin_id'), 'meta_plugin_configs', ['plugin_id'], unique=False)
    op.create_index(op.f('ix_meta_plugin_configs_tenant_id'), 'meta_plugin_configs', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_meta_remote_sites_name'), 'meta_remote_sites', ['name'], unique=True)
    op.create_index(op.f('ix_meta_store_listings_name'), 'meta_store_listings', ['name'], unique=False)
    op.create_index(op.f('ix_meta_subcontract_approval_role_mappings_role_code'), 'meta_subcontract_approval_role_mappings', ['role_code'], unique=False)
    op.create_index(op.f('ix_meta_subcontract_approval_role_mappings_scope_type'), 'meta_subcontract_approval_role_mappings', ['scope_type'], unique=False)
    op.create_index(op.f('ix_meta_subcontract_approval_role_mappings_scope_value'), 'meta_subcontract_approval_role_mappings', ['scope_value'], unique=False)
    op.create_index(op.f('ix_meta_translations_lang'), 'meta_translations', ['lang'], unique=False)
    op.create_index(op.f('ix_meta_translations_record_id'), 'meta_translations', ['record_id'], unique=False)
    op.create_index(op.f('ix_meta_translations_record_type'), 'meta_translations', ['record_type'], unique=False)
    op.create_index(op.f('ix_meta_workflow_custom_action_rules_from_state'), 'meta_workflow_custom_action_rules', ['from_state'], unique=False)
    op.create_index(op.f('ix_meta_workflow_custom_action_rules_to_state'), 'meta_workflow_custom_action_rules', ['to_state'], unique=False)
    op.create_index(op.f('ix_meta_workflow_custom_action_rules_workflow_map_id'), 'meta_workflow_custom_action_rules', ['workflow_map_id'], unique=False)
    op.create_index(op.f('ix_meta_workflow_custom_action_runs_object_id'), 'meta_workflow_custom_action_runs', ['object_id'], unique=False)
    op.create_index(op.f('ix_meta_workflow_custom_action_runs_rule_id'), 'meta_workflow_custom_action_runs', ['rule_id'], unique=False)
    op.create_index(op.f('ix_meta_workorder_document_links_document_item_id'), 'meta_workorder_document_links', ['document_item_id'], unique=False)
    op.create_index(op.f('ix_meta_workorder_document_links_document_version_id'), 'meta_workorder_document_links', ['document_version_id'], unique=False)
    op.create_index(op.f('ix_meta_workorder_document_links_operation_id'), 'meta_workorder_document_links', ['operation_id'], unique=False)
    op.create_index(op.f('ix_meta_workorder_document_links_routing_id'), 'meta_workorder_document_links', ['routing_id'], unique=False)
    op.create_index(op.f('ix_meta_approval_requests_category_id'), 'meta_approval_requests', ['category_id'], unique=False)
    op.create_index(op.f('ix_meta_approval_requests_entity_id'), 'meta_approval_requests', ['entity_id'], unique=False)
    op.create_index(op.f('ix_meta_approval_requests_entity_type'), 'meta_approval_requests', ['entity_type'], unique=False)
    op.create_index(op.f('ix_meta_files_checksum'), 'meta_files', ['checksum'], unique=False)
    op.create_index(op.f('ix_meta_files_file_type'), 'meta_files', ['file_type'], unique=False)
    op.create_index(op.f('ix_meta_maintenance_equipment_category_id'), 'meta_maintenance_equipment', ['category_id'], unique=False)
    op.create_index(op.f('ix_meta_report_executions_report_id'), 'meta_report_executions', ['report_id'], unique=False)
    op.create_index(op.f('ix_meta_sync_jobs_site_id'), 'meta_sync_jobs', ['site_id'], unique=False)
    op.create_index(op.f('ix_meta_approval_request_events_request_id'), 'meta_approval_request_events', ['request_id'], unique=False)
    op.create_index(op.f('ix_meta_config_option_sets_item_type_id'), 'meta_config_option_sets', ['item_type_id'], unique=False)
    op.create_index(op.f('ix_meta_config_option_sets_name'), 'meta_config_option_sets', ['name'], unique=True)
    op.create_index(op.f('ix_meta_items_config_id'), 'meta_items', ['config_id'], unique=False)
    op.create_index(op.f('ix_meta_items_item_type_id'), 'meta_items', ['item_type_id'], unique=False)
    op.create_index(op.f('ix_meta_items_related_id'), 'meta_items', ['related_id'], unique=False)
    op.create_index(op.f('ix_meta_items_source_id'), 'meta_items', ['source_id'], unique=False)
    op.create_index(op.f('ix_meta_maintenance_requests_equipment_id'), 'meta_maintenance_requests', ['equipment_id'], unique=False)
    op.create_index(op.f('ix_meta_revision_schemes_is_default'), 'meta_revision_schemes', ['is_default'], unique=False)
    op.create_index(op.f('ix_meta_revision_schemes_item_type_id'), 'meta_revision_schemes', ['item_type_id'], unique=False)
    op.create_index('ix_revision_scheme_itemtype', 'meta_revision_schemes', ['item_type_id', 'is_default'], unique=False)
    op.create_index(op.f('ix_meta_sync_records_document_id'), 'meta_sync_records', ['document_id'], unique=False)
    op.create_index(op.f('ix_meta_sync_records_job_id'), 'meta_sync_records', ['job_id'], unique=False)
    op.create_index(op.f('ix_meta_box_items_product_id'), 'meta_box_items', ['product_id'], unique=False)
    op.create_index(op.f('ix_meta_config_options_option_set_id'), 'meta_config_options', ['option_set_id'], unique=False)
    op.create_index(op.f('ix_meta_dedup_batches_status'), 'meta_dedup_batches', ['status'], unique=False)
    op.create_index(op.f('ix_meta_electronic_signatures_item_id'), 'meta_electronic_signatures', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_geometric_indices_item_id'), 'meta_geometric_indices', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_item_files_file_id'), 'meta_item_files', ['file_id'], unique=False)
    op.create_index(op.f('ix_meta_item_files_item_id'), 'meta_item_files', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_item_versions_is_current'), 'meta_item_versions', ['is_current'], unique=False)
    op.create_index(op.f('ix_meta_item_versions_is_released'), 'meta_item_versions', ['is_released'], unique=False)
    op.create_index(op.f('ix_meta_item_versions_item_id'), 'meta_item_versions', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_manufacturing_boms_source_item_id'), 'meta_manufacturing_boms', ['source_item_id'], unique=False)
    op.create_index(op.f('ix_meta_product_configurations_product_item_id'), 'meta_product_configurations', ['product_item_id'], unique=False)
    op.create_index(op.f('ix_meta_quality_points_item_type_id'), 'meta_quality_points', ['item_type_id'], unique=False)
    op.create_index(op.f('ix_meta_quality_points_operation_id'), 'meta_quality_points', ['operation_id'], unique=False)
    op.create_index(op.f('ix_meta_quality_points_product_id'), 'meta_quality_points', ['product_id'], unique=False)
    op.create_index(op.f('ix_meta_quality_points_routing_id'), 'meta_quality_points', ['routing_id'], unique=False)
    op.create_index(op.f('ix_meta_raw_materials_product_id'), 'meta_raw_materials', ['product_id'], unique=False)
    op.create_index(op.f('ix_meta_relationships_related_id'), 'meta_relationships', ['related_id'], unique=False)
    op.create_index(op.f('ix_meta_relationships_source_id'), 'meta_relationships', ['source_id'], unique=False)
    op.create_index(op.f('ix_meta_signature_manifests_item_id'), 'meta_signature_manifests', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_variant_rules_parent_item_id'), 'meta_variant_rules', ['parent_item_id'], unique=False)
    op.create_index(op.f('ix_meta_variant_rules_parent_item_type_id'), 'meta_variant_rules', ['parent_item_type_id'], unique=False)
    op.create_index(op.f('ix_meta_baselines_root_config_id'), 'meta_baselines', ['root_config_id'], unique=False)
    op.create_index(op.f('ix_meta_baselines_root_item_id'), 'meta_baselines', ['root_item_id'], unique=False)
    op.create_index(op.f('ix_meta_baselines_root_version_id'), 'meta_baselines', ['root_version_id'], unique=False)
    op.create_index(op.f('ix_meta_box_contents_box_id'), 'meta_box_contents', ['box_id'], unique=False)
    op.create_index(op.f('ix_meta_box_contents_item_id'), 'meta_box_contents', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_cut_plans_material_id'), 'meta_cut_plans', ['material_id'], unique=False)
    op.create_index(op.f('ix_meta_ecos_product_id'), 'meta_ecos', ['product_id'], unique=False)
    op.create_index(op.f('ix_meta_ecos_stage_id'), 'meta_ecos', ['stage_id'], unique=False)
    op.create_index(op.f('ix_meta_ecos_state'), 'meta_ecos', ['state'], unique=False)
    op.create_index(op.f('ix_meta_effectivities_effectivity_type'), 'meta_effectivities', ['effectivity_type'], unique=False)
    op.create_index(op.f('ix_meta_effectivities_item_id'), 'meta_effectivities', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_effectivities_version_id'), 'meta_effectivities', ['version_id'], unique=False)
    op.create_index('ix_iteration_version_latest', 'meta_item_iterations', ['version_id', 'is_latest'], unique=False)
    op.create_index(op.f('ix_meta_item_iterations_is_latest'), 'meta_item_iterations', ['is_latest'], unique=False)
    op.create_index(op.f('ix_meta_item_iterations_version_id'), 'meta_item_iterations', ['version_id'], unique=False)
    op.create_index(op.f('ix_meta_quality_checks_operation_id'), 'meta_quality_checks', ['operation_id'], unique=False)
    op.create_index(op.f('ix_meta_quality_checks_point_id'), 'meta_quality_checks', ['point_id'], unique=False)
    op.create_index(op.f('ix_meta_quality_checks_product_id'), 'meta_quality_checks', ['product_id'], unique=False)
    op.create_index(op.f('ix_meta_quality_checks_routing_id'), 'meta_quality_checks', ['routing_id'], unique=False)
    op.create_index(op.f('ix_meta_routings_item_id'), 'meta_routings', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_routings_mbom_id'), 'meta_routings', ['mbom_id'], unique=False)
    op.create_index(op.f('ix_meta_similarity_records_batch_id'), 'meta_similarity_records', ['batch_id'], unique=False)
    op.create_index(op.f('ix_meta_similarity_records_pair_key'), 'meta_similarity_records', ['pair_key'], unique=False)
    op.create_index(op.f('ix_meta_similarity_records_source_file_id'), 'meta_similarity_records', ['source_file_id'], unique=False)
    op.create_index(op.f('ix_meta_similarity_records_status'), 'meta_similarity_records', ['status'], unique=False)
    op.create_index(op.f('ix_meta_similarity_records_target_file_id'), 'meta_similarity_records', ['target_file_id'], unique=False)
    op.create_index(op.f('ix_meta_version_files_file_id'), 'meta_version_files', ['file_id'], unique=False)
    op.create_index(op.f('ix_meta_version_files_file_role'), 'meta_version_files', ['file_role'], unique=False)
    op.create_index(op.f('ix_meta_version_files_version_id'), 'meta_version_files', ['version_id'], unique=False)
    op.create_index('uq_version_file_role', 'meta_version_files', ['version_id', 'file_id', 'file_role'], unique=True)
    op.create_index(op.f('ix_meta_version_history_version_id'), 'meta_version_history', ['version_id'], unique=False)
    op.create_index(op.f('ix_meta_baseline_members_baseline_id'), 'meta_baseline_members', ['baseline_id'], unique=False)
    op.create_index(op.f('ix_meta_baseline_members_document_id'), 'meta_baseline_members', ['document_id'], unique=False)
    op.create_index(op.f('ix_meta_baseline_members_item_id'), 'meta_baseline_members', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_baseline_members_relationship_id'), 'meta_baseline_members', ['relationship_id'], unique=False)
    op.create_index(op.f('ix_meta_cut_results_part_id'), 'meta_cut_results', ['part_id'], unique=False)
    op.create_index(op.f('ix_meta_cut_results_plan_id'), 'meta_cut_results', ['plan_id'], unique=False)
    op.create_index(op.f('ix_meta_eco_approvals_eco_id'), 'meta_eco_approvals', ['eco_id'], unique=False)
    op.create_index(op.f('ix_meta_eco_approvals_status'), 'meta_eco_approvals', ['status'], unique=False)
    op.create_index('ix_eco_bom_change_child', 'meta_eco_bom_changes', ['child_item_id'], unique=False)
    op.create_index('ix_eco_bom_change_parent', 'meta_eco_bom_changes', ['parent_item_id'], unique=False)
    op.create_index(op.f('ix_meta_eco_bom_changes_change_type'), 'meta_eco_bom_changes', ['change_type'], unique=False)
    op.create_index(op.f('ix_meta_eco_bom_changes_eco_id'), 'meta_eco_bom_changes', ['eco_id'], unique=False)
    op.create_index(op.f('ix_meta_eco_routing_changes_change_type'), 'meta_eco_routing_changes', ['change_type'], unique=False)
    op.create_index(op.f('ix_meta_eco_routing_changes_eco_id'), 'meta_eco_routing_changes', ['eco_id'], unique=False)
    op.create_index(op.f('ix_meta_operations_routing_id'), 'meta_operations', ['routing_id'], unique=False)
    op.create_index(op.f('ix_meta_quality_alerts_check_id'), 'meta_quality_alerts', ['check_id'], unique=False)
    op.create_index(op.f('ix_meta_quality_alerts_product_id'), 'meta_quality_alerts', ['product_id'], unique=False)
    op.create_index(op.f('ix_meta_mbom_lines_item_id'), 'meta_mbom_lines', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_mbom_lines_mbom_id'), 'meta_mbom_lines', ['mbom_id'], unique=False)
    op.create_index(op.f('ix_meta_subcontract_orders_item_id'), 'meta_subcontract_orders', ['item_id'], unique=False)
    op.create_index(op.f('ix_meta_subcontract_orders_routing_id'), 'meta_subcontract_orders', ['routing_id'], unique=False)
    op.create_index(op.f('ix_meta_subcontract_orders_source_operation_id'), 'meta_subcontract_orders', ['source_operation_id'], unique=False)
    op.create_index(op.f('ix_meta_subcontract_orders_vendor_id'), 'meta_subcontract_orders', ['vendor_id'], unique=False)
    op.create_index(op.f('ix_meta_subcontract_order_events_order_id'), 'meta_subcontract_order_events', ['order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_meta_subcontract_order_events_order_id'), table_name='meta_subcontract_order_events')
    op.drop_index(op.f('ix_meta_subcontract_orders_vendor_id'), table_name='meta_subcontract_orders')
    op.drop_index(op.f('ix_meta_subcontract_orders_source_operation_id'), table_name='meta_subcontract_orders')
    op.drop_index(op.f('ix_meta_subcontract_orders_routing_id'), table_name='meta_subcontract_orders')
    op.drop_index(op.f('ix_meta_subcontract_orders_item_id'), table_name='meta_subcontract_orders')
    op.drop_index(op.f('ix_meta_mbom_lines_mbom_id'), table_name='meta_mbom_lines')
    op.drop_index(op.f('ix_meta_mbom_lines_item_id'), table_name='meta_mbom_lines')
    op.drop_index(op.f('ix_meta_quality_alerts_product_id'), table_name='meta_quality_alerts')
    op.drop_index(op.f('ix_meta_quality_alerts_check_id'), table_name='meta_quality_alerts')
    op.drop_index(op.f('ix_meta_operations_routing_id'), table_name='meta_operations')
    op.drop_index(op.f('ix_meta_eco_routing_changes_eco_id'), table_name='meta_eco_routing_changes')
    op.drop_index(op.f('ix_meta_eco_routing_changes_change_type'), table_name='meta_eco_routing_changes')
    op.drop_index(op.f('ix_meta_eco_bom_changes_eco_id'), table_name='meta_eco_bom_changes')
    op.drop_index(op.f('ix_meta_eco_bom_changes_change_type'), table_name='meta_eco_bom_changes')
    op.drop_index('ix_eco_bom_change_parent', table_name='meta_eco_bom_changes')
    op.drop_index('ix_eco_bom_change_child', table_name='meta_eco_bom_changes')
    op.drop_index(op.f('ix_meta_eco_approvals_status'), table_name='meta_eco_approvals')
    op.drop_index(op.f('ix_meta_eco_approvals_eco_id'), table_name='meta_eco_approvals')
    op.drop_index(op.f('ix_meta_cut_results_plan_id'), table_name='meta_cut_results')
    op.drop_index(op.f('ix_meta_cut_results_part_id'), table_name='meta_cut_results')
    op.drop_index(op.f('ix_meta_baseline_members_relationship_id'), table_name='meta_baseline_members')
    op.drop_index(op.f('ix_meta_baseline_members_item_id'), table_name='meta_baseline_members')
    op.drop_index(op.f('ix_meta_baseline_members_document_id'), table_name='meta_baseline_members')
    op.drop_index(op.f('ix_meta_baseline_members_baseline_id'), table_name='meta_baseline_members')
    op.drop_index(op.f('ix_meta_version_history_version_id'), table_name='meta_version_history')
    op.drop_index('uq_version_file_role', table_name='meta_version_files')
    op.drop_index(op.f('ix_meta_version_files_version_id'), table_name='meta_version_files')
    op.drop_index(op.f('ix_meta_version_files_file_role'), table_name='meta_version_files')
    op.drop_index(op.f('ix_meta_version_files_file_id'), table_name='meta_version_files')
    op.drop_index(op.f('ix_meta_similarity_records_target_file_id'), table_name='meta_similarity_records')
    op.drop_index(op.f('ix_meta_similarity_records_status'), table_name='meta_similarity_records')
    op.drop_index(op.f('ix_meta_similarity_records_source_file_id'), table_name='meta_similarity_records')
    op.drop_index(op.f('ix_meta_similarity_records_pair_key'), table_name='meta_similarity_records')
    op.drop_index(op.f('ix_meta_similarity_records_batch_id'), table_name='meta_similarity_records')
    op.drop_index(op.f('ix_meta_routings_mbom_id'), table_name='meta_routings')
    op.drop_index(op.f('ix_meta_routings_item_id'), table_name='meta_routings')
    op.drop_index(op.f('ix_meta_quality_checks_routing_id'), table_name='meta_quality_checks')
    op.drop_index(op.f('ix_meta_quality_checks_product_id'), table_name='meta_quality_checks')
    op.drop_index(op.f('ix_meta_quality_checks_point_id'), table_name='meta_quality_checks')
    op.drop_index(op.f('ix_meta_quality_checks_operation_id'), table_name='meta_quality_checks')
    op.drop_index(op.f('ix_meta_item_iterations_version_id'), table_name='meta_item_iterations')
    op.drop_index(op.f('ix_meta_item_iterations_is_latest'), table_name='meta_item_iterations')
    op.drop_index('ix_iteration_version_latest', table_name='meta_item_iterations')
    op.drop_index(op.f('ix_meta_effectivities_version_id'), table_name='meta_effectivities')
    op.drop_index(op.f('ix_meta_effectivities_item_id'), table_name='meta_effectivities')
    op.drop_index(op.f('ix_meta_effectivities_effectivity_type'), table_name='meta_effectivities')
    op.drop_index(op.f('ix_meta_ecos_state'), table_name='meta_ecos')
    op.drop_index(op.f('ix_meta_ecos_stage_id'), table_name='meta_ecos')
    op.drop_index(op.f('ix_meta_ecos_product_id'), table_name='meta_ecos')
    op.drop_index(op.f('ix_meta_cut_plans_material_id'), table_name='meta_cut_plans')
    op.drop_index(op.f('ix_meta_box_contents_item_id'), table_name='meta_box_contents')
    op.drop_index(op.f('ix_meta_box_contents_box_id'), table_name='meta_box_contents')
    op.drop_index(op.f('ix_meta_baselines_root_version_id'), table_name='meta_baselines')
    op.drop_index(op.f('ix_meta_baselines_root_item_id'), table_name='meta_baselines')
    op.drop_index(op.f('ix_meta_baselines_root_config_id'), table_name='meta_baselines')
    op.drop_index(op.f('ix_meta_variant_rules_parent_item_type_id'), table_name='meta_variant_rules')
    op.drop_index(op.f('ix_meta_variant_rules_parent_item_id'), table_name='meta_variant_rules')
    op.drop_index(op.f('ix_meta_signature_manifests_item_id'), table_name='meta_signature_manifests')
    op.drop_index(op.f('ix_meta_relationships_source_id'), table_name='meta_relationships')
    op.drop_index(op.f('ix_meta_relationships_related_id'), table_name='meta_relationships')
    op.drop_index(op.f('ix_meta_raw_materials_product_id'), table_name='meta_raw_materials')
    op.drop_index(op.f('ix_meta_quality_points_routing_id'), table_name='meta_quality_points')
    op.drop_index(op.f('ix_meta_quality_points_product_id'), table_name='meta_quality_points')
    op.drop_index(op.f('ix_meta_quality_points_operation_id'), table_name='meta_quality_points')
    op.drop_index(op.f('ix_meta_quality_points_item_type_id'), table_name='meta_quality_points')
    op.drop_index(op.f('ix_meta_product_configurations_product_item_id'), table_name='meta_product_configurations')
    op.drop_index(op.f('ix_meta_manufacturing_boms_source_item_id'), table_name='meta_manufacturing_boms')
    op.drop_index(op.f('ix_meta_item_versions_item_id'), table_name='meta_item_versions')
    op.drop_index(op.f('ix_meta_item_versions_is_released'), table_name='meta_item_versions')
    op.drop_index(op.f('ix_meta_item_versions_is_current'), table_name='meta_item_versions')
    op.drop_index(op.f('ix_meta_item_files_item_id'), table_name='meta_item_files')
    op.drop_index(op.f('ix_meta_item_files_file_id'), table_name='meta_item_files')
    op.drop_index(op.f('ix_meta_geometric_indices_item_id'), table_name='meta_geometric_indices')
    op.drop_index(op.f('ix_meta_electronic_signatures_item_id'), table_name='meta_electronic_signatures')
    op.drop_index(op.f('ix_meta_dedup_batches_status'), table_name='meta_dedup_batches')
    op.drop_index(op.f('ix_meta_config_options_option_set_id'), table_name='meta_config_options')
    op.drop_index(op.f('ix_meta_box_items_product_id'), table_name='meta_box_items')
    op.drop_index(op.f('ix_meta_sync_records_job_id'), table_name='meta_sync_records')
    op.drop_index(op.f('ix_meta_sync_records_document_id'), table_name='meta_sync_records')
    op.drop_index('ix_revision_scheme_itemtype', table_name='meta_revision_schemes')
    op.drop_index(op.f('ix_meta_revision_schemes_item_type_id'), table_name='meta_revision_schemes')
    op.drop_index(op.f('ix_meta_revision_schemes_is_default'), table_name='meta_revision_schemes')
    op.drop_index(op.f('ix_meta_maintenance_requests_equipment_id'), table_name='meta_maintenance_requests')
    op.drop_index(op.f('ix_meta_items_source_id'), table_name='meta_items')
    op.drop_index(op.f('ix_meta_items_related_id'), table_name='meta_items')
    op.drop_index(op.f('ix_meta_items_item_type_id'), table_name='meta_items')
    op.drop_index(op.f('ix_meta_items_config_id'), table_name='meta_items')
    op.drop_index(op.f('ix_meta_config_option_sets_name'), table_name='meta_config_option_sets')
    op.drop_index(op.f('ix_meta_config_option_sets_item_type_id'), table_name='meta_config_option_sets')
    op.drop_index(op.f('ix_meta_approval_request_events_request_id'), table_name='meta_approval_request_events')
    op.drop_index(op.f('ix_meta_sync_jobs_site_id'), table_name='meta_sync_jobs')
    op.drop_index(op.f('ix_meta_report_executions_report_id'), table_name='meta_report_executions')
    op.drop_index(op.f('ix_meta_maintenance_equipment_category_id'), table_name='meta_maintenance_equipment')
    op.drop_index(op.f('ix_meta_files_file_type'), table_name='meta_files')
    op.drop_index(op.f('ix_meta_files_checksum'), table_name='meta_files')
    op.drop_index(op.f('ix_meta_approval_requests_entity_type'), table_name='meta_approval_requests')
    op.drop_index(op.f('ix_meta_approval_requests_entity_id'), table_name='meta_approval_requests')
    op.drop_index(op.f('ix_meta_approval_requests_category_id'), table_name='meta_approval_requests')
    op.drop_index(op.f('ix_meta_workorder_document_links_routing_id'), table_name='meta_workorder_document_links')
    op.drop_index(op.f('ix_meta_workorder_document_links_operation_id'), table_name='meta_workorder_document_links')
    op.drop_index(op.f('ix_meta_workorder_document_links_document_version_id'), table_name='meta_workorder_document_links')
    op.drop_index(op.f('ix_meta_workorder_document_links_document_item_id'), table_name='meta_workorder_document_links')
    op.drop_index(op.f('ix_meta_workflow_custom_action_runs_rule_id'), table_name='meta_workflow_custom_action_runs')
    op.drop_index(op.f('ix_meta_workflow_custom_action_runs_object_id'), table_name='meta_workflow_custom_action_runs')
    op.drop_index(op.f('ix_meta_workflow_custom_action_rules_workflow_map_id'), table_name='meta_workflow_custom_action_rules')
    op.drop_index(op.f('ix_meta_workflow_custom_action_rules_to_state'), table_name='meta_workflow_custom_action_rules')
    op.drop_index(op.f('ix_meta_workflow_custom_action_rules_from_state'), table_name='meta_workflow_custom_action_rules')
    op.drop_index(op.f('ix_meta_translations_record_type'), table_name='meta_translations')
    op.drop_index(op.f('ix_meta_translations_record_id'), table_name='meta_translations')
    op.drop_index(op.f('ix_meta_translations_lang'), table_name='meta_translations')
    op.drop_index(op.f('ix_meta_subcontract_approval_role_mappings_scope_value'), table_name='meta_subcontract_approval_role_mappings')
    op.drop_index(op.f('ix_meta_subcontract_approval_role_mappings_scope_type'), table_name='meta_subcontract_approval_role_mappings')
    op.drop_index(op.f('ix_meta_subcontract_approval_role_mappings_role_code'), table_name='meta_subcontract_approval_role_mappings')
    op.drop_index(op.f('ix_meta_store_listings_name'), table_name='meta_store_listings')
    op.drop_index(op.f('ix_meta_remote_sites_name'), table_name='meta_remote_sites')
    op.drop_index(op.f('ix_meta_plugin_configs_tenant_id'), table_name='meta_plugin_configs')
    op.drop_index(op.f('ix_meta_plugin_configs_plugin_id'), table_name='meta_plugin_configs')
    op.drop_index(op.f('ix_meta_plugin_configs_org_id'), table_name='meta_plugin_configs')
    op.drop_index(op.f('ix_meta_numbering_sequences_tenant_id'), table_name='meta_numbering_sequences')
    op.drop_index(op.f('ix_meta_numbering_sequences_org_id'), table_name='meta_numbering_sequences')
    op.drop_index(op.f('ix_meta_numbering_sequences_item_type_id'), table_name='meta_numbering_sequences')
    op.drop_index(op.f('ix_meta_maintenance_categories_parent_id'), table_name='meta_maintenance_categories')
    op.drop_index(op.f('ix_meta_eco_activity_gates_status'), table_name='meta_eco_activity_gates')
    op.drop_index(op.f('ix_meta_eco_activity_gates_eco_id'), table_name='meta_eco_activity_gates')
    op.drop_index(op.f('ix_meta_eco_activity_gate_events_eco_id'), table_name='meta_eco_activity_gate_events')
    op.drop_index(op.f('ix_meta_eco_activity_gate_events_activity_id'), table_name='meta_eco_activity_gate_events')
    op.drop_index(op.f('ix_meta_conversion_jobs_status'), table_name='meta_conversion_jobs')
    op.drop_index(op.f('ix_meta_conversion_jobs_dedupe_key'), table_name='meta_conversion_jobs')
    op.drop_index(op.f('ix_meta_consumption_records_source_id'), table_name='meta_consumption_records')
    op.drop_index(op.f('ix_meta_consumption_records_recorded_at'), table_name='meta_consumption_records')
    op.drop_index(op.f('ix_meta_consumption_records_plan_id'), table_name='meta_consumption_records')
    op.drop_index(op.f('ix_meta_consumption_plans_state'), table_name='meta_consumption_plans')
    op.drop_index(op.f('ix_meta_consumption_plans_name'), table_name='meta_consumption_plans')
    op.drop_index(op.f('ix_meta_consumption_plans_item_id'), table_name='meta_consumption_plans')
    op.drop_index(op.f('ix_meta_breakage_incidents_version_id'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_status'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_severity'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_routing_id'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_production_order_id'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_product_item_id'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_mbom_id'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_incident_code'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_eco_id'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_customer_name'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_bom_line_item_id'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_bom_id'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_breakage_incidents_batch_code'), table_name='meta_breakage_incidents')
    op.drop_index(op.f('ix_meta_approval_categories_parent_id'), table_name='meta_approval_categories')
    op.drop_index(op.f('ix_meta_3d_overlays_status'), table_name='meta_3d_overlays')
    op.drop_index(op.f('ix_meta_3d_overlays_document_item_id'), table_name='meta_3d_overlays')
    op.drop_index(op.f('ix_cad_change_logs_user_id'), table_name='cad_change_logs')
    op.drop_index(op.f('ix_cad_change_logs_tenant_id'), table_name='cad_change_logs')
    op.drop_index(op.f('ix_cad_change_logs_org_id'), table_name='cad_change_logs')
    op.drop_index(op.f('ix_cad_change_logs_file_id'), table_name='cad_change_logs')
    op.drop_table('meta_subcontract_order_events')
    op.drop_table('meta_subcontract_orders')
    op.drop_table('meta_mbom_lines')
    op.drop_table('meta_workflow_tasks')
    op.drop_table('meta_quality_alerts')
    op.drop_table('meta_operations')
    op.drop_table('meta_eco_routing_changes')
    op.drop_table('meta_eco_bom_changes')
    op.drop_table('meta_eco_approvals')
    op.drop_table('meta_cut_results')
    op.drop_table('meta_baseline_members')
    op.drop_table('meta_baseline_comparisons')
    op.drop_table('meta_workflow_activity_instances')
    op.drop_table('meta_version_history')
    op.drop_table('meta_version_files')
    op.drop_table('meta_similarity_records')
    op.drop_table('meta_signature_audit_logs')
    op.drop_table('meta_routings')
    op.drop_table('meta_quality_checks')
    op.drop_table('meta_item_iterations')
    op.drop_table('meta_effectivities')
    op.drop_table('meta_ecos')
    op.drop_table('meta_cut_plans')
    op.drop_table('meta_box_contents')
    op.drop_table('meta_baselines')
    op.drop_table('meta_workflow_processes')
    op.drop_table('meta_variant_rules')
    op.drop_table('meta_signature_manifests')
    op.drop_table('meta_relationships')
    op.drop_table('meta_raw_materials')
    op.drop_table('meta_quality_points')
    op.drop_table('meta_product_configurations')
    op.drop_table('meta_manufacturing_boms')
    op.drop_table('meta_item_versions')
    op.drop_table('meta_item_files')
    op.drop_table('meta_geometric_indices')
    op.drop_table('meta_electronic_signatures')
    op.drop_table('meta_dedup_batches')
    op.drop_table('meta_config_options')
    op.drop_table('meta_box_items')
    op.drop_table('meta_workflow_transitions')
    op.drop_table('meta_view_mappings')
    op.drop_table('meta_sync_records')
    op.drop_table('meta_state_identity_permissions')
    op.drop_table('meta_signing_reasons')
    op.drop_table('meta_saved_searches')
    op.drop_table('meta_revision_schemes')
    op.drop_table('meta_properties')
    op.drop_table('meta_maintenance_requests')
    op.drop_table('meta_lifecycle_transitions')
    op.drop_table('meta_items')
    op.drop_table('meta_dedup_rules')
    op.drop_table('meta_config_option_sets')
    op.drop_table('meta_approval_request_events')
    op.drop_table('meta_workflow_activities')
    op.drop_table('meta_sync_jobs')
    op.drop_table('meta_report_executions')
    op.drop_table('meta_relationship_types')
    op.drop_table('meta_maintenance_equipment')
    op.drop_table('meta_lifecycle_states')
    op.drop_table('meta_item_types')
    op.drop_table('meta_grid_columns')
    op.drop_table('meta_form_fields')
    op.drop_table('meta_files')
    op.drop_table('meta_extensions')
    op.drop_table('meta_approval_requests')
    op.drop_table('meta_app_licenses')
    op.drop_table('meta_access')
    op.drop_table('meta_workorder_document_links')
    op.drop_table('meta_workflow_maps')
    op.drop_table('meta_workflow_custom_action_runs')
    op.drop_table('meta_workflow_custom_action_rules')
    op.drop_table('meta_workcenters')
    op.drop_table('meta_vaults')
    op.drop_table('meta_translations')
    op.drop_table('meta_sync_sites')
    op.drop_table('meta_subcontract_approval_role_mappings')
    op.drop_table('meta_store_listings')
    op.drop_table('meta_report_locale_profiles')
    op.drop_table('meta_report_definitions')
    op.drop_table('meta_remote_sites')
    op.drop_table('meta_plugin_configs')
    op.drop_table('meta_permissions')
    op.drop_table('meta_numbering_sequences')
    op.drop_table('meta_methods')
    op.drop_table('meta_maintenance_categories')
    op.drop_table('meta_lifecycle_maps')
    op.drop_table('meta_grid_views')
    op.drop_table('meta_forms')
    op.drop_table('meta_extension_points')
    op.drop_table('meta_eco_stages')
    op.drop_table('meta_eco_activity_gates')
    op.drop_table('meta_eco_activity_gate_events')
    op.drop_table('meta_dashboards')
    op.drop_table('meta_conversion_jobs')
    op.drop_table('meta_consumption_records')
    op.drop_table('meta_consumption_plans')
    op.drop_table('meta_breakage_incidents')
    op.drop_table('meta_approval_categories')
    op.drop_table('meta_app_registry')
    op.drop_table('meta_3d_overlays')
    op.drop_table('cad_change_logs')
