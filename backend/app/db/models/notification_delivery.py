from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

class NotificationDeliveryModel(Base):
    """Durable single-owner delivery fence.

    ``send_started_at`` without ``notified_at`` is intentionally a durable
    unknown outcome.  It must not be reclaimed or automatically retried,
    because Telegram may have accepted a request before a worker lost its
    response.
    """
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index(
            "ix_notification_deliveries_unknown_at",
            "unknown_at",
            postgresql_where=text("unknown_at IS NOT NULL"),
        ),
    )

    delivery_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unknown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unknown_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def is_durable_unknown(self) -> bool:
        return (
            self.send_started_at is not None
            and self.notified_at is None
            and self.invalidated_at is None
        )

    def belongs_to_owner(self, owner_id: int) -> bool:
        return self.user_id == owner_id
