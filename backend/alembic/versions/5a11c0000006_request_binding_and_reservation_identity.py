"""Bind idempotency requests and make staged receipts non-detachable."""
from alembic import op
import sqlalchemy as sa


revision = "5a11c0000006"
down_revision = "5a11c0000005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "provider_reservations",
        sa.Column("request_fingerprint", sa.String(64), nullable=True),
    )
    op.add_column(
        "firecrawl_operations",
        sa.Column("request_fingerprint", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_provider_reservation_id_operation_key",
        "provider_reservations",
        ["id", "operation_key"],
    )
    op.drop_constraint(
        "fk_firecrawl_operation_reservation",
        "firecrawl_operations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_firecrawl_operation_reservation_identity",
        "firecrawl_operations",
        "provider_reservations",
        ["reservation_id", "operation_key"],
        ["id", "operation_key"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "chk_firecrawl_ops_staged_reservation",
        "firecrawl_operations",
        "result_state = 'none' OR (reservation_id IS NOT NULL AND operation_key IS NOT NULL)",
    )


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM firecrawl_operations
            WHERE result_state IN ('staged', 'consumed')
        ) THEN
            RAISE EXCEPTION 'Cannot downgrade while staged Firecrawl results exist';
        END IF;
    END $$""")
    op.drop_constraint(
        "chk_firecrawl_ops_staged_reservation",
        "firecrawl_operations",
        type_="check",
    )
    op.drop_constraint(
        "fk_firecrawl_operation_reservation_identity",
        "firecrawl_operations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_firecrawl_operation_reservation",
        "firecrawl_operations",
        "provider_reservations",
        ["reservation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "uq_provider_reservation_id_operation_key",
        "provider_reservations",
        type_="unique",
    )
    op.drop_column("firecrawl_operations", "request_fingerprint")
    op.drop_column("provider_reservations", "request_fingerprint")
