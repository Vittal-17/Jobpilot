from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models.job import Job as PydanticJob
from app.db.models.job import JobModel
from app.db.models.job_source import JobSourceModel
from app.db.models.job_enrichment import JobEnrichmentModel
import hashlib
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def normalize_url(url_str: str | None) -> str | None:
    if not url_str:
        return None
    lower_url = url_str.lower()
    if "adzuna.com" in lower_url or "jooble.org" in lower_url:
        return None
    try:
        parsed = urlparse(url_str)
        if not parsed.scheme or not parsed.netloc:
            return None
        q_params = parse_qsl(parsed.query, keep_blank_values=True)
        filtered_params = [
            (k, v) for k, v in q_params
            if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid', 'msclkid', 'mc_eid')
        ]
        filtered_params.sort(key=lambda x: (x[0], x[1]))
        new_query = urlencode(filtered_params)
        clean_url = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip('/') if parsed.path != '/' else '/',
            parsed.params,
            new_query,
            ''
        ))
        return clean_url
    except Exception:
        return None

def compute_canonical_hash(job: PydanticJob) -> str | None:
    url_norm = normalize_url(str(job.url) if job.url else None)
    if url_norm:
        return hashlib.sha256(f"URL|{url_norm}".encode('utf-8')).hexdigest()

    company_norm = normalize_text(job.company)
    title_norm = normalize_text(job.title)
    loc_norm = normalize_text(job.location)

    if len(company_norm) >= 2 and len(title_norm) >= 2 and len(loc_norm) >= 2:
        identity_str = f"ETL|{company_norm}|{title_norm}|{loc_norm}"
        return hashlib.sha256(identity_str.encode('utf-8')).hexdigest()

    return None

def _add_job_source(db: Session, job_id: int, source: str, source_job_id: str, url: str | None):
    # Check if source exists to avoid UniqueConstraint error
    exists = db.query(JobSourceModel).filter(
        JobSourceModel.source == source,
        JobSourceModel.source_job_id == source_job_id
    ).first()
    if not exists:
        js = JobSourceModel(
            job_id=job_id,
            source=source,
            source_job_id=source_job_id,
            url=url
        )
        db.add(js)
        db.flush()

def enqueue_job_enrichment(db: Session, job_id: int, url: str, execution_id: int | None = None) -> None:
    stmt = pg_insert(JobEnrichmentModel).values(
        job_id=job_id,
        status='pending',
        url=url,
        source_execution_id=execution_id
    ).on_conflict_do_nothing(index_elements=['job_id'])
    db.execute(stmt)
    db.flush()

def save_job(db: Session, job: PydanticJob, execution_id: int | None = None) -> tuple[JobModel, bool]:
    # 1. Provider-ID precedence first
    existing_source = db.query(JobSourceModel).filter(JobSourceModel.source == job.source, JobSourceModel.source_job_id == job.source_job_id).first()
    if existing_source:
        existing = db.query(JobModel).filter(JobModel.id == existing_source.job_id).first()
        if existing:
            _add_job_source(db, existing.id, job.source, job.source_job_id, str(job.url) if job.url else None)
            return existing, False

    canonical_hash = compute_canonical_hash(job)

    # 2. Canonical hash deduplication
    if canonical_hash is not None:
        existing = db.query(JobModel).filter(JobModel.canonical_hash == canonical_hash).first()
        if existing:
            _add_job_source(db, existing.id, job.source, job.source_job_id, str(job.url) if job.url else None)
            return existing, False

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
        canonical_hash=canonical_hash,
        description_is_snippet=job.description_is_snippet
    )

    try:
        with db.begin_nested():
            db.add(db_job)
            db.flush()
            _add_job_source(db, db_job.id, job.source, job.source_job_id, str(job.url) if job.url else None)
            if job.description_is_snippet and job.url:
                enqueue_job_enrichment(db, db_job.id, str(job.url), execution_id)
    except IntegrityError as e:
        # Concurrent collision recovery
        if canonical_hash and "canonical_hash" in str(e.orig):
            existing = db.query(JobModel).filter(JobModel.canonical_hash == canonical_hash).first()
            if existing:
                _add_job_source(db, existing.id, job.source, job.source_job_id, str(job.url) if job.url else None)
                return existing, False

        # If it was a source identity collision
        existing_source = db.query(JobSourceModel).filter(JobSourceModel.source == job.source, JobSourceModel.source_job_id == job.source_job_id).first()
        if existing_source:
            existing = db.query(JobModel).filter(JobModel.id == existing_source.job_id).first()
            if existing:
                _add_job_source(db, existing.id, job.source, job.source_job_id, str(job.url) if job.url else None)
                return existing, False

        raise

    return db_job, True

def get_job_by_source_id(db: Session, source: str, source_job_id: str) -> JobModel | None:
    return db.query(JobModel).filter(JobModel.source == source, JobModel.source_job_id == source_job_id).first()
