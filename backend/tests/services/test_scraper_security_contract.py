import gzip
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from app.services.job_url_classifier import classify_job_url
from app.services.scraper.firecrawl import FirecrawlClient, FirecrawlPayloadError
from app.services.scraper.ssrf import SSRFClient, SSRFViolation


@respx.mock
def test_firecrawl_scrape_uses_fixed_cost_contract_and_streams_gzip():
    body = json.dumps({"success": True, "data": {"markdown": "ok"}}).encode()
    wire = gzip.compress(body)
    route = respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(wire))},
            content=wire,
        )
    )

    assert FirecrawlClient("key").scrape("https://example.com/job") == "ok"
    payload = json.loads(route.calls.last.request.content)
    assert payload == {
        "url": "https://example.com/job",
        "formats": ["markdown"],
        "onlyMainContent": True,
        "parsers": [],
        "timeout": 30000,
    }


@respx.mock
def test_firecrawl_search_sends_bounded_limit_and_supported_filters():
    route = respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True, "data": []})
    )

    assert FirecrawlClient("key").search(
        "python",
        limit=10000,
        tbs="qdr:m",
        country="in",
        location="Bengaluru",
    ) == []
    payload = json.loads(route.calls.last.request.content)
    assert payload == {
        "query": "python",
        "limit": 100,
        "tbs": "qdr:m",
        "country": "in",
        "location": "Bengaluru",
    }


@respx.mock
def test_firecrawl_rejects_oversized_declared_body_before_reading():
    route = respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Length": str(FirecrawlClient.MAX_WIRE_BYTES + 1)},
            content=b"{}",
        )
    )

    with pytest.raises(FirecrawlPayloadError, match="wire size limit"):
        FirecrawlClient("key").search("python")
    assert route.called


@respx.mock
def test_firecrawl_rejects_malformed_envelope_and_paid_scrape_options():
    respx.post("https://api.firecrawl.dev/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    with pytest.raises(FirecrawlPayloadError, match="missing 'data'"):
        FirecrawlClient("key").search("python")

    with pytest.raises(FirecrawlPayloadError, match="Unsupported"):
        FirecrawlClient("key").scrape("https://example.com", actions=[{"type": "screenshot"}])


def _mock_response(peer: str) -> MagicMock:
    response = MagicMock()
    response.is_redirect = False
    response.status_code = 200
    response.headers = {"content-type": "text/plain"}
    response.iter_bytes.return_value = [b"ok"]
    response.request = httpx.Request("GET", "http://example.com")
    response.extensions = {
        "network_stream": MagicMock(get_extra_info=lambda name: (peer, 80))
    }
    return response


def test_ssrf_binds_selected_destination_and_requires_exact_peer():
    client = SSRFClient()
    response = _mock_response("93.184.216.34")
    with patch.object(client, "_resolve_and_validate", return_value=["93.184.216.34"]):
        with patch("httpx.Client.stream") as stream:
            stream.return_value.__enter__.return_value = response
            fetched = client.fetch("http://example.com")
    assert fetched.content == b"ok"

    mismatch = _mock_response("93.184.216.35")
    with patch.object(client, "_resolve_and_validate", return_value=["93.184.216.34"]):
        with patch("httpx.Client.stream") as stream:
            stream.return_value.__enter__.return_value = mismatch
            with pytest.raises(SSRFViolation, match="does not match selected"):
                client.fetch("http://example.com")


def test_ssrf_fails_closed_without_peer_metadata_and_platform_suffixes_are_exact():
    client = SSRFClient()
    response = _mock_response("93.184.216.34")
    response.extensions = {}
    with patch.object(client, "_resolve_and_validate", return_value=["93.184.216.34"]):
        with patch("httpx.Client.stream") as stream:
            stream.return_value.__enter__.return_value = response
            with pytest.raises(SSRFViolation, match="Unable to verify connected peer"):
                client.fetch("http://example.com")

    assert classify_job_url("https://notglassdoor.com/")[1] != "glassdoor_listing"
    assert classify_job_url("https://evilmyworkdayjobs.com/")[1] != "workday_listing"
