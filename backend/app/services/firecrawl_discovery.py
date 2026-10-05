import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.user_search import UserSearch
from app.db.models.user_profile import UserProfile
from app.db.models.search_execution import SearchExecutionModel
from app.domain.taxonomy import get_authoritative_taxonomy
from app.providers.registry import create_provider
from app.providers.types import ProviderName
from app.providers.exceptions import ProviderError, ProviderConfigurationError
from app.schemas.job_search import JobSearchQuery, IngestionResult
from app.services.firecrawl_quota import calculate_search_credits, get_firecrawl_budget_state, record_firecrawl_operation
from app.services.ingestion import (
    run_ingestion,
    RateLimitExceeded,
    DatabaseUnavailable,
)

logger = logging.getLogger(__name__)

FRESHER_INDICATORS = ("fresher", "freshers", "entry level", "junior", "jr", "graduate", "trainee", "intern")


class DiscoveryQuery(BaseModel):
    keywords: str = Field(..., min_length=1, max_length=100)
    location: str = Field(..., min_length=1, max_length=100)
    page_size: int = Field(10, ge=1, le=100)
    estimated_credits: int = Field(2, ge=1)


class IngestionStats(BaseModel):
    created: int = 0
    duplicates: int = 0
    invalid: int = 0
    failed: int = 0


class FirecrawlDiscoveryPlan(BaseModel):
    enabled: bool = True
    max_queries: int = 2
    max_results_per_query: int = 10
    max_credits_per_run: int = 20
    queries: list[DiscoveryQuery] = Field(default_factory=list)
    reason: str | None = None


class FirecrawlDiscoveryTelemetry(BaseModel):
    run_id: str | None = None
    status: str  # "completed", "quota_stopped", "disabled", "failed"
    query_count: int = 0
    attempted_queries: int = 0
    successful_queries: int = 0
    candidates_found: int = 0
    candidates_accepted: int = 0
    candidates_rejected: int = 0
    validated_individual: int = 0
    rejections_by_reason: dict[str, int] = Field(default_factory=dict)
    ingestion: IngestionStats = Field(default_factory=IngestionStats)
    credits_consumed: int = 0  # Authoritative credits reserved in provider_usage
    credits_reserved: int = 0  # Explicit alias for internal quota reservation
    estimated_credits: int = 0  # Authoritative planned credit cost
    reason: str | None = None
    execution_ids: list[int] = Field(default_factory=list)


def _format_discovery_keywords(raw_role: str) -> str:
    clean = raw_role.strip()
    lower = clean.lower()
    if not any(tok in lower for tok in FRESHER_INDICATORS):
        return f"{clean} fresher"
    return clean


def plan_discovery_queries(db: Session, force: bool = False) -> FirecrawlDiscoveryPlan:
    enabled = bool(getattr(settings, "firecrawl_discovery_enabled", False)) or force
    max_queries = getattr(settings, "firecrawl_discovery_max_queries_per_run", 2)
    max_results = getattr(settings, "firecrawl_discovery_max_results_per_query", 10)
    max_credits = getattr(settings, "firecrawl_discovery_max_credits_per_run", 20)

    if not enabled:
        return FirecrawlDiscoveryPlan(
            enabled=False,
            max_queries=max_queries,
            max_results_per_query=max_results,
            max_credits_per_run=max_credits,
            queries=[],
            reason="Firecrawl discovery is disabled",
        )

    budget_state = get_firecrawl_budget_state(db)
    if budget_state["is_exhausted"] or budget_state["firecrawl_monthly_remaining"] < 2:
        return FirecrawlDiscoveryPlan(
            enabled=True,
            max_queries=max_queries,
            max_results_per_query=max_results,
            max_credits_per_run=max_credits,
            queries=[],
            reason="Firecrawl monthly automation budget exhausted",
        )

    candidates: list[tuple[str, str]] = []

    # 1. Active User Searches
    user_searches = db.query(UserSearch).filter(UserSearch.enabled == True).all()
    for us in user_searches:
        kw = (us.query or "").strip()
        loc = (us.location or "").strip() or "Bengaluru"
        if kw:
            candidates.append((_format_discovery_keywords(kw), loc))

    # 2. User Profile preferences
    if len(candidates) < max_queries:
        profiles = db.query(UserProfile).all()
        for p in profiles:
            if p.preferred_roles:
                roles = [r.strip() for r in p.preferred_roles.split(",") if r.strip()]
                loc = (p.preferred_locations or "Bengaluru").split(",")[0].strip() or "Bengaluru"
                for r in roles:
                    candidates.append((_format_discovery_keywords(r), loc))

    # 3. Authoritative Taxonomy fallback
    if len(candidates) < max_queries:
        taxonomy = get_authoritative_taxonomy()
        default_loc = taxonomy.locations[0].canonical if taxonomy.locations else "Bengaluru"
        for role in taxonomy.roles:
            candidates.append((_format_discovery_keywords(role.canonical), default_loc))

    # Deduplicate and apply per-run limits
    seen = set()
    bounded_queries: list[DiscoveryQuery] = []
    total_estimated_credits = 0

    credits_per_query = calculate_search_credits(max_results)

    for kw, loc in candidates:
        key = (kw.lower(), loc.lower())
        if key in seen:
            continue
        seen.add(key)

        if len(bounded_queries) >= max_queries:
            break
        if total_estimated_credits + credits_per_query > max_credits:
            break

        bounded_queries.append(
            DiscoveryQuery(
                keywords=kw,
                location=loc,
                page_size=max_results,
                estimated_credits=credits_per_query,
            )
        )
        total_estimated_credits += credits_per_query

    return FirecrawlDiscoveryPlan(
        enabled=True,
        max_queries=max_queries,
        max_results_per_query=max_results,
        max_credits_per_run=max_credits,
        queries=bounded_queries,
        reason="Plan generated successfully",
    )


def execute_discovery_run(
    db: Session,
    plan: FirecrawlDiscoveryPlan | None = None,
    force: bool = False,
    run_id: str | None = None,
) -> FirecrawlDiscoveryTelemetry:
    max_queries = getattr(settings, "firecrawl_discovery_max_queries_per_run", 2)
    max_results = getattr(settings, "firecrawl_discovery_max_results_per_query", 10)
    max_credits = getattr(settings, "firecrawl_discovery_max_credits_per_run", 20)

    if run_id is None:
        run_id = f"fc_run_{uuid.uuid4().hex[:16]}"

    if plan is None:
        plan = plan_discovery_queries(db, force=force)

    # Server-authoritative enabled check
    is_enabled = bool(getattr(settings, "firecrawl_discovery_enabled", False)) or force
    if not is_enabled:
        return FirecrawlDiscoveryTelemetry(
            run_id=run_id,
            status="disabled",
            query_count=0,
            attempted_queries=0,
            successful_queries=0,
            candidates_found=0,
            candidates_accepted=0,
            candidates_rejected=0,
            ingestion=IngestionStats(),
            credits_consumed=0,
            credits_reserved=0,
            estimated_credits=0,
            reason="Firecrawl discovery is disabled",
        )

    if not plan.queries:
        return FirecrawlDiscoveryTelemetry(
            run_id=run_id,
            status="completed",
            query_count=0,
            attempted_queries=0,
            successful_queries=0,
            candidates_found=0,
            candidates_accepted=0,
            candidates_rejected=0,
            ingestion=IngestionStats(),
            credits_consumed=0,
            credits_reserved=0,
            estimated_credits=0,
            reason=plan.reason or "No discovery queries to execute",
        )

    # Server-authoritative query limits and sanitization:
    # 1. Bounded to backend max_queries (never trust submitted plan.queries length)
    queries_to_execute = plan.queries[:max_queries]

    # 2. Server-authoritatively calculate estimated cost per query and cap to max_credits_per_run
    validated_queries: list[tuple[DiscoveryQuery, int, int]] = []
    total_estimated_credits = 0

    for q in queries_to_execute:
        # Normalize page_size strictly to backend max_results (default 10)
        norm_page_size = min(max(1, q.page_size), max_results)
        # Authoritative cost calculation (ignoring any client-supplied estimated_credits)
        cost_units = calculate_search_credits(norm_page_size)

        if total_estimated_credits + cost_units > max_credits:
            logger.info(
                "Planned query %s exceeds per-run credit limit (%s credits); stopping planning",
                q.keywords, max_credits
            )
            break

        validated_queries.append((q, norm_page_size, cost_units))
        total_estimated_credits += cost_units

    telemetry = FirecrawlDiscoveryTelemetry(
        run_id=run_id,
        status="completed",
        query_count=len(validated_queries),
        attempted_queries=0,
        successful_queries=0,
        candidates_found=0,
        candidates_accepted=0,
        candidates_rejected=0,
        ingestion=IngestionStats(),
        credits_consumed=0,
        credits_reserved=0,
        estimated_credits=total_estimated_credits,
    )

    if not validated_queries:
        telemetry.reason = "No queries within per-run credit cap"
        return telemetry

    try:
        provider = create_provider(ProviderName.FIRECRAWL)
    except ProviderConfigurationError as e:
        logger.error("Firecrawl provider configuration error: %s", e)
        telemetry.status = "failed"
        telemetry.reason = "Provider configuration error"
        return telemetry
    except Exception as e:
        logger.exception("Failed to instantiate FirecrawlProvider: %s", e)
        telemetry.status = "failed"
        telemetry.reason = "Failed to instantiate provider"
        return telemetry

    for q, norm_page_size, cost_units in validated_queries:
        if telemetry.credits_reserved + cost_units > max_credits:
            logger.info("Discovery run reached per-run credit limit (%s credits)", max_credits)
            telemetry.reason = f"Per-run credit limit of {max_credits} reached"
            break

        telemetry.attempted_queries += 1
        query = JobSearchQuery(
            keywords=q.keywords,
            location=q.location,
            page=1,
            page_size=norm_page_size,
        )

        now_dt = datetime.now(timezone.utc)
        query_hash = hashlib.sha256(f"{q.keywords}::{q.location}".encode("utf-8")).hexdigest()[:8]
        candidate_id = f"fc_disc_{run_id}_{telemetry.attempted_queries}_{query_hash}"
        execution = SearchExecutionModel(
            candidate_id=candidate_id,
            cycle_id=run_id,
            status="selected",
            provider_name="firecrawl",
            query_variant=q.keywords,
            retrieval_location=q.location,
            selected_at=now_dt,
        )
        db.add(execution)
        db.commit()

        try:
            result, job_ids = run_ingestion(db, "firecrawl", provider, query, execution_id=execution.id)
            telemetry.execution_ids.append(execution.id)
            # If run_ingestion didn't raise RateLimitExceeded or DatabaseUnavailable,
            # acquire_provider_request_slot() successfully reserved cost_units in provider_usage!
            telemetry.credits_consumed += cost_units
            telemetry.credits_reserved += cost_units

            if result.failed == 0:
                record_firecrawl_operation(operation="discovery", cost_units=cost_units, status="success")
                telemetry.successful_queries += 1
                search_telem = getattr(provider, "last_search_telemetry", {})
                if isinstance(search_telem, dict) and search_telem:
                    telemetry.candidates_found += search_telem.get("candidates_found", result.fetched)
                    telemetry.candidates_accepted += result.created
                    prov_rejected = search_telem.get("candidates_rejected", 0)
                    telemetry.candidates_rejected += prov_rejected + result.duplicates + result.invalid
                    telemetry.validated_individual += search_telem.get("candidates_accepted", result.fetched)
                    rejections_dict = search_telem.get("rejections_by_reason")
                    if isinstance(rejections_dict, dict):
                        for r_reason, r_cnt in rejections_dict.items():
                            telemetry.rejections_by_reason[r_reason] = telemetry.rejections_by_reason.get(r_reason, 0) + r_cnt

                        if rejections_dict:
                            db.execute(text("""
                                UPDATE search_execution
                                SET error_message = :rejection_json
                                WHERE id = :eid
                            """), {
                                "eid": execution.id,
                                "rejection_json": json.dumps({"rejections_by_reason": rejections_dict})
                            })
                            db.commit()
                else:
                    telemetry.candidates_found += result.fetched
                    telemetry.candidates_accepted += result.created
                    telemetry.candidates_rejected += (result.duplicates + result.invalid)
                    telemetry.validated_individual += result.fetched

                if result.duplicates > 0:
                    telemetry.rejections_by_reason["duplicate_in_db"] = telemetry.rejections_by_reason.get("duplicate_in_db", 0) + result.duplicates
                if result.invalid > 0:
                    telemetry.rejections_by_reason["invalid_persistence"] = telemetry.rejections_by_reason.get("invalid_persistence", 0) + result.invalid

                telemetry.ingestion.created += result.created
                telemetry.ingestion.duplicates += result.duplicates
                telemetry.ingestion.invalid += result.invalid
            else:
                record_firecrawl_operation(operation="discovery", cost_units=cost_units, status="failed")
                telemetry.ingestion.failed += result.failed
                logger.warning("Discovery query failed for %s: %s", q.keywords, result.failed)
        except RateLimitExceeded:
            logger.warning("Firecrawl monthly quota limit reached during discovery run")
            telemetry.status = "quota_stopped"
            telemetry.reason = "Monthly quota cap reached"
            # Quota was denied, so ZERO credits were reserved for this request
            break
        except DatabaseUnavailable:
            logger.error("Database unavailable during discovery query %s", q.keywords)
            telemetry.status = "failed"
            telemetry.reason = "Database unavailable"
            break
        except Exception as e:
            logger.warning("Firecrawl discovery query error for %s: %s", q.keywords, e)
            telemetry.ingestion.failed += 1

    if telemetry.status != "quota_stopped" and telemetry.status != "failed":
        telemetry.status = "completed"

    return telemetry
