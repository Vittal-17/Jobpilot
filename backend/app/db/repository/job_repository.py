from sqlalchemy.orm import Session
from app.models.job import Job as PydanticJob
from app.db.models.job import JobModel

def save_job(db: Session, job: PydanticJob) -> JobModel:
    db_job = JobModel(
        title=job.title,
        company=job.company,
        source=job.source,
        source_job_id=job.source_job_id,
        discovered_at=job.discovered_at,
        location=job.location,
        remote=job.remote,
        employment_type=job.employment_type,
        description=job.description,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        currency=job.currency,
        url=str(job.url) if job.url else None,
        published_at=job.published_at,
        match_score=job.match_score,
    )
    db.add(db_job)
    db.flush()
    return db_job

def get_job_by_source_id(db: Session, source: str, source_job_id: str) -> JobModel | None:
    return db.query(JobModel).filter(JobModel.source == source, JobModel.source_job_id == source_job_id).first()
