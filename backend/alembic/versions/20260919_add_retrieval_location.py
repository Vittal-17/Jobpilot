"""add retrieval_location to search_execution

Revision ID: 202609191045
Revises: 2f8e25ff3da4
Create Date: 2026-09-19 10:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
from typing import Union, Sequence

# revision identifiers, used by Alembic.
revision: str = '202609191045'
down_revision: Union[str, Sequence[str], None] = '2f8e25ff3da4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    op.add_column('search_execution', sa.Column('retrieval_location', sa.String(length=100), nullable=True))

def downgrade():
    op.drop_column('search_execution', 'retrieval_location')
