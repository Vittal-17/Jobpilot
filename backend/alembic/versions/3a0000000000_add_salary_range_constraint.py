"""Add salary range constraint

Revision ID: 3a0000000000
Revises: 17f4b0fbca2b
Create Date: 2026-09-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a0000000000'
down_revision: Union[str, None] = '17f4b0fbca2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        'chk_jobs_salary_range',
        'jobs',
        'salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max'
    )


def downgrade() -> None:
    op.drop_constraint('chk_jobs_salary_range', 'jobs', type_='check')
