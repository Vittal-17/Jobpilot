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
