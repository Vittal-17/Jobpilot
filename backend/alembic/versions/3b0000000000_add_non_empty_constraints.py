"""Add non empty constraints

Revision ID: 3b0000000000
Revises: 3a0000000000
Create Date: 2026-09-08 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b0000000000'
down_revision: Union[str, None] = '3a0000000000'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint('chk_jobs_title_not_empty', 'jobs', "length(trim(title)) > 0")
    op.create_check_constraint('chk_jobs_company_not_empty', 'jobs', "length(trim(company)) > 0")
    op.create_check_constraint('chk_jobs_source_not_empty', 'jobs', "length(trim(source)) > 0")
    op.create_check_constraint('chk_jobs_source_job_id_not_empty', 'jobs', "length(trim(source_job_id)) > 0")


def downgrade() -> None:
    op.drop_constraint('chk_jobs_title_not_empty', 'jobs', type_='check')
    op.drop_constraint('chk_jobs_company_not_empty', 'jobs', type_='check')
    op.drop_constraint('chk_jobs_source_not_empty', 'jobs', type_='check')
    op.drop_constraint('chk_jobs_source_job_id_not_empty', 'jobs', type_='check')
