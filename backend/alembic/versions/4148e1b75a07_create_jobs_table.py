"""Create jobs table

Revision ID: 4148e1b75a07
Revises:
Create Date: 2026-09-08 16:51:26.513031

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4148e1b75a07'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('company', sa.String(length=255), nullable=False),
    sa.Column('source', sa.String(length=100), nullable=False),
    sa.Column('source_job_id', sa.String(length=255), nullable=False),
    sa.Column('discovered_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('remote', sa.Boolean(), nullable=True),
    sa.Column('employment_type', sa.String(length=100), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('salary_min', sa.Integer(), nullable=True),
    sa.Column('salary_max', sa.Integer(), nullable=True),
    sa.Column('currency', sa.String(length=10), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('match_score', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('match_score >= 0 AND match_score <= 100', name='chk_jobs_match_score'),
    sa.CheckConstraint('salary_max >= 0', name='chk_jobs_salary_max'),
    sa.CheckConstraint('salary_min >= 0', name='chk_jobs_salary_min'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source', 'source_job_id', name='uq_jobs_source_source_job_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('jobs')
