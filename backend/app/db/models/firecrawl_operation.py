from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, CheckConstraint, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class FirecrawlOperationModel(Base):
    __tablename__ = "firecrawl_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    cost_units: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint("operation IN ('discovery', 'enrichment')", name="chk_firecrawl_ops_operation"),
        CheckConstraint("cost_units >= 0", name="chk_firecrawl_ops_cost"),
        CheckConstraint("status IN ('success', 'failed', 'denied', 'payment_required')", name="chk_firecrawl_ops_status"),
        Index("idx_firecrawl_ops_created_status", "created_at", "status"),
        Index("idx_firecrawl_ops_order", text("created_at DESC"), text("id DESC")),
    )
