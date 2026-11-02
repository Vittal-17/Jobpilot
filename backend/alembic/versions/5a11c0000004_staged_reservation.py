"""Link a durable paid result to its exact reservation, never infer ownership."""
from alembic import op
import sqlalchemy as sa

revision = "5a11c0000004"
down_revision = "5a11c0000003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_enrichments", sa.Column("staged_reservation_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_enrichment_staged_reservation", "job_enrichments", "provider_reservations", ["staged_reservation_id"], ["id"], ondelete="SET NULL")


def downgrade():
    # Losing receipt identity would let older workers optimistically finalize
    # paid results. Refuse rather than discard recovery evidence.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM job_enrichments WHERE staged_reservation_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Cannot downgrade while linked paid results exist';
        END IF;
    END $$""")
    op.drop_constraint("fk_enrichment_staged_reservation", "job_enrichments", type_="foreignkey")
    op.drop_column("job_enrichments", "staged_reservation_id")
