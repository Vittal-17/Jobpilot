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

class AdzunaProvider(JobProvider):
    def __init__(self):
        self.app_id = settings.adzuna_app_id
        self.app_key = settings.adzuna_app_key
        self.base_url = "https://api.adzuna.com/v1/api/jobs/in/search"

    def validate_config(self):
        if not self.app_id or not self.app_key:
            logger.warning("Adzuna credentials missing")
            from app.providers.exceptions import ProviderConfigurationError
            raise ProviderConfigurationError("Adzuna credentials missing")

    def search_jobs(self, query: JobSearchQuery) -> List[Job]:

        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": query.keywords,
            "where": query.location,
            "results_per_page": query.page_size,
        }
        if query.radius_km is not None:
            params["distance"] = query.radius_km

        url = f"{self.base_url}/{query.page}"

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Adzuna HTTP error: {e.response.status_code}")
            from app.providers.exceptions import ProviderHTTPError
            raise ProviderHTTPError(f"Adzuna HTTP error: {e.response.status_code}")
        except httpx.TimeoutException:
            logger.error("Adzuna connection error: Timeout")
            from app.providers.exceptions import ProviderTimeout
            raise ProviderTimeout("Adzuna connection error: Timeout")
        except httpx.RequestError:
            logger.error("Adzuna connection error: Request failed")
            from app.providers.exceptions import ProviderNetworkError
            raise ProviderNetworkError("Adzuna connection error: Request failed")
        except Exception as e:
            logger.error(f"Adzuna unexpected network error: {type(e).__name__}")
            from app.providers.exceptions import ProviderNetworkError
            raise ProviderError("Adzuna unexpected error")

        # P1.3 Validate top-level schema
        if not isinstance(data, dict) or "results" not in data:
            from app.providers.exceptions import ProviderPayloadError
            raise ProviderPayloadError("AdzunaProviderSchemaError: Missing or malformed 'results' key")

        results = data.get("results")
        if not isinstance(results, list):
            from app.providers.exceptions import ProviderPayloadError
            raise ProviderPayloadError("AdzunaProviderSchemaError: 'results' is not a list")

        jobs = []
        for item in results:
            raw_id = "unknown"
            if not isinstance(item, dict):
                logger.warning("AdzunaProviderSchemaError: 'item' is not a dictionary")
                continue
            try:
                # P1.4 Skip if no stable ID
                raw_id = item.get("id")
                if not raw_id:
                    logger.warning("Adzuna validation error: missing stable ID")
                    continue

                # published_at parsing
                pub_date_str = item.get("created")
                pub_date = None
                if pub_date_str:
                    try:
                        pub_date = datetime.strptime(pub_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

                job = Job(
                    title=item.get("title", ""),
                    company=item.get("company", {}).get("display_name", "Unknown"),
                    source="adzuna",
                    source_job_id=str(raw_id),
                    discovered_at=datetime.now(timezone.utc),
                    location=item.get("location", {}).get("display_name"),
                    description=item.get("description"),
                    salary_min=int(item.get("salary_min")) if item.get("salary_min") else None,
                    salary_max=int(item.get("salary_max")) if item.get("salary_max") else None,
                    url=item.get("redirect_url"),
                    published_at=pub_date
                )
                jobs.append(job)
            except ValidationError as e:
                # Log without raw item to avoid leaks, though it shouldn't have secrets
                logger.warning(f"Adzuna validation error for job {raw_id}")
            except Exception:
                logger.warning(f"Adzuna unexpected parsing error for job {raw_id}")

        return jobs
