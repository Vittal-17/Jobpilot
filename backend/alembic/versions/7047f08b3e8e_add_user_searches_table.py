"""add user_searches table

Revision ID: 7047f08b3e8e
Revises: 2edc23e92a4a
Create Date: 2026-09-14 13:13:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '7047f08b3e8e'
down_revision: Union[str, None] = '2edc23e92a4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        'user_searches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('query', sa.String(length=255), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('remote_only', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("query IS NOT NULL OR location IS NOT NULL", name="chk_user_searches_not_empty"),
        sa.CheckConstraint("query IS NULL OR length(trim(query)) > 0", name="chk_user_searches_query_not_empty"),
        sa.CheckConstraint("location IS NULL OR length(trim(location)) > 0", name="chk_user_searches_location_not_empty")
    )
    op.create_index(op.f('ix_user_searches_user_id'), 'user_searches', ['user_id'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_user_searches_user_id'), table_name='user_searches')
    op.drop_table('user_searches')
