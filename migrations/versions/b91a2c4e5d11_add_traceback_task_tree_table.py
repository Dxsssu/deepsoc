"""Add traceback_task_trees table

Revision ID: b91a2c4e5d11
Revises: 71f72226a5f5
Create Date: 2026-04-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b91a2c4e5d11'
down_revision = '71f72226a5f5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'traceback_task_trees',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('event_id', sa.String(length=64), nullable=False),
        sa.Column('ttt_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('ttt_schema_version', sa.String(length=32), nullable=False, server_default='1.0'),
        sa.Column('tree_json', sa.JSON(), nullable=False),
        sa.Column('updated_by', sa.String(length=64), nullable=False, server_default='_captain'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('event_id', 'ttt_version', name='uq_ttt_event_version'),
    )
    op.create_index('ix_traceback_task_trees_event_id', 'traceback_task_trees', ['event_id'], unique=False)


def downgrade():
    op.drop_index('ix_traceback_task_trees_event_id', table_name='traceback_task_trees')
    op.drop_table('traceback_task_trees')
