import pytest
from dataclasses import replace
from unittest.mock import patch, MagicMock
from datetime import date
from sqlalchemy import text
from app.services.enrichment_worker import EnrichmentWorker
from app.services.scraper.ssrf import SSRFViolation
from app.services.scraper.extractor import ExtractionError
from app.services.scraper.firecrawl import FirecrawlError
from app.services.firecrawl_quota import ProviderReservationReceipt
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
def mock_db(monkeypatch):
    db = MagicMock()
    # process_job now probes the ownership lease before doing any work.  Keep
    # this unit fixture explicit about the committed, live lease that the
    # legacy tests are exercising rather than relying on MagicMock truthiness.
    lease_result = MagicMock()
    lease_result.scalar.return_value = 1
    lease_result.fetchone.return_value = (1,)
    db.execute.return_value = lease_result
    db.commit()

    # Reservation authorization and settlement are independent transactions in
    # production.  Keep those boundaries mocked here while retaining the
    # receipt/status transitions that the worker must account for.
    db.firecrawl_in_flight = MagicMock(
        side_effect=lambda receipt, **_kwargs: replace(receipt, status="in_flight")
    )
    db.firecrawl_settle = MagicMock(
        side_effect=lambda receipt, status, **_kwargs: replace(receipt, status=status)
    )
    monkeypatch.setattr(
        "app.services.firecrawl_quota.mark_provider_request_in_flight",
        db.firecrawl_in_flight,
    )
    monkeypatch.setattr(
        "app.services.firecrawl_quota.settle_provider_reservation",
        db.firecrawl_settle,
    )
    return db


def _receipt_for_request(
    _db,
    *,
    execution_id=None,
    job_id=None,
    lease_token=None,
    operation_key=None,
):
    """Return the durable authorization shape used by a paid scrape."""
    receipt_number = int(job_id or execution_id or 1)
    return ProviderReservationReceipt(
        reservation_id=f"00000000-0000-0000-0000-{receipt_number:012d}",
        operation_key=operation_key or f"enrichment:{job_id}:{lease_token}",
        provider_name="firecrawl_monthly",
        cost_units=1,
        status="reserved",
        usage_date=date.today(),
        execution_id=execution_id,
        job_id=job_id,
        lease_token=lease_token,
    )


def _assert_reservation_accounted(mock_db, expected_status):
    mock_db.firecrawl_settle.assert_called_once()
    receipt, status = mock_db.firecrawl_settle.call_args.args[:2]
    assert isinstance(receipt, ProviderReservationReceipt)
    assert receipt.provider_name == "firecrawl_monthly"
    assert receipt.cost_units == 1
    assert receipt.status == "in_flight"
    assert status == expected_status

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
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
def test_fc_success_from_native_403(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.return_value = "This is a very long markdown string that definitely exceeds the 200 character limit required to pass the content predicate check inside the enrichment worker. This ensures it successfully completes the pipeline."
        worker.process_job(mock_db, 6, "http://blocked.com", "token123")
        # Paid success is durably staged; finalization belongs to a later
        # worker claim and must not be faked by this unit double.
        mock_scrape.assert_called_once_with("http://blocked.com")

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
def test_fc_content_fails_predicate(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.return_value = "Cloudflare Access Denied Please enable cookies to continue"
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 7, "http://blocked.com", "token123")
            mock_snippet.assert_called_once()
            assert mock_snippet.call_args[1]["unsupported"] is True
            mock_scrape.assert_called_once_with("http://blocked.com")
            _assert_reservation_accounted(mock_db, "failed")

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

ADZUNA_JOB_FALLBACK_INTERSTITIAL_FIXTURE = (
    "# Python Job Board\n\n"
    "**Notice:** This page displays a fallback because interactive scripts did not run. "
    "Possible causes include disabled JavaScript or failure to load scripts or stylesheets.\n\n"
    "## Senior Python Developer\n\n"
    "Location: Remote / Bengaluru\n"
    "Company: TechCorp Solutions\n"
    "Salary: ₹2,500,000 - ₹3,500,000 per year\n\n"
    "We are seeking an experienced Senior Python Developer to join our backend engineering team. "
    "In this role, you will design, build, and maintain scalable APIs using FastAPI and PostgreSQL, "
    "collaborate with product managers, and optimize database queries.\n\n"
    "**Requirements:**\n"
    "- 5+ years of software engineering experience with Python\n"
    "- Strong proficiency in SQLAlchemy and asynchronous programming\n"
    "- Solid foundation in relational database schema design and indexing\n"
)

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
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
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
def test_fc_rejects_js_fallback_interstitial_page(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "https://www.python.org/jobs/123"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.return_value = ADZUNA_JOB_FALLBACK_INTERSTITIAL_FIXTURE
        with patch.object(worker, 'complete_success') as mock_success:
            with patch.object(worker, 'complete_with_snippet') as mock_snippet:
                worker.process_job(mock_db, 561, "https://www.python.org/jobs/123", "token123")
                mock_success.assert_not_called()
                mock_snippet.assert_called_once()
                assert mock_snippet.call_args[1]["unsupported"] is True
                assert "fallback/interstitial" in mock_snippet.call_args[0][3]

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
def test_fc_rejects_js_fallback_wording_variations(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "https://example.com/jobs/1"))
    settings.firecrawl_api_key = "test_key"

    variations = [
        "Notice: This page displays a fallback because interactive scripts did not run.\n\n" + ("Job description details here. " * 10),
        "Warning: This page displays a fallback because interactive scripts failed to run.\n\n" + ("Job description details here. " * 10),
        "Notice: page displays a fallback because interactive scripts did not run.\n\n" + ("Job description details here. " * 10),
        "Notice: displays a fallback because interactive scripts were blocked.\n\n" + ("Job description details here. " * 10),
        "Error: failure to load scripts or stylesheets. Please reload.\n\n" + ("Job description details here. " * 10),
        "Notice: disabled javascript or failure to load page resources.\n\n" + ("Job description details here. " * 10),
        "Fallback because interactive scripts could not run.\n\n" + ("Job description details here. " * 10),
    ]

    for idx, content in enumerate(variations):
        with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
            mock_scrape.return_value = content
            with patch.object(worker, 'complete_success') as mock_success:
                with patch.object(worker, 'complete_with_snippet') as mock_snippet:
                    worker.process_job(mock_db, 700 + idx, f"https://example.com/jobs/{idx}", "token123")
                    mock_success.assert_not_called()
                    mock_snippet.assert_called_once()
                    assert mock_snippet.call_args[1]["unsupported"] is True
                    assert "fallback/interstitial" in mock_snippet.call_args[0][3]

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
def test_fc_accepts_realistic_adzuna_job_with_chrome_job_601(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "https://www.adzuna.in/details/5903777285"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.return_value = ADZUNA_JOB_601_REALISTIC_FIXTURE
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 601, "https://www.adzuna.in/details/5903777285", "token123")
            mock_scrape.assert_called_once()
            mock_snippet.assert_not_called()

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
def test_fc_timeout_is_unknown_terminal(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.side_effect = FirecrawlError("Timeout", status_code=408)
        with patch.object(worker, 'complete_failure') as mock_fail:
            worker.process_job(mock_db, 8, "http://blocked.com", "token123")
            mock_fail.assert_called_once()
            assert "unknown_after_crash" in mock_fail.call_args[0][3]
            assert mock_fail.call_args.kwargs["terminal"] is True
            mock_scrape.assert_called_once_with("http://blocked.com")
            _assert_reservation_accounted(mock_db, "unknown_after_crash")

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
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
                mock_scrape.assert_called_once_with("http://blocked.com")
                _assert_reservation_accounted(mock_db, "payment_required")

@patch('app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt', return_value=None)
def test_budget_limit_exhausted(mock_reserve, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        with patch.object(worker, 'complete_with_snippet') as mock_snippet:
            worker.process_job(mock_db, 10, "http://blocked.com", "token123")
            mock_snippet.assert_called_once()
            assert "budget exhausted" in mock_snippet.call_args[0][3]
            assert mock_snippet.call_args[1]["unsupported"] is True
            mock_reserve.assert_called_once()


@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
def test_fc_5xx_is_unknown_terminal(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.side_effect = FirecrawlError("Server Error", status_code=502)
        with patch.object(worker, 'complete_failure') as mock_fail:
            worker.process_job(mock_db, 11, "http://blocked.com", "token123")
            mock_fail.assert_called_once()
            assert "unknown_after_crash" in mock_fail.call_args[0][3]
            assert mock_fail.call_args.kwargs["terminal"] is True
            mock_scrape.assert_called_once_with("http://blocked.com")
            _assert_reservation_accounted(mock_db, "unknown_after_crash")

import respx
@respx.mock
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
def test_fc_transport_failure_is_unknown_terminal(mock_reserve, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"

    # Let the Firecrawl HTTP layer raise httpx.RequestError
    route = respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        side_effect=httpx.RequestError("Network error simulated")
    )

    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        # Do not mock complete_failure: let process_job execute its real complete_failure path
        worker.process_job(mock_db, 12, "http://blocked.com", "token123")

        # Verify the actual database update query executed by complete_failure.
        # A paid transport outcome is terminal: retrying could double-charge.
        mock_db.execute.assert_called()
        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert params["status"] == "failure"
        assert params["job_id"] == 12
        assert params["token"] == "token123"
        assert "unknown_after_crash" in params["reason"]
        assert "Network error simulated" in params["reason"]
        assert params["telemetry"]
        assert route.call_count == 1
        _assert_reservation_accounted(mock_db, "unknown_after_crash")
        mock_db.commit.assert_called()

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
def test_fc_malformed_response_is_unknown_terminal(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.side_effect = Exception("JSON decoding failed")
        with patch.object(worker, 'complete_failure') as mock_fail:
            worker.process_job(mock_db, 13, "http://blocked.com", "token123")
            mock_fail.assert_called_once()
            assert "unknown_after_crash" in mock_fail.call_args[0][3]
            assert mock_fail.call_args.kwargs["terminal"] is True
            mock_scrape.assert_called_once_with("http://blocked.com")
            _assert_reservation_accounted(mock_db, "unknown_after_crash")

@patch('app.services.scraper.firecrawl.FirecrawlClient.scrape')
@patch(
    'app.services.enrichment_worker.EnrichmentWorker._reserve_firecrawl_receipt',
    side_effect=_receipt_for_request,
)
def test_fc_429_is_unknown_terminal(mock_reserve, mock_scrape, worker, mock_db):
    mock_resp = httpx.Response(403, request=httpx.Request("GET", "http://blocked.com"))
    settings.firecrawl_api_key = "test_key"
    with patch.object(worker.ssrf_client, 'fetch', side_effect=httpx.HTTPStatusError("403", request=mock_resp.request, response=mock_resp)):
        mock_scrape.side_effect = FirecrawlError("Rate Limited", status_code=429)
        with patch.object(worker, 'complete_failure') as mock_fail:
            worker.process_job(mock_db, 14, "http://blocked.com", "token123")
            mock_fail.assert_called_once()
            assert "unknown_after_crash" in mock_fail.call_args[0][3]
            assert mock_fail.call_args.kwargs["terminal"] is True
            mock_scrape.assert_called_once_with("http://blocked.com")
            _assert_reservation_accounted(mock_db, "unknown_after_crash")

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
