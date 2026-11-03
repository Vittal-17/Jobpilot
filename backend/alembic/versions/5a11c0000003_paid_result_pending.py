"""Stage paid provider content before final enrichment settlement."""
from alembic import op
import sqlalchemy as sa

revision = "5a11c0000003"
down_revision = "5a11c0000002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_enrichments", sa.Column("staged_content", sa.Text(), nullable=True))
    op.drop_constraint("chk_job_enrichments_status", "job_enrichments", type_="check")
    op.create_check_constraint(
        "chk_job_enrichments_status",
        "job_enrichments",
        "status IN ('pending', 'in_progress', 'result_pending', 'success', 'failure', 'unsupported', 'retry')",
    )


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM job_enrichments WHERE staged_content IS NOT NULL OR status='result_pending') THEN
            RAISE EXCEPTION 'Cannot downgrade while staged paid results exist';
        END IF;
    END $$""")
    op.drop_constraint("chk_job_enrichments_status", "job_enrichments", type_="check")
    op.create_check_constraint(
        "chk_job_enrichments_status",
        "job_enrichments",
        "status IN ('pending', 'in_progress', 'success', 'failure', 'unsupported', 'retry')",
    )
    op.drop_column("job_enrichments", "staged_content")
