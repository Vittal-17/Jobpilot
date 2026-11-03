"""Shared retrieval-location policy used after asynchronous enrichment."""
import re

_ALIASES = {
    "bangalore": "bengaluru",
    "bengaluru": "bengaluru",
    "bombay": "mumbai",
    "mumbai": "mumbai",
    "delhi": "delhi",
    "new delhi": "delhi",
}


def _tokens(value: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", value.lower()) if part}


def _canonical(value: str) -> str:
    cleaned = " ".join(value.lower().split())
    return _ALIASES.get(cleaned, cleaned)


def _parse(value: str | None) -> tuple[str, set[str]]:
    """Normalize location text, rejecting punctuation-only/malformed values."""
    raw = (value or "").strip()
    tokens = _tokens(raw)
    if not tokens:
        return raw, set()
    # Preserve word order for phrase aliases, then canonicalize individual
    # words within compound locations (not just whole-field aliases).
    normalized = " ".join(re.findall(r"[a-z0-9]+", raw.lower()))
    normalized = re.sub(r"\bnew delhi\b", "delhi", normalized)
    canonical = " ".join(_ALIASES.get(token, token) for token in normalized.split())
    return canonical, _tokens(canonical)


def posting_matches_retrieval_location(
    job_location: str | None,
    remote: bool | None,
    retrieval_location: str | None,
) -> bool:
    """Return whether a posting is valid for an execution's location filter.

    Empty filters are unrestricted. Remote postings are valid for an explicit
    filter only when the posting also carries a concrete matching location;
    this prevents a location-less remote posting from leaking into a narrowed
    city execution. Matching uses canonical aliases and token boundaries.
    """
    requested_raw = (retrieval_location or "").strip()
    requested, requested_tokens = _parse(requested_raw)
    if not requested_raw:
        return True
    if not requested_tokens:
        return False
    if all(len(token) < 2 for token in requested_tokens):
        return False
    actual, actual_tokens = _parse(job_location)
    if not actual_tokens:
        return False
    # A posting may be more specific than the requested place ("Bengaluru,
    # India"), but a broad posting must never satisfy a narrower request.
    # In particular this prevents country-vs-city and reverse broad matches.
    if requested_tokens.issubset(actual_tokens):
        return True
    # Related areas are discovery hints, not aliases or proof that a posting
    # satisfies a more specific requested neighbourhood.
    return False
