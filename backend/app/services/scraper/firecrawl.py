import httpx
from pydantic import BaseModel, Field

class FirecrawlResponseData(BaseModel):
    markdown: str = ""

class FirecrawlResponse(BaseModel):
    success: bool
    data: FirecrawlResponseData | None = None

class FirecrawlError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code

class FirecrawlPayloadError(FirecrawlError):
    pass

class FirecrawlClient:
    def __init__(self, api_key: str, timeout: int = 30000):
        self.api_key = api_key
        self.timeout = timeout

    def scrape(self, url: str) -> str:
        if not self.api_key:
            raise FirecrawlError("FIRECRAWL_API_KEY is not configured", status_code=None)

        payload = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": self.timeout
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            # We use timeout/1000 because httpx expects seconds
            with httpx.Client(timeout=self.timeout / 1000.0) as client:
                resp = client.post(
                    "https://api.firecrawl.dev/v2/scrape",
                    json=payload,
                    headers=headers
                )
                if resp.status_code != 200:
                    raise FirecrawlError(f"Firecrawl returned {resp.status_code}: {resp.text}", status_code=resp.status_code)

                data = resp.json()
                if not data.get("success"):
                    raise FirecrawlError(f"Firecrawl scrape failed: {data}", status_code=resp.status_code)

                markdown = data.get("data", {}).get("markdown", "")
                if not isinstance(markdown, str):
                    markdown = ""
                return markdown

        except httpx.TimeoutException:
            raise FirecrawlError("Firecrawl request timed out", status_code=408)
        except httpx.RequestError as e:
            raise FirecrawlError(f"Firecrawl request failed: {e}", status_code=None)

    def search(
        self,
        query: str,
        limit: int = 10,
        tbs: str | None = None,
        country: str | None = None,
        location: str | None = None,
    ) -> list[dict]:
        if not self.api_key:
            raise FirecrawlError("FIRECRAWL_API_KEY is not configured", status_code=None)

        if not query or not query.strip():
            return []

        payload: dict[str, str | int] = {
            "query": query.strip(),
            "limit": limit,
        }
        if tbs:
            payload["tbs"] = tbs
        if country:
            payload["country"] = country
        if location:
            payload["location"] = location

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=self.timeout / 1000.0) as client:
                resp = client.post(
                    "https://api.firecrawl.dev/v2/search",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code != 200:
                    raise FirecrawlError(f"Firecrawl returned {resp.status_code}: {resp.text}", status_code=resp.status_code)

                try:
                    data = resp.json()
                except Exception as e:
                    raise FirecrawlPayloadError(f"Firecrawl returned invalid JSON: {e}") from e

                if not isinstance(data, dict):
                    raise FirecrawlPayloadError("Firecrawl response is not a valid JSON object")

                if not data.get("success", False):
                    raise FirecrawlPayloadError(f"Firecrawl search failed: {data}")

                raw_data = data.get("data")
                if raw_data is None:
                    return []

                if isinstance(raw_data, dict):
                    raw_results = raw_data.get("web")
                    if not isinstance(raw_results, list):
                        raise FirecrawlPayloadError("Firecrawl search results payload 'data.web' is missing or not a list")
                    return raw_results
                elif isinstance(raw_data, list):
                    return raw_data
                else:
                    raise FirecrawlPayloadError("Firecrawl search results payload 'data' is neither a list nor a dictionary containing 'web'")

        except httpx.TimeoutException:
            raise FirecrawlError("Firecrawl request timed out", status_code=408)
        except httpx.RequestError as e:
            raise FirecrawlError(f"Firecrawl request failed: {e}", status_code=None)
