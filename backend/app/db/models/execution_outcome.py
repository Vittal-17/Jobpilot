"""Durable ownership, billing and execution provenance for Firecrawl work."""
from datetime import date, datetime
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class ExecutionJobModel(Base):
    __tablename__ = "search_execution_jobs"
    execution_id: Mapped[int] = mapped_column(ForeignKey("search_execution.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    ingestion_outcome: Mapped[str | None] = mapped_column(String(16))
    description_is_snippet: Mapped[bool | None] = mapped_column(Boolean)
    enrichment_status: Mapped[str | None] = mapped_column(String(50))
    validation: Mapped[str | None] = mapped_column(String(50))
    rejection_reason: Mapped[str | None] = mapped_column(String(255))
    fresher_eligible: Mapped[bool | None] = mapped_column(Boolean)
    eligibility_counted: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class ProviderReservationModel(Base):
    __tablename__ = "provider_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Idempotency key for the provider request.  A receipt is tied to this
    # key, rather than to a worker process, so a restarted worker cannot spend
    # another unit for the same external request.
    operation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False)
    cost_units: Mapped[int] = mapped_column(Integer, nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    execution_id: Mapped[int | None] = mapped_column(ForeignKey("search_execution.id", ondelete="SET NULL"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), server_default="reserved", nullable=False)
    billing_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("cost_units > 0", name="chk_provider_reservation_cost"),
        CheckConstraint(
            "status IN ('reserved', 'in_flight', 'succeeded', 'failed', 'payment_required', 'unknown_after_crash')",
            name="chk_provider_reservation_status",
        ),
        UniqueConstraint("operation_key", name="uq_provider_reservations_operation_key"),
        UniqueConstraint("id", "operation_key", name="uq_provider_reservation_id_operation_key"),
    )

class FirecrawlBudgetModel(Base):
    __tablename__ = "firecrawl_budget_state"
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    account_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    automation_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    protected_reserve: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_required: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    exhausted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("account_budget >= 0 AND automation_cap >= 0 AND protected_reserve >= 0 AND automation_cap <= account_budget - protected_reserve", name="chk_firecrawl_budget_policy"),)

class FirecrawlDiscoveryRunModel(Base):
    __tablename__ = "firecrawl_discovery_runs"
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    receipt: Mapped[dict | None] = mapped_column(JSONB)
    owner_token: Mapped[str | None] = mapped_column(String(64))
    owner_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class FirecrawlQueryStateModel(Base):
    __tablename__ = "firecrawl_query_state"
    query_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    keywords: Mapped[str] = mapped_column(String(100), nullable=False)
    location: Mapped[str] = mapped_column(String(100), nullable=False)
    original_keywords: Mapped[str | None] = mapped_column(String(255))
    original_location: Mapped[str | None] = mapped_column(String(255))
    last_planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
