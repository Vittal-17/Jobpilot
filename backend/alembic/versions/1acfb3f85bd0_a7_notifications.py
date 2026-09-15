"""a7_notifications

Revision ID: 1acfb3f85bd0
Revises: f25ff86125c9
Create Date: 2026-09-14 20:18:17.579161

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1acfb3f85bd0'
down_revision: Union[str, Sequence[str], None] = 'f25ff86125c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'notification_deliveries',
        sa.Column('delivery_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('delivery_id')
    )
    op.create_index(op.f('ix_notification_deliveries_user_id'), 'notification_deliveries', ['user_id'], unique=False)

    op.add_column('recommendation_history', sa.Column('delivery_id', sa.String(length=64), nullable=True))
    op.create_foreign_key('fk_recommendation_history_delivery_id', 'recommendation_history', 'notification_deliveries', ['delivery_id'], ['delivery_id'], ondelete='SET NULL')
    op.create_index(op.f('ix_recommendation_history_delivery_id'), 'recommendation_history', ['delivery_id'], unique=False)

def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_recommendation_history_delivery_id'), table_name='recommendation_history')
    op.drop_constraint('fk_recommendation_history_delivery_id', 'recommendation_history', type_='foreignkey')
    op.drop_column('recommendation_history', 'delivery_id')

    op.drop_index(op.f('ix_notification_deliveries_user_id'), table_name='notification_deliveries')
    op.drop_table('notification_deliveries')
