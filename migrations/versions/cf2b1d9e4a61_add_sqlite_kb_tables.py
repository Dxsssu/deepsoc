"""Add SQLite KB tables for SOP/assets/runtime annotations

Revision ID: cf2b1d9e4a61
Revises: b91a2c4e5d11
Create Date: 2026-04-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cf2b1d9e4a61'
down_revision = 'b91a2c4e5d11'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sop',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('alert_type_key', sa.String(length=128), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('content_md', sa.Text(), nullable=False, server_default=''),
        sa.Column('version', sa.String(length=32), nullable=False, server_default='1.0.0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('alert_type_key', name='uq_sop_alert_type_key'),
    )

    op.create_table(
        'assets',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('asset_key', sa.String(length=256), nullable=False),
        sa.Column('asset_type', sa.String(length=64), nullable=True),
        sa.Column('asset_group', sa.String(length=128), nullable=True),
        sa.Column('criticality', sa.String(length=32), nullable=True),
        sa.Column('owner', sa.String(length=128), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('asset_key', name='uq_assets_asset_key'),
    )

    op.create_table(
        'runtime_annotations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('annotation_key', sa.String(length=256), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('content_md', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index(
        'ix_runtime_annotations_annotation_key',
        'runtime_annotations',
        ['annotation_key'],
        unique=False,
    )


def downgrade():
    op.drop_index('ix_runtime_annotations_annotation_key', table_name='runtime_annotations')
    op.drop_table('runtime_annotations')

    op.drop_table('assets')

    op.drop_table('sop')
