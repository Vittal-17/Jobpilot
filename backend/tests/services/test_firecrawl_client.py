import pytest
import respx
import httpx
from app.services.scraper.firecrawl import FirecrawlClient, FirecrawlError, FirecrawlPayloadError

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


@respx.mock
def test_firecrawl_client_search_v2_web_format():
    client = FirecrawlClient("test-key")
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "web": [
                        {
                            "url": "https://example.com/job1",
                            "title": "Python Engineer",
                            "description": "Great Python role",
                            "position": 1,
                        }
                    ]
                },
            },
        )
    )

    results = client.search("Python Engineer", limit=10)
    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/job1"
    assert results[0]["title"] == "Python Engineer"
    assert results[0]["description"] == "Great Python role"
    assert results[0]["position"] == 1


@respx.mock
def test_firecrawl_client_search_legacy_list_format():
    client = FirecrawlClient("test-key")
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "data": [
                    {
                        "url": "https://example.com/job2",
                        "title": "Backend Dev",
                        "description": "FastAPI role",
                    }
                ],
            },
        )
    )

    results = client.search("Backend Dev", limit=10)
    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/job2"
    assert results[0]["title"] == "Backend Dev"


@respx.mock
def test_firecrawl_client_search_empty_results():
    client = FirecrawlClient("test-key")

    # data.web is empty list
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"web": []}})
    )
    assert client.search("query 1") == []

    # data is empty list
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True, "data": []})
    )
    assert client.search("query 2") == []

    # data is None
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True, "data": None})
    )
    assert client.search("query 3") == []

    # data key missing entirely
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    assert client.search("query 4") == []


@respx.mock
def test_firecrawl_client_search_malformed_dict_missing_web():
    client = FirecrawlClient("test-key")
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"other_key": "val"}})
    )

    with pytest.raises(FirecrawlPayloadError, match="payload 'data.web' is missing or not a list"):
        client.search("test")


@respx.mock
def test_firecrawl_client_search_malformed_dict_web_not_list():
    client = FirecrawlClient("test-key")
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"web": "not a list"}})
    )

    with pytest.raises(FirecrawlPayloadError, match="payload 'data.web' is missing or not a list"):
        client.search("test")


@respx.mock
def test_firecrawl_client_search_malformed_data_not_dict_or_list():
    client = FirecrawlClient("test-key")

    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True, "data": "unexpected string"})
    )
    with pytest.raises(FirecrawlPayloadError, match="neither a list nor a dictionary"):
        client.search("test 1")

    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True, "data": 42})
    )
    with pytest.raises(FirecrawlPayloadError, match="neither a list nor a dictionary"):
        client.search("test 2")


@respx.mock
def test_firecrawl_client_search_non_object_payload():
    client = FirecrawlClient("test-key")
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json=["not", "a", "dict"])
    )

    with pytest.raises(FirecrawlPayloadError, match="not a valid JSON object"):
        client.search("test")


@respx.mock
def test_firecrawl_client_search_success_false():
    client = FirecrawlClient("test-key")
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": False, "error": "Search quota exhausted"})
    )

    with pytest.raises(FirecrawlPayloadError, match="Firecrawl search failed"):
        client.search("test")
