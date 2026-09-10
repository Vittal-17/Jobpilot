import httpx
import logging
from datetime import datetime, timezone
from typing import List

from pydantic import ValidationError
from app.providers.base import JobProvider
from app.models.job import Job
from app.schemas.job_search import JobSearchQuery
from app.core.config import settings

logger = logging.getLogger(__name__)

class JoobleProvider(JobProvider):
    def __init__(self):
        self.api_key = settings.jooble_api_key
        self.url = f"https://jooble.org/api/{self.api_key}"

    def validate_config(self):
        if not self.api_key:
            logger.warning("Jooble API key missing")
            from app.providers.exceptions import ProviderConfigurationError
            raise ProviderConfigurationError("Jooble API key missing")

    def search_jobs(self, query: JobSearchQuery) -> List[Job]:

        # Note: Jooble API does not natively support an arbitrary 'page_size' (result count per page) parameter.
        # It handles pagination inherently via the 'page' parameter with its own fixed result count.
        payload = {
            "keywords": query.keywords,
            "location": query.location,
            "page": query.page
        }
        if query.radius_km is not None:
            payload["radius"] = query.radius_km

        try:
            headers = {"User-Agent": "JobPilot/1.0", "Content-Type": "application/json"}
            with httpx.Client(timeout=10.0, headers=headers) as client:
                response = client.post(self.url, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Jooble HTTP error: {e.response.status_code}")
            from app.providers.exceptions import ProviderHTTPError
            raise ProviderHTTPError(f"Jooble HTTP error: {e.response.status_code}")
        except httpx.TimeoutException:
            logger.error("Jooble connection error: Timeout")
            from app.providers.exceptions import ProviderTimeout
            raise ProviderTimeout("Jooble connection error: Timeout")
        except httpx.RequestError as e:
            # Note: Do not log `e.request.url` as it contains the api key
            logger.error("Jooble connection error: Request failed")
            from app.providers.exceptions import ProviderNetworkError
            raise ProviderNetworkError("Jooble connection error: Request failed")
        except Exception as e:
            logger.error(f"Jooble unexpected network error: {type(e).__name__}")
            from app.providers.exceptions import ProviderError
            raise ProviderError("Jooble unexpected error")

        if not isinstance(data, dict) or "jobs" not in data:
            from app.providers.exceptions import ProviderPayloadError
            raise ProviderPayloadError("JoobleProviderSchemaError: Missing or malformed 'jobs' key")

        results = data.get("jobs")
        if not isinstance(results, list):
            from app.providers.exceptions import ProviderPayloadError
            raise ProviderPayloadError("JoobleProviderSchemaError: 'jobs' is not a list")

        jobs = []
        for item in results:
            raw_id = "unknown"
            if not isinstance(item, dict):
                logger.warning("JoobleProviderSchemaError: 'item' is not a dictionary")
                continue
            try:
                # P1.4 Skip if no stable ID
                raw_id = item.get("id")
                if not raw_id:
                    logger.warning("Jooble validation error: missing stable ID")
                    continue

                pub_date_str = item.get("updated")
                pub_date = None
                if pub_date_str:
                    try:
                        pub_date = datetime.fromisoformat(pub_date_str)
                        if pub_date.tzinfo is None:
                            pub_date = pub_date.replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

                job = Job(
                    title=item.get("title", ""),
                    company=item.get("company", "Unknown"),
                    source="jooble",
                    source_job_id=str(raw_id),
                    discovered_at=datetime.now(timezone.utc),
                    location=item.get("location"),
                    description=item.get("snippet"),
                    employment_type=item.get("type"),
                    url=item.get("link"),
                    published_at=pub_date
                )
                jobs.append(job)
            except ValidationError as e:
                logger.warning(f"Jooble validation error for job {raw_id}")
            except Exception:
                logger.warning(f"Jooble unexpected parsing error for job {raw_id}")

        return jobs
