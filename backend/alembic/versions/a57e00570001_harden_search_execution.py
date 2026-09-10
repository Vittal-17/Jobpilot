"""harden search execution state invariants

Revision ID: a57e00570001
Revises: 97dd355ba57c
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a57e00570001"
down_revision: Union[str, Sequence[str], None] = "97dd355ba57c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        UPDATE search_execution
        SET status = 'failed',
            completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
            error_message = COALESCE(error_message, 'invalid legacy status repaired')
        WHERE status NOT IN ('selected', 'started', 'succeeded', 'failed')
    """)
    op.create_check_constraint(
        "chk_status_valid",
        "search_execution",
        "status IN ('selected', 'started', 'succeeded', 'failed')",
    )
    op.drop_index("uq_active_claim", table_name="search_execution")
    op.create_index(
        "uq_active_claim",
        "search_execution",
        ["candidate_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('selected', 'started')"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_claim", table_name="search_execution")
    op.create_index(
        "uq_active_claim",
        "search_execution",
        ["candidate_id"],
        unique=True,
        postgresql_where=sa.text("status = 'selected'"),
    )
    op.drop_constraint("chk_status_valid", "search_execution", type_="check")
