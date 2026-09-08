from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, DateTime, Text, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobModel(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    source_job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    location: Mapped[str | None] = mapped_column(String(255))
    remote: Mapped[bool | None] = mapped_column(Boolean)
    employment_type: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)

    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(10))

    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_score: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="uq_jobs_source_source_job_id"),
        CheckConstraint("match_score >= 0 AND match_score <= 100", name="chk_jobs_match_score"),
        CheckConstraint("salary_min >= 0", name="chk_jobs_salary_min"),
        CheckConstraint("salary_max >= 0", name="chk_jobs_salary_max"),
        CheckConstraint("salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max", name="chk_jobs_salary_range"),
        CheckConstraint("length(trim(title)) > 0", name="chk_jobs_title_not_empty"),
        CheckConstraint("length(trim(company)) > 0", name="chk_jobs_company_not_empty"),
        CheckConstraint("length(trim(source)) > 0", name="chk_jobs_source_not_empty"),
        CheckConstraint("length(trim(source_job_id)) > 0", name="chk_jobs_source_job_id_not_empty"),
    )
