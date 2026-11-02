"""P0 regression coverage for bounded content validation and provider bodies."""

from __future__ import annotations

import gzip
import json
import subprocess
import sys

import httpx
import pytest
import respx

from app.services.scraper.firecrawl import FirecrawlClient, FirecrawlPayloadError


class _CountingStream(httpx.SyncByteStream):
    """Prove that the public client stops consuming after a budget violation."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.reads = 0
        self.closed = False

    def __iter__(self):
        for chunk in self._chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


@pytest.mark.parametrize("api", ["title_classifier", "posting_validator"])
def test_h18_numeric_near_miss_is_bounded_and_linear_at_exact_sizes(api):
    """Exercise the actual overlapping-digit regex near miss from H18.

    Run the timing loop in a child so a catastrophic regression cannot hang
    the suite.  The 90-second watchdog is not a performance target.  Relative
    growth is the assertion; a 2ms timer floor and 4x normalized-work allowance
    tolerate CI noise while rejecting quadratic/exponential growth over 16x
    input.  CPU time (not scheduler contention) determines the growth ratio;
    wall time and the watchdog also establish bounded completion.  Import and
    startup time is excluded from every sample.  Fast paths use three samples;
    a slow path is sampled once so every exact size can still be measured.
    """
    script = """
import json, statistics, sys, time
from app.services.enrichment_worker import validate_posting_content
from app.services.job_url_classifier import classify_job_url
api = sys.argv[1]
for size in (1024, 2048, 4096, 8192, 16384):
    payload = '9' * (size - 1) + '!'
    durations = []
    wall_durations = []
    for _ in range(3):
        start = time.process_time()
        wall_start = time.perf_counter()
        if api == 'title_classifier':
            outcome = classify_job_url('https://example.com/jobs/software-engineer-1234', payload)
        else:
            outcome = validate_posting_content('Backend Engineer', payload)
        durations.append(time.process_time() - start)
        wall_durations.append(time.perf_counter() - wall_start)
        assert isinstance(outcome, tuple) and len(outcome) == 2
        if wall_durations[-1] > 0.05:
            break
    sample = {'size': len(payload.encode()), 'seconds': statistics.median(durations), 'wall_seconds': statistics.median(wall_durations)}
    print(json.dumps(sample), flush=True)
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, api], capture_output=True, text=True, check=True, timeout=90
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"H18 {api} exceeded the generous 90s watchdog; completed samples: {exc.stdout!r}")
    samples = [json.loads(line) for line in result.stdout.splitlines()]
    assert [sample["size"] for sample in samples] == [1024, 2048, 4096, 8192, 16384]
    print(json.dumps({"api": api, "samples": samples}))
    normalized = [max(sample["seconds"], 0.002) / sample["size"] for sample in samples]
    assert normalized[-1] <= min(normalized[:2]) * 4, samples
    for left, right in zip(samples, samples[1:]):
        assert right["seconds"] <= max(left["seconds"], 0.002) * 8, samples


def _invoke(method: str):
    client = FirecrawlClient("key")
    return client.search("python") if method == "search" else client.scrape("https://example.com/job")


def _body(method: str) -> bytes:
    return json.dumps({"success": True, "data": [] if method == "search" else {"markdown": "ok"}}).encode()


@pytest.mark.parametrize("method", ["search", "scrape"])
def test_h19_stream_without_content_length_is_bounded(method):
    body = _body(method)
    stream = _CountingStream([body[:3], body[3:]])
    with respx.mock:
        respx.post(f"https://api.firecrawl.dev/v2/{method}").respond(200, stream=stream)
        assert _invoke(method) == ([] if method == "search" else "ok")
    assert stream.reads == 2
    assert stream.closed is True


@pytest.mark.parametrize("method", ["search", "scrape"])
@pytest.mark.parametrize("declared_delta", [-1, 1])
def test_h19_false_content_length_is_rejected(method, declared_delta):
    body = _body(method)
    stream = _CountingStream([body])
    with respx.mock:
        respx.post(f"https://api.firecrawl.dev/v2/{method}").respond(
            200, headers={"Content-Length": str(len(body) + declared_delta)}, stream=stream
        )
        with pytest.raises(FirecrawlPayloadError, match="Content-Length does not match"):
            _invoke(method)
    assert stream.closed is True


@pytest.mark.parametrize("method", ["search", "scrape"])
@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}], ids=["no_length", "false_length"])
def test_h19_oversized_stream_is_rejected_without_consuming_tail(method, headers):
    chunks = [b"x" * FirecrawlClient.STREAM_CHUNK_SIZE] * (
        FirecrawlClient.MAX_WIRE_BYTES // FirecrawlClient.STREAM_CHUNK_SIZE + 2
    )
    stream = _CountingStream(chunks)
    with respx.mock:
        respx.post(f"https://api.firecrawl.dev/v2/{method}").respond(200, headers=headers, stream=stream)
        with pytest.raises(FirecrawlPayloadError, match="wire size limit"):
            _invoke(method)
    assert stream.reads == len(chunks) - 1
    assert stream.closed is True


@pytest.mark.parametrize("method", ["search", "scrape"])
def test_h19_oversized_declared_length_is_rejected_before_reading(method):
    stream = _CountingStream([b"{}"])
    with respx.mock:
        respx.post(f"https://api.firecrawl.dev/v2/{method}").respond(
            200, headers={"Content-Length": str(FirecrawlClient.MAX_WIRE_BYTES + 1)}, stream=stream
        )
        with pytest.raises(FirecrawlPayloadError, match="wire size limit"):
            _invoke(method)
    assert stream.reads == 0
    assert stream.closed is True


@pytest.mark.parametrize("method", ["search", "scrape"])
def test_h19_compression_expansion_is_rejected_after_decoding_budget(method):
    wire = gzip.compress(b"x" * (FirecrawlClient.MAX_RESPONSE_BYTES + 1))
    assert len(wire) < FirecrawlClient.MAX_WIRE_BYTES
    stream = _CountingStream([wire])
    with respx.mock:
        respx.post(f"https://api.firecrawl.dev/v2/{method}").respond(
            200,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(wire))},
            stream=stream,
        )
        with pytest.raises(FirecrawlPayloadError, match="decoded size limit"):
            _invoke(method)
    assert stream.closed is True
