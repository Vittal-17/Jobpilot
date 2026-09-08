from datetime import date
from sqlalchemy import String, Integer, Date, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

class ProviderUsageModel(Base):
    __tablename__ = "provider_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("provider_name", "usage_date", name="uq_provider_date"),
    )
