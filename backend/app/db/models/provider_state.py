from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class ProviderStateModel(Base):
    __tablename__ = "provider_state"

    provider_name: Mapped[str] = mapped_column(String(50), primary_key=True)
    lifetime_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
