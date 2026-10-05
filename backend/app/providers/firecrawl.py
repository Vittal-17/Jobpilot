import hashlib
import logging
import math
import re
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlparse

from app.core.config import settings
from app.models.job import Job
from app.providers.base import JobProvider
from app.providers.exceptions import (
    ProviderConfigurationError,
    ProviderError,
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderPayloadError,
    ProviderTimeout,
)
from app.schemas.job_search import JobSearchQuery
from app.services.firecrawl_quota import calculate_search_credits
from app.services.job_url_classifier import classify_job_url
from app.services.scraper.firecrawl import FirecrawlClient, FirecrawlError, FirecrawlPayloadError

logger = logging.getLogger(__name__)


NON_COMPANY_TOKENS = {
    # Workplace & work models
    "remote", "hybrid", "onsite", "on-site", "in-office", "wfh", "work from home",
    # Geography & locations
    "bengaluru", "bangalore", "mumbai", "hyderabad", "pune", "delhi", "new delhi",
    "noida", "gurgaon", "gurugram", "chennai", "kolkata", "ahmedabad", "chandigarh",
    "india", "karnataka", "maharashtra", "telangana", "tamil nadu", "usa", "us", "uk",
    "london", "singapore", "germany", "canada",
    # Employment types & seniority
    "full time", "full-time", "part time", "part-time", "contract", "contractor",
    "internship", "intern", "interns", "temporary", "freelance", "permanent",
    "fresher", "freshers", "entry level", "junior", "senior", "lead", "principal", "staff",
    # Status & calls to action
    "immediate", "urgent", "hiring", "careers", "jobs", "apply", "opening", "openings",
    "opportunity", "opportunities", "walk-in", "walkin",
    # Tech skills & frameworks
    "python", "java", "react", "javascript", "typescript", "fastapi", "django", "aws",
    "sql", "node", "golang", "c++", "c#", "flutter", "devops",
}

ROLE_INDICATOR_SUFFIXES = (
    "engineer", "developer", "programmer", "architect", "analyst", "manager",
    "lead", "specialist", "designer", "consultant", "intern", "scientist", "administrator",
)


def _is_valid_company_name(cand: str) -> bool:
    if not cand:
        return False
    cand_clean = cand.strip()
    if len(cand_clean) < 2 or len(cand_clean) > 60:
        return False
    cand_lower = cand_clean.lower()
    if cand_lower in NON_COMPANY_TOKENS:
        return False
    # Reject tech skill strings
    tokens = re.split(r'[\s/+,]+', cand_lower)
    if all(tok in NON_COMPANY_TOKENS for tok in tokens if tok):
        return False
    # Reject salary / compensation indicators (e.g. 100k, 12 LPA, $120k)
    if re.search(r'(?:\$|\b\d+\s*(?:k|lpa|inr|usd)\b)', cand_lower):
        return False
    # Reject experience requirements (e.g. 0-1 years, 2+ yrs)
    if re.search(r'\b\d+\s*(?:-\s*\d+)?\s*(?:years?|yrs?)\b', cand_lower):
        return False
    return True


def _extract_company_from_title(title: str, domain: str | None = None) -> tuple[str, str]:
    """
    Extract clean title and company name with high-confidence fail-closed heuristics.
    Preserves company as 'Unknown' when confidence is insufficient to prevent corrupting canonical identity.
    """
    clean_title = title.strip()
    if not clean_title:
        return "", "Unknown"

    # 1. High-confidence prepositional markers: " at " or " @ "
    for prep in (" at ", " @ "):
        if prep in clean_title:
            parts = clean_title.rsplit(prep, 1)
            left = parts[0].strip()
            right = parts[1].strip()
            # Strip trailing delimiters if present (e.g. "Acme Corp - Bengaluru" or "Acme Corp | Remote")
            candidate = re.split(r'\s*[-|–—]\s*', right)[0].strip()
            if _is_valid_company_name(candidate):
                return left, candidate
            # If candidate was invalid (e.g. location/type), still keep clean role title
            return left, "Unknown"

    # 2. Delimiters: " | ", " - ", " – ", " — "
    for sep in (" | ", " - ", " – ", " — "):
        if sep in clean_title:
            parts = clean_title.split(sep)
            if len(parts) >= 2:
                cand_right = parts[-1].strip()
                cand_left = parts[0].strip()
                cand_right_lower = cand_right.lower()
                cand_left_lower = cand_left.lower()

                # Case A: "Title - Company" (right is valid company, does not look like a job role)
                if _is_valid_company_name(cand_right) and not any(cand_right_lower.endswith(sfx) for sfx in ROLE_INDICATOR_SUFFIXES):
                    clean_role = sep.join(parts[:-1]).strip()
                    return clean_role, cand_right

                # Case B: "Company - Title" (left is valid company, right ends in job role)
                if any(cand_right_lower.endswith(sfx) for sfx in ROLE_INDICATOR_SUFFIXES) and _is_valid_company_name(cand_left) and not any(cand_left_lower.endswith(sfx) for sfx in ROLE_INDICATOR_SUFFIXES):
                    clean_role = sep.join(parts[1:]).strip()
                    return clean_role, cand_left

    # Fail closed: never guess from arbitrary domains or ambiguous titles
    return clean_title, "Unknown"


def formulate_individual_job_query(keywords: str, location: str | None = None) -> str:
    """
    Formulate a search query targeted at individual job vacancies and postings
    rather than high-level aggregator or listing directory pages.
    """
    kw = (keywords or "").strip()
    loc = (location or "").strip()
    if not kw and not loc:
        return ""

    parts = []
    if kw:
        parts.append(kw)
    if loc:
        parts.append(loc)

    lower = f"{kw} {loc}".lower()
    if not any(token in lower for token in ("apply", "opening", "vacancy", "vacancies", "job opening")):
        parts.append('"apply"')

    return " ".join(parts).strip()


class FirecrawlProvider(JobProvider):
    def __init__(self, client: FirecrawlClient | None = None):
        self.api_key = settings.firecrawl_api_key
        self.client = client or FirecrawlClient(self.api_key or "")
        self.last_search_telemetry: dict = {
            "candidates_found": 0,
            "candidates_accepted": 0,
            "candidates_rejected": 0,
            "rejections_by_reason": {},
        }

    def validate_config(self):
        if not getattr(settings, "firecrawl_enabled", True):
            raise ProviderConfigurationError("Firecrawl provider is disabled")
        if not self.api_key or not self.api_key.strip():
            logger.warning("Firecrawl API key missing")
            raise ProviderConfigurationError("Firecrawl API key missing")

    def search_jobs(self, query: JobSearchQuery) -> List[Job]:
        keywords = (query.keywords or "").strip()
        location = (query.location or "").strip()
        search_query_str = formulate_individual_job_query(keywords, location)

        if not search_query_str:
            self.last_search_telemetry = {
                "candidates_found": 0,
                "candidates_accepted": 0,
                "candidates_rejected": 0,
                "rejections_by_reason": {},
            }
            return []

        limit = getattr(query, "page_size", 10) or 10
        freshness = getattr(settings, "firecrawl_discovery_freshness", "qdr:m")
        country = getattr(settings, "firecrawl_discovery_country", "in")

        try:
            raw_results = self.client.search(
                query=search_query_str,
                limit=limit,
                tbs=freshness,
                country=country,
                location=location or None,
            )
        except FirecrawlPayloadError as e:
            logger.error(f"Firecrawl payload error: {e}")
            raise ProviderPayloadError(f"Firecrawl payload error: {e}") from e
        except FirecrawlError as e:
            if e.status_code == 408:
                logger.error("Firecrawl connection error: Timeout")
                raise ProviderTimeout("Firecrawl connection error: Timeout") from e
            elif e.status_code is not None and e.status_code >= 400:
                logger.error(f"Firecrawl HTTP error: {e.status_code}")
                raise ProviderHTTPError(f"Firecrawl HTTP error: {e.status_code}") from e
            else:
                logger.error(f"Firecrawl connection error: {e}")
                raise ProviderNetworkError(f"Firecrawl connection error: {e}") from e
        except Exception as e:
            logger.error(f"Firecrawl unexpected error: {type(e).__name__}")
            raise ProviderError(f"Firecrawl unexpected error: {e}") from e

        if not isinstance(raw_results, list):
            raise ProviderPayloadError("FirecrawlProviderSchemaError: Search results is not a list")

        jobs: List[Job] = []
        now_utc = datetime.now(timezone.utc)
        rejections: dict[str, int] = {}
        candidates_found = len(raw_results)
        candidates_rejected = 0

        for item in raw_results:
            if not isinstance(item, dict):
                logger.warning("FirecrawlProviderSchemaError: result item is not a dictionary")
                candidates_rejected += 1
                rejections["invalid_schema"] = rejections.get("invalid_schema", 0) + 1
                continue

            raw_url = item.get("url")
            if not raw_url or not isinstance(raw_url, str) or not raw_url.strip():
                logger.warning("Firecrawl result missing URL, skipping")
                candidates_rejected += 1
                rejections["missing_url"] = rejections.get("missing_url", 0) + 1
                continue

            raw_url = raw_url.strip()
            parsed_url = urlparse(raw_url)
            domain = parsed_url.netloc.lower() if parsed_url.netloc else None

            raw_title = item.get("title")
            if not raw_title or not isinstance(raw_title, str) or not raw_title.strip():
                logger.warning("Firecrawl result missing title, skipping")
                candidates_rejected += 1
                rejections["missing_title"] = rejections.get("missing_title", 0) + 1
                continue

            # Layer 2: Filter non-individual job postings (search/category/aggregators)
            is_valid, reject_reason = classify_job_url(raw_url, raw_title)
            if not is_valid:
                logger.info("Firecrawl search dropped non-individual URL: %s (reason: %s)", raw_url, reject_reason)
                candidates_rejected += 1
                rejections[reject_reason] = rejections.get(reject_reason, 0) + 1
                continue

            clean_title, company = _extract_company_from_title(raw_title, domain)
            if not clean_title:
                clean_title = raw_title.strip()

            explicit_id = item.get("id") or item.get("job_id")
            if explicit_id and str(explicit_id).strip():
                source_job_id = str(explicit_id).strip()
            else:
                url_hash = hashlib.sha256(raw_url.encode("utf-8")).hexdigest()[:16]
                source_job_id = f"fc_{url_hash}"

            snippet = item.get("description") or item.get("snippet") or None
            if snippet and isinstance(snippet, str):
                snippet = snippet.strip() or None

            # Finding 7: Parse publication timestamp if available
            published_at = None
            raw_date = item.get("published_at") or item.get("date") or item.get("publishedDate") or item.get("published_time")
            if raw_date and isinstance(raw_date, str) and raw_date.strip():
                try:
                    dt = datetime.fromisoformat(raw_date.strip().replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    published_at = dt
                except Exception:
                    published_at = None

            job_location = location or None

            try:
                job = Job(
                    title=clean_title,
                    company=company,
                    source="firecrawl",
                    source_job_id=source_job_id,
                    discovered_at=now_utc,
                    location=job_location,
                    remote=getattr(query, "remote", None),
                    description=snippet,
                    description_is_snippet=True,
                    url=raw_url,
                    published_at=published_at,
                )
                jobs.append(job)
            except Exception as e:
                logger.warning(f"Firecrawl validation error for job {source_job_id}: {e}")
                candidates_rejected += 1
                rejections["validation_error"] = rejections.get("validation_error", 0) + 1

        self.last_search_telemetry = {
            "candidates_found": candidates_found,
            "candidates_accepted": len(jobs),
            "candidates_rejected": candidates_rejected,
            "rejections_by_reason": rejections,
        }

        return jobs
