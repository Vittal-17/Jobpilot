"""Add score and reasons to recommendation history

Revision ID: 4ecab3f5c08c
Revises: 8da5c3cbdffc
Create Date: 2026-09-24 04:21:04.454086

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4ecab3f5c08c'
down_revision: Union[str, Sequence[str], None] = '8da5c3cbdffc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('recommendation_history', sa.Column('score', sa.Integer(), nullable=True))
    op.add_column('recommendation_history', sa.Column('reasons', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('recommendation_history', 'reasons')
    op.drop_column('recommendation_history', 'score')
