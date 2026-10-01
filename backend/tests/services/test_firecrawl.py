import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from sqlalchemy import text
from app.services.enrichment_worker import EnrichmentWorker
from app.services.scraper.ssrf import SSRFViolation
from app.services.scraper.extractor import ExtractionError
from app.services.scraper.firecrawl import FirecrawlError
import httpx
from app.core.config import settings

@pytest.fixture(autouse=True)
def restore_settings():
    orig_key = settings.firecrawl_api_key
    orig_budget = settings.firecrawl_monthly_budget
    try:
        yield
    finally:
        settings.firecrawl_api_key = orig_key
        settings.firecrawl_monthly_budget = orig_budget

@pytest.fixture
def mock_db():
    db = MagicMock()
    # By default, reserve returns True
    db.execute.return_value.scalar.return_value = 1
    return db

@pytest.fixture
def worker():
    return EnrichmentWorker(max_attempts=3)

@patch('app.services.enrichment_worker.extract_job_description')
def test_native_success(mock_extract, worker, mock_db):
    with patch.object(worker.ssrf_client, 'fetch') as mock_fetch:
        mock_response = MagicMock()
        mock_response.text = "<html>some html</html>"
        mock_fetch.return_value = mock_response
        mock_extract.return_value = "Long extracted text that passes."

        with patch.object(worker, 'complete_success') as mock_success:
            worker.process_job(mock_db, 1, "http://valid.com", "token123")
            mock_success.assert_called_once_with(mock_db, 1, "token123", "Long extracted text that passes.", None)

def test_native_ssrf(worker, mock_db):
    with patch.object(worker.ssrf_client, 'fetch', side_effect=SSRFViolation("Blocked IP")):
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 2, "http://127.0.0.1", "token123")
            mock_snippet.assert_called_once()
            args, kwargs = mock_snippet.call_args
            assert args[3] == "Blocked IP"
            assert kwargs["unsupported"] is True

def test_native_timeout(worker, mock_db):
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.ConnectTimeout("Timeout")):
        with patch.object(worker, 'complete_failure') as mock_fail:
            worker.process_job(mock_db, 3, "http://timeout.com", "token123")
            mock_fail.assert_called_once()
            assert "Timeout" in mock_fail.call_args[0][3]

def test_native_5xx(worker, mock_db):
    mock_resp = httpx.Response(502, request=httpx.Request("GET", "http://err.com"))
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("502", request=mock_resp.request, response=mock_resp)):
        with patch.object(worker, 'complete_failure') as mock_fail:
            worker.process_job(mock_db, 4, "http://err.com", "token123")
            mock_fail.assert_called_once()

def test_native_404(worker, mock_db):
    mock_resp = httpx.Response(404, request=httpx.Request("GET", "http://gone.com"))
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("404", request=mock_resp.request, response=mock_resp)):
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 5, "http://gone.com", "token123")
            mock_snippet.assert_called_once()
            kwargs = mock_snippet.call_args[1]
            assert kwargs
            assert kwargs["unsupported"] is True

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=True)
def test_fc_success_from_native_403(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.return_value = "This is a very long markdown string that definitely exceeds the 200 character limit required to pass the content predicate check inside the enrichment worker. This ensures it successfully completes the pipeline."
        with patch.object(worker, 'complete_success') as mock_success:
            worker.process_job(mock_db, 6, "http://blocked.com", "token123")
            mock_success.assert_called_once()
            assert mock_success.call_args[0][3] == mock_scrape.return_value

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=True)
def test_fc_content_fails_predicate(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.return_value = "Cloudflare Access Denied Please enable cookies to continue"
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 7, "http://blocked.com", "token123")
            mock_snippet.assert_called_once()
            assert mock_snippet.call_args[1]["unsupported"] is True

ADZUNA_JOB_560_REDIRECT_FIXTURE = (
    "# Adzuna\n\n"
    "Every job. Everywhere.\n\n"
    "## You are now being redirected to **LinkedIn**\n\n"
    "If you are not redirected within 5 seconds, [view ad here]"
    "(https://click.appcast.io/t/D4tTJpE0fIOgRYREerHicvT6TT97rTBcoy_WUTFHJzOVv9P0WB6G7rj7lWixjI1lhG1mHJFEivyRSTtabQm9gg"
    "==?ppt=eyJhbGciOiJIUzI1NiJ9.eyJlcG9jaCI6MTc5MDgzNzg5Niwic3JjX2lkIjo1MTQxNzAsImNsaWNrX2lkIjoiQ0l3aGVtVzk4Ukc0Sk1kMXRIMzJfZyIsInNvdXJjZV9yZWYiOiIxNTgyMF80NDcwMDgyNTY0IiwicHBfbmFtZSI6ImFwcGNhc3QifQ.pd_ikVIT3ystivPxB5uMqd16pWU9TDpWgYrIBRzu9Ok)"
)

ADZUNA_JOB_601_REALISTIC_FIXTURE = (
    "## Generative AI Engineer jobs in Bangalore\n\n"
    "Leave us your email address and we'll send you similar new jobsCreate email alert [No, thanks](https://www.adzuna.in/details/5903777285#)\n\n"
    "By creating an email alert, you agree to our [Terms & Conditions](https://www.adzuna.in/terms-and-conditions.html) and [Privacy Notice](https://www.adzuna.in/privacy-policy.html), and Cookie Use. You can cancel at any time.\n\n"
    "Loading...\n\n"
    "Are you based in the United States? Select your country to see jobs specific to your location.\n\n"
    "United KingdomAustraliaÖsterreichBelgiëBrasilCanadaFranceDeutschlandIndiaItaliaMéxicoNederlandNew ZealandPolskaSingaporeSouth AfricaEspañaSchweizUnited StatesContinue\n\n"
    "# Generative AI Engineer\n\n"
    "Gravity Engineering Services Pvt Ltd\n\n"
    "Bengaluru\n\n"
    "8 - 40 lacs/annum\n\n"
    "Full time\n\n"
    "[Apply for this job](https://www.adzuna.in/land/ad/5903777285)\n\n"
    "We are hiring a Generative AI Engineer to build production LLM applications.\n\n"
    "**Responsibilities**\n\n"
    "- Build RAG pipelines with LangChain or LlamaIndex\n"
    "- Design prompts and evaluate model outputs\n"
    "- Manage embeddings in vector databases such as Pinecone, Weaviate or FAISS\n"
    "- Deploy and monitor LLM features in production\n\n"
    "**Requirements**\n\n"
    "- 1+ years building LLM-powered applications\n"
    "- Hands-on with LangChain or LlamaIndex and vector databases\n"
    "- Experience with the OpenAI, Anthropic or open-source model APIs\n\n"
    "Skills:- LangChain, Retrieval Augmented Generation (RAG), Vector database, Prompt engineering and LlamaIndex\n"
)

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=True)
def test_fc_rejects_redirect_wrapper_job_560(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "https://www.adzuna.in/land/ad/5899510236"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.return_value = ADZUNA_JOB_560_REDIRECT_FIXTURE
        with patch.object(worker, 'complete_success') as mock_success:
            with patch.object(worker, 'complete_with_snippet') as mock_snippet:
                worker.process_job(mock_db, 560, "https://www.adzuna.in/land/ad/5899510236", "token123")
                mock_success.assert_not_called()
                mock_snippet.assert_called_once()
                assert mock_snippet.call_args[1]["unsupported"] is True
                assert "redirect/interstitial" in mock_snippet.call_args[0][3]

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=True)
def test_fc_accepts_realistic_adzuna_job_with_chrome_job_601(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "https://www.adzuna.in/details/5903777285"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.return_value = ADZUNA_JOB_601_REALISTIC_FIXTURE
        with patch.object(worker, 'complete_success') as mock_success:
            with patch.object(worker, 'complete_with_snippet') as mock_snippet:
                worker.process_job(mock_db, 601, "https://www.adzuna.in/details/5903777285", "token123")
                mock_success.assert_called_once()
                assert mock_success.call_args[0][3] == ADZUNA_JOB_601_REALISTIC_FIXTURE
                mock_snippet.assert_not_called()

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=True)
def test_fc_timeout_retry(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.side_effect = FirecrawlError("Timeout", status_code=408)
        with patch.object(worker, 'complete_failure') as mock_fail:
            worker.process_job(mock_db, 8, "http://blocked.com", "token123")
            mock_fail.assert_called_once()
            assert "Firecrawl transient error" in mock_fail.call_args[0][3]

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=True)
def test_fc_exhaustion_402(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.side_effect = FirecrawlError("Exhausted", status_code=402)
        with patch.object(worker, '_sync_firecrawl_budget') as mock_sync:
            with patch.object(worker, 'complete_with_snippet') as mock_snippet:
                worker.process_job(mock_db, 9, "http://blocked.com", "token123")
                mock_sync.assert_called_once()
                mock_snippet.assert_called_once()
                assert mock_snippet.call_args[1]["unsupported"] is True

@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=False)
def test_budget_limit_exhausted(mock_reserve, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 10, "http://blocked.com", "token123")
            mock_snippet.assert_called_once()
            assert "budget exhausted" in mock_snippet.call_args[0][3]
            assert mock_snippet.call_args[1]["unsupported"] is True


@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=True)
def test_fc_5xx_retry(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.side_effect = FirecrawlError("Server Error", status_code=502)
        with patch.object(worker, 'complete_failure') as mock_fail:
            worker.process_job(mock_db, 11, "http://blocked.com", "token123")
            mock_fail.assert_called_once()
            assert "Firecrawl transient error" in mock_fail.call_args[0][3]

import respx
@respx.mock
@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=True)
def test_fc_transport_failure_retry_real(mock_reserve, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"

    # Let the Firecrawl HTTP layer raise httpx.RequestError
    respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        side_effect=httpx.RequestError("Network error simulated")
    )

    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        # Do not mock complete_failure: let process_job execute its real complete_failure path
        worker.process_job(mock_db, 12, "http://blocked.com", "token123")

        # Verify the actual database update query executed by complete_failure
        mock_db.execute.assert_called()
        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert params["status"] == "retry"
        assert params["max_attempts"] == worker.max_attempts
        assert params["job_id"] == 12
        assert params["token"] == "token123"
        assert "Firecrawl transient error" in params["reason"]
        assert "Network error simulated" in params["reason"]
        mock_db.commit.assert_called()

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=True)
def test_fc_malformed_response_fallback(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.side_effect = Exception("JSON decoding failed")
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 13, "http://blocked.com", "token123")
            mock_snippet.assert_called_once()
            assert "Firecrawl unhandled error" in mock_snippet.call_args[0][3]

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_credit', return_value=True)
def test_fc_429_retry(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.side_effect = FirecrawlError("Rate Limited", status_code=429)
        with patch.object(worker, 'complete_failure') as mock_fail:
            worker.process_job(mock_db, 14, "http://blocked.com", "token123")
            mock_fail.assert_called_once()
            assert "Firecrawl transient error" in mock_fail.call_args[0][3]

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
def test_missing_api_key(mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = None
    settings.firecrawl_monthly_budget = 1000
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 15, "http://blocked.com", "token123")
            mock_snippet.assert_called_once()
            assert mock_snippet.call_args[1]["unsupported"] is True
            mock_scrape.assert_not_called()

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
def test_negative_budget(mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    settings.firecrawl_monthly_budget = -1
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 16, "http://blocked.com", "token123")
            mock_snippet.assert_called_once()
            assert mock_snippet.call_args[1]["unsupported"] is True
            mock_scrape.assert_not_called()

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
def test_zero_budget(mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    settings.firecrawl_monthly_budget = 0
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 17, "http://blocked.com", "token123")
            mock_snippet.assert_called_once()
            assert mock_snippet.call_args[1]["unsupported"] is True
            mock_scrape.assert_not_called()
