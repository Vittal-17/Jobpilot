"""Shared execution ownership, matching and delivery-safe invalidation."""
from sqlalchemy import case, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from app.db.models.execution_outcome import ExecutionJobModel
from app.db.models.job import JobModel
from app.db.models.recommendation_history import RecommendationHistoryModel
from app.db.models.user import User
from app.db.models.user_search import UserSearch
from app.db.models.user_profile import UserProfile
from app.schemas.job import JobResponse
from app.schemas.match import RecommendationPreferences

def record_job_outcome(db: Session, execution_id: int | None, job_id: int, **values) -> None:
    if execution_id is None: return
    allowed={"ingestion_outcome","description_is_snippet","enrichment_status","validation","rejection_reason","fresher_eligible"}
    if values.keys()-allowed: raise ValueError("Unknown execution outcome field")
    stmt=insert(ExecutionJobModel).values(execution_id=execution_id,job_id=job_id,**values)
    updates={k:v for k,v in values.items()}; updates["updated_at"]=func.clock_timestamp()
    if "ingestion_outcome" in values:
        updates["ingestion_outcome"]=case((ExecutionJobModel.ingestion_outcome=="created","created"),else_=stmt.excluded.ingestion_outcome)
    db.execute(stmt.on_conflict_do_update(index_elements=[ExecutionJobModel.execution_id,ExecutionJobModel.job_id],set_=updates))

def claim_execution_eligibility(db: Session, execution_id: int | None, job_id: int) -> int:
    """Claim the per-execution/job counter once, in the caller's transaction."""
    if execution_id is None:
        return 1
    record_job_outcome(db, execution_id, job_id, fresher_eligible=True)
    return int(db.execute(text("""
        UPDATE search_execution_jobs
        SET eligibility_counted=true, updated_at=clock_timestamp()
        WHERE execution_id=:eid AND job_id=:jid AND eligibility_counted=false
        RETURNING job_id
    """), {"eid": execution_id, "jid": job_id}).fetchone() is not None)


def increment_execution_quality(db: Session, execution_id: int, eligible: int, recommendations: int) -> None:
    # Recommendation insert accounting is independent of eligibility replay.
    db.execute(text("""
        UPDATE search_execution
        SET jobs_fresher_eligible=COALESCE(jobs_fresher_eligible, 0)+:elig,
            recommendations_created=COALESCE(recommendations_created, 0)+:recs
        WHERE id=:eid AND status NOT IN ('failed', 'abandoned')
    """), {"eid": execution_id, "elig": eligible, "recs": recommendations})

def lock_notification_payloads(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('notifications'), hashtext('payload'))"))

def invalidate_unsent_recommendations(db: Session, job_id: int) -> None:
    lock_notification_payloads(db)
    rows=db.execute(text("""
        SELECT delivery_id,send_started_at,notified_at FROM notification_deliveries
        WHERE delivery_id IN (SELECT delivery_id FROM recommendation_history WHERE job_id=:jid AND delivery_id IS NOT NULL)
        ORDER BY delivery_id FOR UPDATE
    """),{"jid":job_id}).mappings().all()
    for row in rows:
        if row["send_started_at"] is not None or row["notified_at"] is not None: continue
        db.execute(text("UPDATE notification_deliveries SET invalidated_at=clock_timestamp() WHERE delivery_id=:did AND send_started_at IS NULL AND notified_at IS NULL"),{"did":row["delivery_id"]})
        db.execute(text("UPDATE recommendation_history SET delivery_id=NULL WHERE delivery_id=:did"),{"did":row["delivery_id"]})
    db.execute(text("DELETE FROM recommendation_history WHERE job_id=:jid AND delivery_id IS NULL"),{"jid":job_id})

def evaluate_jobs(db: Session, job_ids: list[int], execution_id: int | None, allow_snippets=False, limit=5):
    if not job_ids: return 0,0
    from app.services.eligibility import is_fresher_eligible
    from app.services.matching_service import calculate_match
    from app.services.location_policy import posting_matches_retrieval_location
    from app.db.models.search_execution import SearchExecutionModel
    db.flush()
    retrieval=db.query(SearchExecutionModel.retrieval_location).filter_by(id=execution_id).scalar() if execution_id else None
    jobs=db.query(JobModel).filter(JobModel.id.in_(set(job_ids))).order_by(JobModel.id).with_for_update().populate_existing().all()
    eligible_jobs=[]; newly=0
    for job in jobs:
        usable=not job.requires_refresh and job.validation_status not in ("rejected_listing","refresh_required") and (allow_snippets or not job.description_is_snippet)
        eligible=usable and posting_matches_retrieval_location(job.location,job.remote,retrieval) and is_fresher_eligible(job.title,job.description or "",is_snippet=job.description_is_snippet)
        record_job_outcome(db,execution_id,job.id,fresher_eligible=bool(eligible),description_is_snippet=job.description_is_snippet)
        if not eligible:
            # A mismatch in this execution must not invalidate another run.
            continue
        newly += claim_execution_eligibility(db, execution_id, job.id)
        eligible_jobs.append(job)
    users=db.query(UserSearch.user_id).join(User,User.id==UserSearch.user_id).filter(UserSearch.enabled.is_(True),User.is_active.is_(True)).distinct().all()
    created=0
    for (uid,) in users:
        profile=db.query(UserProfile).filter_by(user_id=uid).first()
        prefs=RecommendationPreferences(**{f:getattr(profile,f,None) if profile else None for f in ("preferred_roles","skills","preferred_locations","remote_preference","experience_years")})
        existing=set(db.execute(select(RecommendationHistoryModel.job_id).where(RecommendationHistoryModel.user_id==uid,RecommendationHistoryModel.job_id.in_(job_ids))).scalars())
        scored=sorted(((j,calculate_match(JobResponse.model_validate(j),prefs)) for j in eligible_jobs if j.id not in existing),key=lambda x:(-x[1].score,x[0].id))
        for job,match in scored[:limit]:
            if match.score < 50:
                continue
            row=db.execute(insert(RecommendationHistoryModel).values(user_id=uid,job_id=job.id,score=match.score,reasons=[r.model_dump() for r in match.reasons],source_execution_id=execution_id).on_conflict_do_nothing(index_elements=["user_id","job_id"]).returning(RecommendationHistoryModel.id)).scalar()
            created+=int(row is not None)
    return newly,created
