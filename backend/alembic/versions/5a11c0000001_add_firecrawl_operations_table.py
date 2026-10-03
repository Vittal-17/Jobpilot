"""Add firecrawl operations table

Revision ID: 5a11c0000001
Revises: 4ecab3f5c08c
Create Date: 2026-10-02 19:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a11c0000001'
down_revision: Union[str, Sequence[str], None] = '4ecab3f5c08c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'firecrawl_operations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('operation', sa.String(length=20), nullable=False),
        sa.Column('cost_units', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("operation IN ('discovery', 'enrichment')", name='chk_firecrawl_ops_operation'),
        sa.CheckConstraint('cost_units >= 0', name='chk_firecrawl_ops_cost'),
        sa.CheckConstraint("status IN ('success', 'failed', 'denied', 'payment_required')", name='chk_firecrawl_ops_status')
    )
    op.create_index('idx_firecrawl_ops_created_status', 'firecrawl_operations', ['created_at', 'status'])
    op.create_index('idx_firecrawl_ops_order', 'firecrawl_operations', [sa.text('created_at DESC'), sa.text('id DESC')])


def downgrade() -> None:
    op.drop_index('idx_firecrawl_ops_order', table_name='firecrawl_operations')
    op.drop_index('idx_firecrawl_ops_created_status', table_name='firecrawl_operations')
    op.drop_table('firecrawl_operations')
