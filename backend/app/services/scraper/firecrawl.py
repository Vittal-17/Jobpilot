"""Bounded Firecrawl API client.

Firecrawl responses are provider-controlled input.  Do not use ``Response
.content``/``.json()`` here: both can buffer an unbounded body before a size
check.  The client checks Content-Length before reading, then counts both wire
bytes and decoded/decompressed bytes while streaming the body.
"""

from __future__ import annotations

import json
import time
import zlib
from typing import Any, Iterator

import httpx
from pydantic import BaseModel


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
    # This is deliberately a response-body budget, not a post-read check.
    MAX_RESPONSE_BYTES = 2 * 1024 * 1024
    MAX_WIRE_BYTES = MAX_RESPONSE_BYTES
    MAX_MARKDOWN_CHARS = 1 * 1024 * 1024
    MAX_RESULTS = 100
    MAX_RESULT_STRING_CHARS = 10000
    SCRAPE_COST_UNITS = 1
    STREAM_CHUNK_SIZE = 16 * 1024

    def __init__(self, api_key: str, timeout: int = 30000):
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Do not advertise optional HTTPX decoders we intentionally reject.
            "Accept-Encoding": "gzip, deflate",
        }

    @staticmethod
    def _status(response: httpx.Response) -> int | None:
        return getattr(response, "status_code", None)

    def _declared_length(self, response: httpx.Response) -> int | None:
        raw = response.headers.get("content-length")
        if raw is None:
            return None
        value = raw.strip()
        # A comma-separated duplicate Content-Length is intentionally rejected
        # instead of choosing one value.
        if not value or not value.isascii() or not value.isdigit():
            raise FirecrawlPayloadError(
                "Firecrawl response has malformed Content-Length",
                status_code=self._status(response),
            )
        # Avoid even converting an attacker-controlled, thousands-of-digits
        # header to an integer. Leading zeroes do not affect its value.
        significant = value.lstrip("0") or "0"
        if len(significant) > len(str(self.MAX_WIRE_BYTES)):
            raise FirecrawlPayloadError(
                "Firecrawl response exceeded wire size limit",
                status_code=self._status(response),
            )
        declared = int(significant)
        if declared > self.MAX_WIRE_BYTES:
            raise FirecrawlPayloadError(
                "Firecrawl response exceeded wire size limit",
                status_code=self._status(response),
            )
        return declared

    def _decoder(self, response: httpx.Response):
        encoding = response.headers.get("content-encoding", "").strip().lower()
        if not encoding or encoding == "identity":
            return None
        if "," in encoding:
            raise FirecrawlPayloadError(
                "Firecrawl response uses unsupported multiple content encodings",
                status_code=self._status(response),
            )
        if encoding == "gzip":
            return zlib.decompressobj(16 + zlib.MAX_WBITS)
        if encoding == "deflate":
            return zlib.decompressobj()
        # httpx may be able to decode optional brotli/zstd dependencies, but
        # accepting them here would make the decompression budget dependent on
        # the environment.  Fail closed instead.
        raise FirecrawlPayloadError(
            "Firecrawl response uses unsupported content encoding",
            status_code=self._status(response),
        )

    def _read_streamed_body(self, response: httpx.Response, *, deadline: float | None = None) -> bytes:
        declared = self._declared_length(response)
        decoder = self._decoder(response)
        wire_bytes = 0
        decoded_bytes = 0
        body = bytearray()

        def append_decoded(chunk: bytes) -> None:
            nonlocal decoded_bytes
            if not chunk:
                return
            decoded_bytes += len(chunk)
            if decoded_bytes > self.MAX_RESPONSE_BYTES:
                raise FirecrawlPayloadError(
                    "Firecrawl response exceeded decoded size limit",
                    status_code=self._status(response),
                )
            body.extend(chunk)

        try:
            # iter_raw is the wire stream.  Unlike iter_bytes it does not
            # silently decompress before our limits are applied.
            raw_chunks: Iterator[bytes] = response.iter_raw(chunk_size=self.STREAM_CHUNK_SIZE)
            for raw in raw_chunks:
                if deadline is not None and time.monotonic() >= deadline:
                    raise FirecrawlError("Firecrawl request timed out", status_code=408)
                wire_bytes += len(raw)
                if wire_bytes > self.MAX_WIRE_BYTES:
                    raise FirecrawlPayloadError(
                        "Firecrawl response exceeded wire size limit",
                        status_code=self._status(response),
                    )
                if decoder is None:
                    append_decoded(raw)
                else:
                    # One extra byte beyond the remaining budget lets us
                    # detect overflow without asking zlib for a huge output.
                    remaining = self.MAX_RESPONSE_BYTES - decoded_bytes
                    append_decoded(decoder.decompress(raw, max(1, remaining + 1)))
                    if decoder.unused_data:
                        raise FirecrawlPayloadError(
                            "Firecrawl response has trailing compressed content",
                            status_code=self._status(response),
                        )
            if decoder is not None:
                # No unbounded flush(): decompress emits the complete output
                # on a valid EOF. A tail means output was not fully consumed;
                # a missing EOF means the compression envelope was truncated.
                if not decoder.eof or decoder.unconsumed_tail:
                    raise FirecrawlPayloadError(
                        "Firecrawl response has incomplete compressed content",
                        status_code=self._status(response),
                    )
        except zlib.error as exc:
            raise FirecrawlPayloadError(
                "Firecrawl response has invalid compressed content",
                status_code=self._status(response),
            ) from exc
        if declared is not None and declared != wire_bytes:
            raise FirecrawlPayloadError(
                "Firecrawl response Content-Length does not match body",
                status_code=self._status(response),
            )
        return bytes(body)

    def _decode_json(self, body: bytes, response: httpx.Response) -> Any:
        def reject_constant(value: str) -> Any:
            raise ValueError("Non-finite JSON number")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            obj: dict[str, Any] = {}
            for key, value in pairs:
                if key in obj:
                    raise ValueError("Duplicate JSON key")
                obj[key] = value
            return obj

        try:
            text = body.decode("utf-8")
            return json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise FirecrawlPayloadError(
                f"Firecrawl returned invalid JSON: {self._diagnostic(body)}",
                status_code=self._status(response),
            ) from exc

    def _diagnostic(self, body: bytes) -> str:
        # Never include an unbounded provider response in an exception/log.
        text = body[: self.MAX_RESULT_STRING_CHARS].decode("utf-8", errors="replace")
        if len(body) > self.MAX_RESULT_STRING_CHARS:
            text += "..."
        return text

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> tuple[httpx.Response, bytes]:
        deadline = time.monotonic() + self.timeout / 1000.0
        try:
            # Explicitly disable environment proxies.  Firecrawl is an
            # external paid provider and should not inherit a proxy from a
            # process environment unexpectedly.
            with httpx.Client(
                timeout=self.timeout / 1000.0,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                with client.stream(
                    "POST",
                    endpoint,
                    json=payload,
                    headers=self._headers(),
                ) as response:
                    body = self._read_streamed_body(response, deadline=deadline)
                    return response, body
        except httpx.TimeoutException as exc:
            raise FirecrawlError("Firecrawl request timed out", status_code=408) from exc
        except httpx.RequestError as exc:
            detail = str(exc)[:self.MAX_RESULT_STRING_CHARS]
            raise FirecrawlError(f"Firecrawl request failed: {detail}", status_code=None) from exc

    def _raise_http_error(self, response: httpx.Response, body: bytes) -> None:
        status = self._status(response)
        if status != 200:
            raise FirecrawlError(
                f"Firecrawl returned {status}: {self._diagnostic(body)}",
                status_code=status,
            )

    def scrape(self, url: str, **unsupported_options: Any) -> str:
        if not self.api_key:
            raise FirecrawlError("FIRECRAWL_API_KEY is not configured", status_code=None)
        if unsupported_options:
            raise FirecrawlPayloadError(
                "Unsupported Firecrawl scrape options"
            )

        # This is the fixed-cost scrape contract.  In particular, parsers=[]
        # prevents Firecrawl's paid PDF parsing path; a PDF remains one flat
        # scrape credit. Official docs say an unparsed PDF is base64, not
        # extracted text, so any non-markdown result fails closed below.
        # https://docs.firecrawl.dev/advanced-scraping-guide#pdf-parsing
        payload = {
            "url": url,
            "formats": ["markdown"],
            # Optional parsers can have a per-page charge.  An explicit empty
            # parser list is the fixed one-credit scrape contract.
            "parsers": [],
            "onlyMainContent": True,
            "timeout": self.timeout,
        }
        try:
            response, body = self._post_json("https://api.firecrawl.dev/v2/scrape", payload)
            self._raise_http_error(response, body)
            data = self._decode_json(body, response)
            if not isinstance(data, dict):
                raise FirecrawlPayloadError(
                    "Firecrawl response is not a valid JSON object",
                    status_code=self._status(response),
                )
            if type(data.get("success")) is not bool:
                raise FirecrawlPayloadError(
                    "Firecrawl scrape response envelope is malformed",
                    status_code=self._status(response),
                )
            if not data["success"]:
                raise FirecrawlError(
                    f"Firecrawl scrape failed: {self._diagnostic(body)}",
                    status_code=self._status(response),
                )
            raw_data = data.get("data")
            if not isinstance(raw_data, dict):
                raise FirecrawlPayloadError(
                    "Firecrawl scrape payload 'data' is not an object",
                    status_code=self._status(response),
                )
            markdown = raw_data.get("markdown")
            if not isinstance(markdown, str):
                raise FirecrawlPayloadError(
                    "Firecrawl scrape payload 'data.markdown' is not a string",
                    status_code=self._status(response),
                )
            if len(markdown) > self.MAX_MARKDOWN_CHARS:
                raise FirecrawlPayloadError(
                    "Firecrawl markdown exceeded size limit",
                    status_code=self._status(response),
                )
            return markdown
        except FirecrawlError:
            raise

    def _validate_result_values(self, results: list[Any], response: httpx.Response) -> None:
        if len(results) > self.MAX_RESULTS:
            raise FirecrawlPayloadError(
                "Firecrawl search results exceeded size limit",
                status_code=self._status(response),
            )
        if any(not isinstance(item, dict) for item in results):
            raise FirecrawlPayloadError(
                "Firecrawl search results contain a non-object item",
                status_code=self._status(response),
            )
        # Walk nested values iteratively so a provider-controlled nested JSON
        # object cannot recurse through Python's call stack.
        pending: list[Any] = list(results)
        visited = 0
        while pending:
            value = pending.pop()
            visited += 1
            if visited > 10000:
                raise FirecrawlPayloadError(
                    "Firecrawl search payload is too deeply nested",
                    status_code=self._status(response),
                )
            if isinstance(value, str) and len(value) > self.MAX_RESULT_STRING_CHARS:
                raise FirecrawlPayloadError(
                    "Firecrawl result string exceeded size limit",
                    status_code=self._status(response),
                )
            if isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)

    def search(
        self,
        query: str,
        limit: int = 10,
        tbs: str | None = None,
        country: str | None = None,
        location: str | None = None,
        **unsupported_options: Any,
    ) -> list[dict]:
        if not self.api_key:
            raise FirecrawlError("FIRECRAWL_API_KEY is not configured", status_code=None)
        if unsupported_options:
            raise FirecrawlPayloadError("Unsupported Firecrawl search options")
        if not query or not query.strip():
            return []

        # Search pricing is 2 credits per ten results, rounded up.  The API
        # limit is bounded here so callers cannot accidentally request an
        # unbounded paid operation. Supported filters are sent only when they
        # contain a non-empty trimmed value so empty optional fields do not
        # alter provider defaults.
        # https://docs.firecrawl.dev/features/search#cost-implications
        try:
            if isinstance(limit, bool):
                raise ValueError
            bounded_limit = self.bounded_search_limit(limit)
        except (TypeError, ValueError) as exc:
            raise FirecrawlPayloadError("Firecrawl search limit must be an integer") from exc

        payload = {
            "query": query.strip(),
            "limit": bounded_limit,
        }
        for name, value in (("tbs", tbs), ("country", country), ("location", location)):
            if isinstance(value, str):
                trimmed = value.strip()
                if trimmed:
                    payload[name] = trimmed
        try:
            response, body = self._post_json("https://api.firecrawl.dev/v2/search", payload)
            self._raise_http_error(response, body)
            data = self._decode_json(body, response)
            if not isinstance(data, dict):
                raise FirecrawlPayloadError(
                    "Firecrawl response is not a valid JSON object",
                    status_code=self._status(response),
                )
            if type(data.get("success")) is not bool:
                raise FirecrawlPayloadError(
                    "Firecrawl search response envelope is malformed",
                    status_code=self._status(response),
                )
            if not data["success"]:
                raise FirecrawlPayloadError(
                    f"Firecrawl search failed: {self._diagnostic(body)}",
                    status_code=self._status(response),
                )

            if "data" not in data:
                raise FirecrawlPayloadError(
                    "Firecrawl search response is missing 'data'",
                    status_code=self._status(response),
                )
            raw_data = data["data"]
            if isinstance(raw_data, dict):
                if "web" not in raw_data or not isinstance(raw_data["web"], list):
                    raise FirecrawlPayloadError(
                        "Firecrawl search results payload 'data.web' is missing or not a list",
                        status_code=self._status(response),
                    )
                results = raw_data["web"]
            elif isinstance(raw_data, list):
                # Legacy Firecrawl response shape retained for compatibility.
                results = raw_data
            else:
                raise FirecrawlPayloadError(
                    "Firecrawl search results payload 'data' is neither a list nor a dictionary containing 'web'",
                    status_code=self._status(response),
                )

            self._validate_result_values(results, response)
            return results
        except FirecrawlError:
            raise

    @classmethod
    def bounded_search_limit(cls, limit: int) -> int:
        """Return the exact result bound used by both request and billing."""
        if isinstance(limit, bool):
            raise ValueError("Firecrawl search limit must be an integer")
        try:
            return max(1, min(int(limit), cls.MAX_RESULTS))
        except (TypeError, ValueError) as exc:
            raise ValueError("Firecrawl search limit must be an integer") from exc

    @classmethod
    def scrape_billing_contract_verified(cls) -> bool:
        return cls.SCRAPE_COST_UNITS == 1
