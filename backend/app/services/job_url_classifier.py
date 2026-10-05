import logging
import re
import urllib.parse
from typing import Tuple

logger = logging.getLogger(__name__)

# Precompiled regexes for title analysis
TITLE_AGGREGATE_COUNT_RE = re.compile(
    r'(?:'
    r'\b\d+[\d,+]*(?:\+)?\s*(?:python|backend|frontend|full\s*stack|software|developer|engineer|fresher|entry\s*level|internship)?\s*(?:jobs?|vacancies|openings|roles)\b'
    r'|\(\s*\d+[\d,+]*(?:\+)?\s*(?:open\s*roles|jobs?|openings|vacancies)\s*\)'
    r'|\b(?:apply to\s+)?\d+[\d,+]*(?:\+)?\s+.*?(?:jobs?|openings|vacancies)\b'
    r')',
    re.IGNORECASE,
)

TITLE_AGGREGATE_PHRASE_RE = re.compile(
    r'(?:'
    r'\b(?:hiring|find|search|top|browse|all)\s+.*?\s+jobs\s+in\b'
    r'|^hiring\s+.*?jobs\s+in\b'
    r'|\bjobs\s+in\s+[\w\s,]+(?:\s*-\s*\d{4})?$'
    r'|\bjob\s+vacancies\s+in\b'
    r'|\b\d{4}\s+jobs\b'
    r')',
    re.IGNORECASE,
)

GENERIC_SEARCH_PARAMS = {"q", "query", "kw", "keyword", "keywords", "search", "search_term"}
AGGREGATE_PATH_SEGMENTS = {"search", "browse", "categories", "category", "tags", "tag", "explore", "find-jobs", "all-jobs"}
GENERIC_ROOT_PATHS = {"", "/", "/jobs", "/jobs/", "/careers", "/careers/", "/openings", "/openings/", "/vacancies", "/vacancies/"}


def _matches_domain(netloc: str, domain_pattern: str) -> bool:
    """Checks if netloc matches or is a subdomain of domain_pattern."""
    return netloc == domain_pattern or netloc.endswith("." + domain_pattern)


def classify_job_url(raw_url: str, title: str | None = None) -> Tuple[bool, str]:
    """
    Deterministically classify whether a URL and title represent an individual job vacancy
    or a listing / category / search aggregator page.

    Returns:
        (is_valid, reason_code)
        is_valid: True if verified individual job, False otherwise.
        reason_code: String code explaining the classification outcome.
    """
    if not raw_url or not isinstance(raw_url, str) or not raw_url.strip():
        return False, "missing_url"

    clean_url = raw_url.strip()
    try:
        parsed = urllib.parse.urlparse(clean_url)
    except Exception:
        return False, "unparseable_url"

    if parsed.scheme not in ("http", "https"):
        return False, "unsupported_scheme"

    netloc = (parsed.netloc or "").lower().strip()
    if not netloc:
        return False, "invalid_domain"

    # Strip port and 'www.' prefix
    if ":" in netloc:
        netloc = netloc.split(":")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path or "/"
    query = parsed.query or ""
    path_lower = path.lower()

    # -------------------------------------------------------------------------
    # 1. Universal Title Checks (applies across all domains)
    # -------------------------------------------------------------------------
    if title and isinstance(title, str) and title.strip():
        clean_title = title.strip()
        if TITLE_AGGREGATE_COUNT_RE.search(clean_title):
            return False, "title_aggregate_listing"
        if TITLE_AGGREGATE_PHRASE_RE.search(clean_title):
            return False, "title_aggregate_listing"

    # -------------------------------------------------------------------------
    # 2. Platform-Specific Path & Query Rules
    # -------------------------------------------------------------------------
    # Indeed: e.g. indeed.com, in.indeed.com
    if _matches_domain(netloc, "indeed.com"):
        has_jk = "jk=" in query or "vjk=" in query or "jk=" in path_lower
        is_indeed_job = (
            ("/viewjob" in path_lower and (has_jk or len(query) > 3 or len(path_lower.strip("/").split("/")) > 1))
            or ("/rc/clk" in path_lower and has_jk)
            or has_jk
        )
        if is_indeed_job and path_lower.strip("/") not in ("viewjob", "rc/clk"):
            return True, "individual_posting"
        if is_indeed_job and has_jk:
            return True, "individual_posting"
        return False, "indeed_listing"

    # LinkedIn: e.g. linkedin.com, in.linkedin.com
    if _matches_domain(netloc, "linkedin.com"):
        if path_lower.startswith("/jobs/view/") and re.search(r'/jobs/view/.*?\d{4,}', path_lower):
            return True, "individual_posting"
        return False, "linkedin_listing"

    # Naukri: e.g. naukri.com
    if _matches_domain(netloc, "naukri.com"):
        if "/job-listings" in path_lower:
            clean_p = path_lower.strip("/")
            if clean_p not in ("job-listings", "job-listing"):
                after = clean_p.split("job-listings", 1)[1]
                if len(after.strip("-/")) >= 8:
                    return True, "individual_posting"
        return False, "naukri_listing"

    # Cutshort: e.g. cutshort.io
    if _matches_domain(netloc, "cutshort.io"):
        # Individual job: /job/<slug>, Listing: /jobs/<slug> or /browse
        if path_lower.startswith("/job/") and not path_lower.startswith("/jobs/"):
            slug = path[5:].strip("/")
            cutshort_reserved = {"search", "browse", "all", "jobs", "explore", "categories"}
            if slug and slug.lower() not in cutshort_reserved and len(slug) >= 5:
                return True, "individual_posting"
        return False, "cutshort_listing"

    # Glassdoor: e.g. glassdoor.com, glassdoor.co.in
    if "glassdoor." in netloc:
        has_job_listing_term = "job-listing" in path_lower or "joblisting" in path_lower
        is_listing = (
            "srch_" in path_lower
            or path.startswith("/Job/")
            or path.startswith("/Jobs/")
            or not has_job_listing_term
        )
        if not is_listing:
            clean_p = path_lower.strip("/")
            if clean_p not in ("job-listing", "joblisting", "job-listings", "joblistings"):
                slug = clean_p.split("job-listing")[-1].split("joblisting")[-1].strip("/-")
                if len(slug) >= 5:
                    return True, "individual_posting"
        return False, "glassdoor_listing"

    # Internshala: e.g. internshala.com
    if _matches_domain(netloc, "internshala.com"):
        if path_lower.startswith("/job/detail/"):
            slug = path_lower[len("/job/detail/"):].strip("/")
            if slug and len(slug) >= 4 and slug not in ("search", "browse", "all"):
                return True, "individual_posting"
        elif path_lower.startswith("/internship/detail/"):
            slug = path_lower[len("/internship/detail/"):].strip("/")
            if slug and len(slug) >= 4 and slug not in ("search", "browse", "all"):
                return True, "individual_posting"
        return False, "internshala_listing"

    # Instahyre: e.g. instahyre.com
    if _matches_domain(netloc, "instahyre.com"):
        if (path_lower.startswith("/job/") or path_lower.startswith("/job-")) and re.search(r'/job[-/]\d+', path_lower):
            return True, "individual_posting"
        return False, "instahyre_listing"

    # Wellfound / AngelList: e.g. wellfound.com, angel.co
    if _matches_domain(netloc, "wellfound.com") or _matches_domain(netloc, "angel.co"):
        if "/role/" in path_lower or path_lower in ("/jobs", "/jobs/", "/discover"):
            return False, "wellfound_listing"
        has_job_id = bool(re.search(r'/(?:jobs|company/[^/]+/jobs)/\d+', path_lower))
        if has_job_id:
            return True, "individual_posting"
        return False, "wellfound_listing"

    # SimplyHired: e.g. simplyhired.com, simplyhired.co.in
    if "simplyhired." in netloc:
        if path_lower.startswith("/job/"):
            slug = path_lower[5:].strip("/")
            simplyhired_reserved = {"search", "browse", "all", "jobs", "explore"}
            if slug and slug not in simplyhired_reserved and len(slug) >= 4:
                return True, "individual_posting"
        return False, "simplyhired_listing"

    # -------------------------------------------------------------------------
    # 3. Known ATS Platforms (High confidence individual jobs)
    # -------------------------------------------------------------------------
    if _matches_domain(netloc, "greenhouse.io"):
        if ("/jobs/" in path_lower and re.search(r'/jobs/\d+', path_lower)) or "gh_jid=" in query:
            return True, "individual_posting"
        return False, "greenhouse_listing"
    elif _matches_domain(netloc, "lever.co"):
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            lever_dir_keywords = {
                "departments", "teams", "locations", "all", "jobs", "culture",
                "about", "login", "search", "filter", "privacy", "terms", "faq",
            }
            job_segment = parts[1].lower()
            if job_segment not in lever_dir_keywords and (re.search(r'[0-9a-fA-F-]{8,}', job_segment) or len(job_segment) >= 8):
                return True, "individual_posting"
        return False, "lever_listing"
    elif _matches_domain(netloc, "ashbyhq.com"):
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            ashby_dir_keywords = {
                "departments", "teams", "locations", "all", "jobs", "culture",
                "about", "login", "search", "filter", "privacy", "terms",
            }
            job_segment = parts[1].lower()
            if job_segment not in ashby_dir_keywords and (re.search(r'[0-9a-fA-F-]{16,}', job_segment) or len(job_segment) >= 12):
                return True, "individual_posting"
        return False, "ashby_listing"
    elif "myworkdayjobs.com" in netloc:
        if "/job/" in path_lower:
            after_job = path_lower.split("/job/", 1)[1].strip("/")
            leaf = after_job.split("/")[-1] if after_job else ""
            workday_reserved = {"", "search", "browse", "all", "jobs", "explore", "filter"}
            if leaf and leaf not in workday_reserved and (re.search(r'[\d_-]', leaf) or len(leaf) >= 5):
                return True, "individual_posting"
        return False, "workday_listing"

    # -------------------------------------------------------------------------
    # 4. Generic Heuristics for Other Hosts
    # -------------------------------------------------------------------------
    # Check query params for search terms
    if query:
        query_params = urllib.parse.parse_qs(query)
        if any(param in GENERIC_SEARCH_PARAMS for param in query_params):
            return False, "search_query_url"

    # Check root or generic directory path
    if path_lower.rstrip("/") in {p.rstrip("/") for p in GENERIC_ROOT_PATHS}:
        return False, "generic_directory_root"

    # Check path segments for search/category keywords
    segments = [s for s in path_lower.strip("/").split("/") if s]
    if any(s in AGGREGATE_PATH_SEGMENTS for s in segments):
        return False, "listing_directory_path"

    # Check for listing suffixes e.g. 'python-jobs', 'jobs-in-bangalore', '-jobs.html'
    if segments:
        last_seg = segments[-1]
        if (
            last_seg.endswith("-jobs")
            or last_seg.endswith("-jobs.html")
            or last_seg.endswith("-vacancies")
            or last_seg.endswith("-openings")
            or "-jobs-in-" in last_seg
        ):
            return False, "listing_directory_path"

    # Path structure analysis: single job postings typically reside under /job/, /jobs/, /careers/
    # followed by an explicit slug or identifier.
    # Ambiguous paths (e.g. single short segment without job context) fail closed per design decision.
    posting_indicators = {
        "job", "jobs", "career", "careers", "position", "positions",
        "posting", "postings", "vacancy", "vacancies", "opening", "openings",
    }
    has_posting_context = any(s in posting_indicators for s in segments[:-1])

    if has_posting_context:
        leaf = segments[-1]
        # Any numeric ID (e.g. 456, 1234) or slug with digits/delimiters
        if leaf.isdigit() or (len(leaf) >= 3 and (re.search(r'\d+', leaf) or "-" in leaf or "_" in leaf)):
            return True, "individual_posting"
        return False, "ambiguous_pattern"

    if len(segments) == 1:
        leaf = segments[0]
        # Check if single segment explicitly denotes a specific job posting
        # e.g. job_123, job-title-123, firecrawl_test_job_1
        is_explicit_job = bool(
            re.search(r'(?:^|[-_])job[-_\d]', leaf)
            or re.search(r'(?:^|[-_])career[-_\d]', leaf)
            or (re.search(r'[-_]job(?:[-_\d]|$)', leaf) and re.search(r'\d+', leaf))
        )
        if is_explicit_job:
            return True, "individual_posting"
        return False, "ambiguous_pattern"

    # If URL path has multiple segments with slugs and identifiers
    leaf = segments[-1]
    if len(leaf) >= 12 and ("-" in leaf or "_" in leaf) and re.search(r'\d+', leaf):
        return True, "individual_posting"

    # Fail closed on ambiguous patterns
    return False, "ambiguous_pattern"
