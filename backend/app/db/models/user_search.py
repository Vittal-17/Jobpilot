from datetime import datetime, timezone
from sqlalchemy import String, Boolean, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

class UserSearch(Base):
    __tablename__ = "user_searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    query: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remote_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    user: Mapped["User"] = relationship(back_populates="searches")

    __table_args__ = (
        # Ensure at least query or location is provided
        CheckConstraint("query IS NOT NULL OR location IS NOT NULL", name="chk_user_searches_not_empty"),
        CheckConstraint("query IS NULL OR length(trim(query)) > 0", name="chk_user_searches_query_not_empty"),
        CheckConstraint("location IS NULL OR length(trim(location)) > 0", name="chk_user_searches_location_not_empty"),
    )
