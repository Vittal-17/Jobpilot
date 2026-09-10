from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class SearchExecutionModel(Base):
    __tablename__ = "search_execution"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False) # 'selected', 'started', 'succeeded', 'failed'
    provider_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    jobs_fetched: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    jobs_created: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    jobs_duplicates: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    jobs_invalid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("idx_search_execution_candidate", "candidate_id"),
        Index("idx_search_execution_status", "status"),
        Index(
            "uq_active_claim",
            "candidate_id",
            unique=True,
            postgresql_where=status.in_(["selected", "started"]),
        ),
        CheckConstraint(status.in_(['selected', 'started', 'succeeded', 'failed']), name='chk_status_valid')
    )
