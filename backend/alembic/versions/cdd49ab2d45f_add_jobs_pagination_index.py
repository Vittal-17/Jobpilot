"""add_jobs_pagination_index

Revision ID: cdd49ab2d45f
Revises: 202609191045
Create Date: 2026-09-20 03:26:26.652545

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cdd49ab2d45f'
down_revision: Union[str, Sequence[str], None] = '202609191045'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE INDEX ix_jobs_pagination ON jobs (published_at DESC NULLS LAST, discovered_at DESC, id DESC)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX ix_jobs_pagination")
