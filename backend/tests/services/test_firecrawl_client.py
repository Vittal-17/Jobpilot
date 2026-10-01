import pytest
import respx
import httpx
from app.services.scraper.firecrawl import FirecrawlClient, FirecrawlError

@respx.mock
def test_firecrawl_client_payload():
    client = FirecrawlClient("test-key")

    route = respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"markdown": "Hello"}})
    )

    result = client.scrape("http://example.com")

    assert result == "Hello"
    assert route.called
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["Content-Type"] == "application/json"

    import json
    payload = json.loads(request.content)
    assert payload == {
        "url": "http://example.com",
        "formats": ["markdown"],
        "onlyMainContent": True,
        "timeout": 30000
    }

@respx.mock
def test_firecrawl_client_402():
    client = FirecrawlClient("test-key")
    respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        return_value=httpx.Response(402, json={"success": False, "error": "Payment Required"})
    )

    with pytest.raises(FirecrawlError) as exc_info:
        client.scrape("http://example.com")
    assert exc_info.value.status_code == 402

@respx.mock
def test_firecrawl_client_timeout():
    client = FirecrawlClient("test-key")
    respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    with pytest.raises(FirecrawlError) as exc_info:
        client.scrape("http://example.com")
    assert exc_info.value.status_code == 408

@respx.mock
def test_firecrawl_client_transport_error():
    client = FirecrawlClient("test-key")
    respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        side_effect=httpx.RequestError("Connection dropped")
    )

    with pytest.raises(FirecrawlError) as exc_info:
        client.scrape("http://example.com")
    assert exc_info.value.status_code is None
