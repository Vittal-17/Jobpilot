"""Durably stage bounded Firecrawl search results before settlement."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "5a11c0000005"
down_revision = "5a11c0000004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("firecrawl_operations", sa.Column("result_state", sa.String(16), nullable=False, server_default="none"))
    op.add_column("firecrawl_operations", sa.Column("staged_payload", postgresql.JSONB(), nullable=True))
    op.add_column("firecrawl_operations", sa.Column("staged_payload_sha256", sa.String(64), nullable=True))
    op.add_column("firecrawl_operations", sa.Column("staged_payload_bytes", sa.Integer(), nullable=True))
    op.add_column("firecrawl_operations", sa.Column("staged_job_count", sa.Integer(), nullable=True))
    op.add_column("firecrawl_operations", sa.Column("staged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("firecrawl_operations", sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "chk_firecrawl_ops_result_state",
        "firecrawl_operations",
        "result_state IN ('none', 'staged', 'consumed')",
    )
    op.create_check_constraint(
        "chk_firecrawl_ops_staged_metadata",
        "firecrawl_operations",
        "(result_state = 'none' AND staged_payload IS NULL AND staged_payload_sha256 IS NULL AND staged_payload_bytes IS NULL AND staged_job_count IS NULL AND staged_at IS NULL AND consumed_at IS NULL) OR (result_state IN ('staged', 'consumed') AND staged_payload IS NOT NULL AND staged_payload_sha256 IS NOT NULL AND staged_payload_bytes IS NOT NULL AND staged_job_count IS NOT NULL AND staged_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "chk_firecrawl_ops_staged_bounds",
        "firecrawl_operations",
        "(staged_payload_bytes IS NULL OR staged_payload_bytes BETWEEN 0 AND 1048576) AND (staged_job_count IS NULL OR staged_job_count BETWEEN 0 AND 100)",
    )


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM firecrawl_operations WHERE result_state IN ('staged', 'consumed') OR staged_payload IS NOT NULL) THEN
            RAISE EXCEPTION 'Cannot downgrade while Firecrawl result evidence exists';
        END IF;
    END $$""")
    op.drop_constraint("chk_firecrawl_ops_staged_bounds", "firecrawl_operations", type_="check")
    op.drop_constraint("chk_firecrawl_ops_staged_metadata", "firecrawl_operations", type_="check")
    op.drop_constraint("chk_firecrawl_ops_result_state", "firecrawl_operations", type_="check")
    op.drop_column("firecrawl_operations", "consumed_at")
    op.drop_column("firecrawl_operations", "staged_at")
    op.drop_column("firecrawl_operations", "staged_job_count")
    op.drop_column("firecrawl_operations", "staged_payload_bytes")
    op.drop_column("firecrawl_operations", "staged_payload_sha256")
    op.drop_column("firecrawl_operations", "staged_payload")
    op.drop_column("firecrawl_operations", "result_state")
