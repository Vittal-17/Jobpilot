"""Durable execution ownership, billing governance and refresh state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "5a11c0000002"
down_revision = "5a11c0000001"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("jobs", sa.Column("requires_refresh", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("jobs", sa.Column("validation_status", sa.String(32)))
    op.add_column("jobs", sa.Column("source_observation", postgresql.JSONB()))
    op.add_column("jobs", sa.Column("posting_facts", postgresql.JSONB()))
    op.add_column("notification_deliveries", sa.Column("invalidated_at", sa.DateTime(timezone=True)))
    op.add_column("notification_deliveries", sa.Column("unknown_at", sa.DateTime(timezone=True)))
    op.add_column("notification_deliveries", sa.Column("unknown_reason", sa.String(500)))
    op.create_index(
        "ix_notification_deliveries_unknown_at",
        "notification_deliveries",
        ["unknown_at"],
        postgresql_where=sa.text("unknown_at IS NOT NULL"),
    )
    op.add_column("recommendation_history", sa.Column("source_execution_id", sa.Integer()))
    op.create_foreign_key("fk_recommendation_execution", "recommendation_history", "search_execution", ["source_execution_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_recommendation_history_source_execution_id", "recommendation_history", ["source_execution_id"])
    op.create_table("search_execution_jobs", sa.Column("execution_id", sa.Integer(), sa.ForeignKey("search_execution.id", ondelete="CASCADE"), primary_key=True), sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True), sa.Column("ingestion_outcome", sa.String(16)), sa.Column("description_is_snippet", sa.Boolean()), sa.Column("enrichment_status", sa.String(50)), sa.Column("validation", sa.String(50)), sa.Column("rejection_reason", sa.String(255)), sa.Column("fresher_eligible", sa.Boolean()), sa.Column("eligibility_counted", sa.Boolean(), server_default=sa.text("false"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("provider_reservations", sa.Column("id", sa.String(36), primary_key=True), sa.Column("operation_key", sa.String(255), nullable=False), sa.Column("provider_name", sa.String(50), nullable=False), sa.Column("cost_units", sa.Integer(), nullable=False), sa.Column("usage_date", sa.Date(), nullable=False), sa.Column("execution_id", sa.Integer(), sa.ForeignKey("search_execution.id", ondelete="SET NULL")), sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL")), sa.Column("lease_token", sa.String(36)), sa.Column("status", sa.String(20), server_default="reserved", nullable=False), sa.Column("billing_metadata", postgresql.JSONB()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.CheckConstraint("cost_units > 0", name="chk_provider_reservation_cost"), sa.CheckConstraint("status IN ('reserved', 'in_flight', 'succeeded', 'failed', 'payment_required', 'unknown_after_crash')", name="chk_provider_reservation_status"), sa.UniqueConstraint("operation_key", name="uq_provider_reservations_operation_key"))
    op.create_index("ix_provider_reservations_execution_id", "provider_reservations", ["execution_id"])
    op.create_table("firecrawl_budget_state", sa.Column("month", sa.Date(), primary_key=True), sa.Column("account_budget", sa.Integer(), nullable=False), sa.Column("automation_cap", sa.Integer(), nullable=False), sa.Column("protected_reserve", sa.Integer(), nullable=False), sa.Column("payment_required", sa.Boolean(), server_default=sa.text("false"), nullable=False), sa.Column("exhausted_at", sa.DateTime(timezone=True)), sa.CheckConstraint("account_budget >= 0 AND automation_cap >= 0 AND protected_reserve >= 0 AND automation_cap <= account_budget - protected_reserve", name="chk_firecrawl_budget_policy"))
    op.create_table("firecrawl_discovery_runs", sa.Column("run_id", sa.String(64), primary_key=True), sa.Column("plan_hash", sa.String(64), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("receipt", postgresql.JSONB()), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.create_table("firecrawl_query_state", sa.Column("query_key", sa.String(64), primary_key=True), sa.Column("keywords", sa.String(100), nullable=False), sa.Column("location", sa.String(100), nullable=False), sa.Column("original_keywords", sa.String(255)), sa.Column("original_location", sa.String(255)), sa.Column("last_planned_at", sa.DateTime(timezone=True)), sa.Column("last_executed_at", sa.DateTime(timezone=True)))
    for name, type_ in (("execution_id", sa.Integer()), ("job_id", sa.Integer()), ("reservation_id", sa.String(36)), ("operation_key", sa.String(255)), ("metadata_json", postgresql.JSONB())):
        op.add_column("firecrawl_operations", sa.Column(name, type_))
    op.create_unique_constraint("uq_firecrawl_ops_operation_key", "firecrawl_operations", ["operation_key"])
    op.drop_constraint("chk_firecrawl_ops_status", "firecrawl_operations", type_="check")
    op.create_check_constraint("chk_firecrawl_ops_status", "firecrawl_operations", "status IN ('success', 'succeeded', 'failed', 'denied', 'payment_required', 'reserved', 'in_flight', 'unknown_after_crash')")
    op.create_foreign_key("fk_firecrawl_operation_execution", "firecrawl_operations", "search_execution", ["execution_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_firecrawl_operation_job", "firecrawl_operations", "jobs", ["job_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_firecrawl_operation_reservation", "firecrawl_operations", "provider_reservations", ["reservation_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_firecrawl_operations_execution_id", "firecrawl_operations", ["execution_id"])

def downgrade():
    # This revision has no committed migration history in this repository.
    # Never erase durable billing/replay evidence, even terminal successful
    # receipts. Lock before checking so concurrent inserts cannot race DDL.
    op.execute("LOCK TABLE jobs, job_enrichments, search_execution, provider_reservations, "
               "firecrawl_operations, firecrawl_discovery_runs, firecrawl_query_state, "
               "firecrawl_budget_state, search_execution_jobs, recommendation_history, "
               "notification_deliveries IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM provider_reservations)
                OR EXISTS (SELECT 1 FROM firecrawl_discovery_runs)
                OR EXISTS (SELECT 1 FROM firecrawl_query_state)
                OR EXISTS (SELECT 1 FROM firecrawl_budget_state WHERE payment_required)
                OR EXISTS (SELECT 1 FROM firecrawl_operations
                           WHERE operation_key IS NOT NULL OR reservation_id IS NOT NULL
                              OR status IN ('reserved','in_flight','unknown_after_crash','succeeded'))
            THEN
                RAISE EXCEPTION 'Cannot downgrade: durable reservation/discovery replay evidence exists';
            END IF;
        END $$
    """)
    op.drop_index("ix_firecrawl_operations_execution_id", table_name="firecrawl_operations")
    for name in ("fk_firecrawl_operation_reservation", "fk_firecrawl_operation_job", "fk_firecrawl_operation_execution"):
        op.drop_constraint(name, "firecrawl_operations", type_="foreignkey")
    op.drop_constraint("chk_firecrawl_ops_status", "firecrawl_operations", type_="check")
    op.drop_constraint("uq_firecrawl_ops_operation_key", "firecrawl_operations", type_="unique")
    op.create_check_constraint(
        "chk_firecrawl_ops_status",
        "firecrawl_operations",
        "status IN ('success', 'failed', 'denied', 'payment_required')",
    )
    for name in ("metadata_json", "operation_key", "reservation_id", "job_id", "execution_id"):
        op.drop_column("firecrawl_operations", name)
    for name in ("firecrawl_query_state", "firecrawl_discovery_runs", "firecrawl_budget_state", "provider_reservations", "search_execution_jobs"):
        op.drop_table(name)
    op.drop_index("ix_recommendation_history_source_execution_id", table_name="recommendation_history")
    op.drop_constraint("fk_recommendation_execution", "recommendation_history", type_="foreignkey")
    op.drop_column("recommendation_history", "source_execution_id")
    op.drop_column("notification_deliveries", "invalidated_at")
    op.drop_index("ix_notification_deliveries_unknown_at", table_name="notification_deliveries")
    op.drop_column("notification_deliveries", "unknown_reason")
    op.drop_column("notification_deliveries", "unknown_at")
    for name in ("posting_facts", "source_observation", "validation_status", "requires_refresh"):
        op.drop_column("jobs", name)
