"""Add durable parent and child discovery ownership leases."""
from alembic import op
import sqlalchemy as sa


revision = "5a11c0000007"
down_revision = "5a11c0000006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("firecrawl_discovery_runs", sa.Column("owner_token", sa.String(64), nullable=True))
    op.add_column("firecrawl_discovery_runs", sa.Column("owner_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("search_execution", sa.Column("ownership_token", sa.String(64), nullable=True))
    op.add_column("search_execution", sa.Column("ownership_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_search_execution_ownership_expires_at",
        "search_execution",
        ["ownership_expires_at"],
    )


def downgrade():
    op.drop_index("ix_search_execution_ownership_expires_at", table_name="search_execution")
    op.drop_column("search_execution", "ownership_expires_at")
    op.drop_column("search_execution", "ownership_token")
    op.drop_column("firecrawl_discovery_runs", "owner_expires_at")
    op.drop_column("firecrawl_discovery_runs", "owner_token")
