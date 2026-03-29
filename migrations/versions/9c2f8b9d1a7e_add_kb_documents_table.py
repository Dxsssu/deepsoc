"""Add kb_documents table

Revision ID: 9c2f8b9d1a7e
Revises: 71f72226a5f5
Create Date: 2026-03-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c2f8b9d1a7e'
down_revision = '71f72226a5f5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'kb_documents',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('doc_id', sa.String(length=64), nullable=False, unique=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('kb_type', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=256), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('doc_meta', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.Column('version', sa.Integer(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index(
        'ix_kb_documents_tenant_type_status',
        'kb_documents',
        ['tenant_id', 'kb_type', 'status'],
        unique=False,
    )
    op.create_index(
        'ix_kb_documents_tenant_updated_at',
        'kb_documents',
        ['tenant_id', 'updated_at'],
        unique=False,
    )


def downgrade():
    op.drop_index('ix_kb_documents_tenant_updated_at', table_name='kb_documents')
    op.drop_index('ix_kb_documents_tenant_type_status', table_name='kb_documents')
    op.drop_table('kb_documents')
