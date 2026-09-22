"""add_send_started_at_to_notification_deliveries

Revision ID: 4b568321e102
Revises: 016d11fafc0f
Create Date: 2026-09-22 18:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4b568321e102'
down_revision: Union[str, Sequence[str], None] = '016d11fafc0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('notification_deliveries', sa.Column('send_started_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('notification_deliveries', 'send_started_at')
