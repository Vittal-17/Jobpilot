from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, CheckConstraint, Index, ForeignKey, ForeignKeyConstraint, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, synonym
from app.db.base import Base

class FirecrawlOperationModel(Base):
    __tablename__ = "firecrawl_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    cost_units: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    operation_key: Mapped[str | None] = mapped_column(String(255))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    execution_id: Mapped[int | None] = mapped_column(ForeignKey("search_execution.id", ondelete="SET NULL"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    reservation_id: Mapped[str | None] = mapped_column(String(36))
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    result_state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="none")
    staged_payload: Mapped[dict | None] = mapped_column(JSONB)
    staged_payload_sha256: Mapped[str | None] = mapped_column(String(64))
    staged_payload_bytes: Mapped[int | None] = mapped_column(Integer)
    staged_job_count: Mapped[int | None] = mapped_column(Integer)
    staged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Readable compatibility names used by service callers and operational
    # tooling; the persisted names retain the explicit staging terminology.
    result_payload = synonym("staged_payload")
    result_hash = synonym("staged_payload_sha256")
    result_bytes = synonym("staged_payload_bytes")
    result_count = synonym("staged_job_count")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_firecrawl_operations_execution_id", "execution_id"),
        CheckConstraint("operation IN ('discovery', 'enrichment')", name="chk_firecrawl_ops_operation"),
        CheckConstraint("cost_units >= 0", name="chk_firecrawl_ops_cost"),
        CheckConstraint("result_state IN ('none', 'staged', 'consumed')", name="chk_firecrawl_ops_result_state"),
        CheckConstraint("(result_state = 'none' AND staged_payload IS NULL AND staged_payload_sha256 IS NULL AND staged_payload_bytes IS NULL AND staged_job_count IS NULL AND staged_at IS NULL AND consumed_at IS NULL) OR (result_state IN ('staged', 'consumed') AND staged_payload IS NOT NULL AND staged_payload_sha256 IS NOT NULL AND staged_payload_bytes IS NOT NULL AND staged_job_count IS NOT NULL AND staged_at IS NOT NULL)", name="chk_firecrawl_ops_staged_metadata"),
        CheckConstraint("(staged_payload_bytes IS NULL OR staged_payload_bytes BETWEEN 0 AND 1048576) AND (staged_job_count IS NULL OR staged_job_count BETWEEN 0 AND 100)", name="chk_firecrawl_ops_staged_bounds"),
        CheckConstraint(
            "status IN ('success', 'succeeded', 'failed', 'denied', 'payment_required', 'reserved', 'in_flight', 'unknown_after_crash')",
            name="chk_firecrawl_ops_status",
        ),
        CheckConstraint(
            "result_state = 'none' OR (reservation_id IS NOT NULL AND operation_key IS NOT NULL)",
            name="chk_firecrawl_ops_staged_reservation",
        ),
        UniqueConstraint("operation_key", name="uq_firecrawl_ops_operation_key"),
        ForeignKeyConstraint(
            ["reservation_id", "operation_key"],
            ["provider_reservations.id", "provider_reservations.operation_key"],
            name="fk_firecrawl_operation_reservation_identity",
            ondelete="RESTRICT",
        ),
        Index("idx_firecrawl_ops_created_status", "created_at", "status"),
        Index("idx_firecrawl_ops_order", text("created_at DESC"), text("id DESC")),
    )
