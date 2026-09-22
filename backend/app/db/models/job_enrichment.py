from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, Text, CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base

class JobEnrichmentModel(Base):
    __tablename__ = "job_enrichments"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default='pending')
    url: Mapped[str] = mapped_column(Text, nullable=False)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0')
    lease_holder: Mapped[str | None] = mapped_column(String(255))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    error_reason: Mapped[str | None] = mapped_column(Text)
    result_telemetry: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'in_progress', 'success', 'failure', 'unsupported', 'retry')", name="chk_job_enrichments_status"),
    )
