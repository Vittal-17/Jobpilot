import re

# Seniority signals. Evaluated ONLY against role context (the advertised title
# and structured "Title:/Position:/Role:" values), never against description
# prose, where words like "lead" or "manager" routinely describe colleagues,
# teams, or responsibilities rather than the advertised position.
seniority_signals = [
    r"\bsenior\b", r"\bsr\.?\b", r"\bprincipal\b", r"\bstaff\b",
    r"\blead\b", r"\bmanager\b", r"\barchitect\b", r"\bhead\b",
    r"\bdirector\b", r"\bvp\b"
]

# Explicit numeric experience requirements. Authoritative wherever they appear,
# including description prose, because they state what the position requires.
experience_patterns = [
    # Any range: "2-4 years", "1 to 3 yrs", "3 – 4 years"
    r"\b(\d+)\s*(?:-|to|–|—)\s*(\d+)\s*(?:years?|yrs?)\b",
    # Any plus: "3+ years", "5 + yrs", "12+ years"
    r"\b(\d+)\s*\+\s*(?:years?|yrs?)\b",
    # Context before: "minimum 3 years", "requires 2 yrs", "experience: 5 years"
    r"\b(?:minimum|min\.?|at least|requires?|required|preferred|experience|exp\.?)\s*(?:of\s*)?[:\-\s]*(\d+)\s*(?:years?|yrs?)\b",
    # Context after: "3 years experience", "2 years required", "5 years preferred"
    r"\b(\d+)\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp\b|required|preferred|minimum|min\b)",
]

# Role-level fresher signals. Restricted to role context for the same reason as
# seniority: "mentor junior developers" is a responsibility, not a role level.
# Note: "graduate" is evaluated separately by _is_graduate_role to ensure it
# clearly describes the target job level/role rather than generic education
# or audience wording ("Any Graduate", "Graduate Recruitment").
fresher_role_signals = [
    r"\bfreshers?\b", r"\bentry[- ]level\b",
    r"\bjunior\b", r"\bjr\.?\b", r"\btrainees?\b"
]

# Legitimate graduate job levels/roles where "graduate" names the target role/level.
graduate_role_patterns = [
    # "Graduate" modifying an engineering/tech/analyst/trainee/associate role noun
    r'\bgraduate\s+(?:[a-z\/\-]+\s+){0,3}(?:engineers?|developers?|programmers?|analysts?|associates?|consultants?|trainees?|apprentices?|scientists?|designers?|testers?|technicians?|coders?)\b',
    # Role noun followed by "graduate", e.g. "Software Engineer Graduate", "Developer Graduate"
    r'\b(?:[a-z\/\-]+\s+){0,2}(?:engineers?|developers?|programmers?|analysts?|associates?|trainees?)\s+graduates?\b',
    # Graduate program / scheme explicitly specifying a target role, e.g. "Graduate Program - Software Engineer"
    r'\bgraduate\s+(?:program|programme|scheme|track)\s*[-–:]\s*(?:[a-z\/\-]+\s+){0,2}(?:engineers?|developers?|programmers?|analysts?|associates?|trainees?)\b',
]

# Disallow generic education / audience wording and administrative graduate-facing roles
graduate_admin_signals = [
    # Generic audience or education qualifications
    r'\bany\s+graduate\b',
    r'\b(?:all|post)\s+graduates?\b',
    # Standalone programs / recruitment without a role
    r'\bgraduate\s+(?:hiring\s+|recruitment\s+)?(?:program|programme|scheme)\s*(?:[-–:]\s*\d{4})?\s*(?:\(.*?\)\s*)?$',
    r'\bgraduate\s+(?:hiring|recruitment|intake|cohort|campaign)\b',
    # Administrative, recruitment, HR, or admissions roles
    r'\bgraduate\s+(?:recruitment|program|programme|scheme|admissions?|campus|talent|careers?)?\s*(?:recruiter|coordinator|administrator|specialist|officer|advisor|lead|manager|director|head)\b',
    r'\bgraduate\s+(?:recruitment\s+consultant|talent\s+acquisition|admissions?)\b',
    r'\b(?:coordinator|administrator|advisor|recruiter)\s*[-–:,]?\s*(?:for\s+|of\s+)?graduates?\b',
    r'\b(?:coordinator|administrator|advisor|recruiter)\s*[-–:,]\s*graduates?\b',
]

trainee_admin_signals = [
    r'\btrainee\s+(?:program\s+|operations?\s+)?(?:coordinator|administrator|specialist|recruiter)\b',
    r'\b(?:coordinator|administrator|recruiter)\s*[-–:,]?\s*(?:for\s+|of\s+)?trainees?\b',
    r'\b(?:coordinator|administrator|recruiter)\s*[-–:,]\s*trainees?\b',
]

# Clause and boundary patterns for numeric experience classification
_clause_boundary_re = re.compile(
    r'(?:[\r\n;•*|]|\.\s+|\!\s+|\?\s+|(?:,\s*|\s+)and\s+|(?:,\s*|\s+)but\s+|(?:,\s*|\s+)while\s+)'
)

_company_employer_patterns = [
    # Company / employer subject having/bringing experience
    r'\b(?:our|the|this)\s+(?:company|firm|agency|organization|org|startup|business|enterprise|practice|group)\s+(?:has|have|brings?|boasts?|possesses?|accumulated)\b',
    r'\b(?:a|the)\s+(?:company|firm|agency|organization|startup|business)\s+with\b',
    r'\bwe\s+(?:have|bring|boast|possess)\s+(?:over\s+|more than\s+|almost\s+|nearly\s+|about\s+|approximately\s+)?(?:combined\s+|collective\s+|industry\s+|company\s+)?\b',
    r'\bwe(?:[\'’]ve|\s+have)\s+been\s+(?:around|operating|serving|providing|delivering|in\s+business|in\s+(?:the\s+)?(?:market|industry))\b',
    # Team / leadership / staff
    r'\b(?:our|the)\s+(?:team|leadership|founders|staff|consultants|practice|engineers)\s+(?:has|have|brings?|boasts?|with)\b',
    r'\ba\s+team\s+with\b',
    r'\b(?:combined|collective)\s+experience\b',
    r'\bexperience\s+of\s+(?:combined|collective)\b',
    # Serving clients / customers
    r'\b(?:serving|helping|supporting|partnering\s+with|trusted\s+by)\s+(?:our\s+|the\s+)?(?:clients?|customers?|patrons?|users?)\s+for\b',
    r'\bfor\s+(?:our\s+|the\s+)?(?:clients?|customers?)\s+for\b',
    r'\b(?:our|the)\s+clients?\s+(?:has|have|with)\b',
    # History / longevity / track record / in business / founded
    r'\b(?:history|track\s*record)\s+of\s+(?:over\s+|more than\s+)?\b',
    r'\b(?:years?|yrs?)\s+(?:of\s+)?(?:history|track\s*record|excellence|innovation|success|growth|leadership|service|standing|presence|existence|heritage|legacy)\b',
    r'\bin\s+business\s+for\b',
    r'\bin\s+(?:the\s+)?market\s+for\b',
    r'\b(?:years?|yrs?)\s+in\s+business\b',
    r'\b(?:years?|yrs?)\s+in\s+(?:the\s+)?market\b',
    r'\b(?:founded|established|operating|operating\s+for|in\s+operation\s+for)\s+(?:over\s+|more than\s+)?\b',
    r'\b(?:years?|yrs?)\s+ago\b',
    # Platform / product history
    r'\b(?:our|the)\s+(?:platform|product|solution|software|service)\s+(?:has\s+been\s+around\s+for|has)\b',
    # Introductory "With X years of experience, [Company] / we..."
    r'\bwith\s+(?:over\s+|more than\s+)?\d+[^\.\r\n;]*?(?:years?|yrs?)[^\.\r\n;]*?,\s*(?:we|our|the\s+company|[a-z]+\s+(?:is|has|was|provides|delivers|builds))\b',
]

_candidate_governing_signals = [
    r'\b(?:looking\s+for|seeking|hiring)\s+(?:an?\s+)?(?:candidate|applicant|developer|engineer|individual|someone|person|talent)?\s*(?:with|having)\b',
    r'\b(?:require[sd]?|requiring|requirements?)\b',
    r'\b(?:minimum|min\.?|at\s+least)\b',
    r'\b(?:preferred|plus|desired|needed|mandatory|expected)\b',
    r'\b(?:must\s+have|should\s+have|needs?\s+to\s+have|need\s+have|must\s+possess)\b',
    r'\b(?:candidates?|applicants?)\s+(?:must|should|need|with|have|to\s+have)\b',
    r'\b(?:candidates?|applicants?)\b',
    r'\b(?:ideal\s+candidate|successful\s+candidate|qualifications?)\b',
    r'\byou\s+(?:must|should|have|will\s+have|bring|possess)\b',
    r'\bfor\s+(?:candidates?|applicants?|this\s+role|the\s+role|this\s+position|the\s+position|this\s+job|the\s+job)\b',
]

# Explicit declarations about the experience the POSITION accepts. Unlike the
# bare role words above, these are unambiguous statements addressed to the
# applicant, so they are honoured wherever they appear. Must be whole-candidate
# statements, not skill- or domain-specific lack of experience.
_allowed_for_target = (
    r"(?:(?:this|the|our|an?|any)\s+)?(?:new\s+|open\s+|entry[- ]level\s+)?"
    r"(?:roles?|positions?|jobs?|opportunit(?:y|ies)|vacanc(?:y|ies)|posts?|"
    r"freshers?|beginners?|graduates?|candidates?|applicants?|entry[- ]level|juniors?)\b"
)
_safe_boundary = r"(?:\s*[\.,;:\!\?\-\–\—\(\)\[\]\"'/]|\s*[\r\n]|\s*$)"
_skill_disallowed = (
    rf"(?:with\b|in\b(?! order\b)|on\b|using\b|for\b\s+(?!{_allowed_for_target}{_safe_boundary}))"
)
zero_experience_signals = [
    rf"\bno\s+(?:prior\s+|previous\s+|relevant\s+|work\s+|professional\s+)?experience\s+(?:is\s+)?(?:strictly\s+)?(?:required|necessary|needed|expected|mandatory)\b(?!\s+{_skill_disallowed})",
    rf"\brequires?\s+no\s+(?:prior\s+|previous\s+|relevant\s+|work\s+|professional\s+)?experience\b(?!\s+{_skill_disallowed})",
    r"\bfreshers?\b\s*(?:are\s+|is\s+)?(?:also\s+)?(?:welcome|eligible|encouraged)\b",
    r"\bfreshers?\b\s*(?:can|may|are\s+welcome\s+to|are\s+encouraged\s+to)\s+apply\b",
    r"\bopen\s+to\s+freshers?\b",
    r"\bhiring\s+freshers?\b",
]

# Structured role-context lines inside a description, e.g. "Position: Backend
# Developer Intern". Treated as secondary titles.
role_context_pattern = r'(?:title|position|role)\s*:\s*([^\n]{0,120})'

# Matches "Intern" or "Internship" as the role itself, not incidental mentions
# like "mentoring interns" or "internship experience preferred".
internship_role_pattern = r'\bintern(?:ship)?\b'

# Administrative/program-management titles that contain "intern" but are not
# themselves internship positions.
internship_admin_signals = [
    r'\bintern(?:ship)?\s+(?:program\s+|operations?\s+)?(?:coordinator|administrator|specialist)\b',
    r'\bintern(?:ship)?\s+program\s+(?:manager|lead|director|head|officer)\b',
    r'\bintern(?:ship)?\s+program\s*(?:[-–:]\s*\d{4})?\s*(?:\(.*?\)\s*)?$',
    r'\bintern(?:ship)?\s+program\s+(?:for|of)\b',
    r'\bintern(?:ship)?\s+(?:recruitment|support)\b',
    r'\bintern(?:ship)?\s+operations?\s+(?:specialist|lead|manager|director|head|officer)\b',
    r'\b(?:coordinator|administrator)\s*[-–:,]?\s*(?:for\s+|of\s+)intern(?:ship)?\b',
    r'\b(?:coordinator|administrator)\s*[-–:,]\s*intern(?:ship)?\b',
]


def _role_texts(title: str, desc: str) -> list[str]:
    """Returns every text that names the advertised position.

    That is the title plus any structured "Title:/Position:/Role:" value found
    in the description. Free-form prose is deliberately excluded: it describes
    the team, the responsibilities and the company, not the vacancy.
    """
    texts = [title]
    texts.extend(m.group(1) for m in re.finditer(role_context_pattern, desc))
    return texts


def _has_seniority(text: str) -> bool:
    return any(re.search(sig, text) for sig in seniority_signals)


def _is_internship_role(text: str) -> bool:
    """True when text names an internship position, not an admin role."""
    text = (text or "").lower()
    if not re.search(internship_role_pattern, text):
        return False
    if _has_seniority(text):
        return False
    if any(re.search(sig, text) for sig in internship_admin_signals):
        return False
    return True


def _is_graduate_role(text: str) -> bool:
    """True when text names a legitimate graduate position, not generic audience or admin."""
    text = (text or "").lower()
    if not re.search(r'\bgraduates?\b', text):
        return False
    if any(re.search(sig, text) for sig in graduate_admin_signals):
        return False
    return any(re.search(sig, text) for sig in graduate_role_patterns)


def _is_fresher_role(text: str) -> bool:
    """True when text names a fresher/junior/trainee/graduate position."""
    text = (text or "").lower()
    if _has_seniority(text):
        return False
    for sig in fresher_role_signals:
        if re.search(sig, text):
            if re.search(r'\btrainees?\b', text) and any(re.search(a, text) for a in trainee_admin_signals):
                continue
            return True
    if _is_graduate_role(text):
        return True
    return False


def _is_company_or_history_prose(text: str, match_start: int, match_end: int) -> bool:
    """True when the numeric experience match describes the employer, team, client,
    or company history rather than a requirement demanded of the applicant.
    """
    # 1. Extract the enclosing clause context around the match
    window_start = max(0, match_start - 160)
    pre_window = text[window_start:match_start]
    splits = list(_clause_boundary_re.finditer(pre_window))
    pre_clause = pre_window[splits[-1].end():] if splits else pre_window

    window_end = min(len(text), match_end + 160)
    post_window = text[match_end:window_end]
    split_post = _clause_boundary_re.search(post_window)
    post_clause = post_window[:split_post.start()] if split_post else post_window

    match_str = text[match_start:match_end]
    clause = f"{pre_clause} {match_str} {post_clause}"

    # 2. Check if the match itself is directly bound to history/longevity prose
    is_direct_history_match = bool(
        re.search(
            r'^\s*(?:in\s+business|in\s+(?:the\s+)?market|of\s+(?:history|track\s*record|excellence|innovation|success|growth|leadership|service|standing|presence|existence|heritage|legacy)|ago\b)',
            post_clause
        )
        or re.search(
            r'\b(?:history\s+of|track\s*record\s+of|in\s+business\s+for|in\s+(?:the\s+)?market\s+for|serving\s+(?:clients?|customers?)\s+for|founded|established)\s+(?:over\s+|more than\s+)?$',
            pre_clause
        )
    )

    # 3. Check for candidate-governing signals across the match and its relevant clause context
    has_candidate_signal = (
        any(re.search(p, match_str) for p in _candidate_governing_signals)
        or any(re.search(p, pre_clause) for p in _candidate_governing_signals)
        or any(re.search(p, post_clause) for p in _candidate_governing_signals)
    )

    # Candidate-governing signals override company/history classification for non-history matches
    if has_candidate_signal and not is_direct_history_match:
        return False

    # 4. Check if the clause has company/team/client/history patterns
    has_company_signal = is_direct_history_match or any(re.search(p, clause) for p in _company_employer_patterns)
    if not has_company_signal:
        return False

    return True


def _experience_upper_bounds(combined: str) -> list[int]:
    """Extracts the upper bound of every explicit candidate experience requirement."""
    text = (combined or "").lower()
    bounds = []
    for p in experience_patterns:
        for m in re.finditer(p, text):
            if _is_company_or_history_prose(text, m.start(), m.end()):
                continue
            min_val_str = m.group(1)
            max_val_str = m.group(2) if len(m.groups()) > 1 and m.group(2) else min_val_str
            try:
                bounds.append(int(max_val_str))
            except ValueError:
                pass
    return bounds


def is_fresher_eligible(title: str, description: str, is_snippet: bool = True) -> bool:
    """
    Deterministic experience-eligibility gate.

    Default provenance is fail-closed (is_snippet=True), meaning description-derived
    positive evidence is insufficient unless the caller explicitly attests that
    the text is an authoritative full description (is_snippet=False).

    Decision precedence, highest first:

    1. Role-level seniority in the title or a structured role-context line
       rejects outright.
    2. An explicit numeric experience requirement anywhere in the posting is
       authoritative: any upper bound above 1 year rejects, and a requirement
       wholly within 0-1 years accepts (unless the evidence is a snippet, in which
       case we cannot assume a >1 year requirement wasn't truncated, so we fall through).
    3. A role-level fresher or internship signal accepts, but only when it
       appears in the title or a structured role-context line (title-only when is_snippet=True).
    4. An explicit declaration that the position takes no experience accepts
       (title-only when is_snippet=True).
    5. Anything else is ambiguous and fails closed.
    """
    title = (title or "").lower()
    desc = (description or "").lower()
    combined = f"{title} {desc}"

    role_texts = _role_texts(title, desc)

    # 1. Reject explicitly senior roles
    for role_text in role_texts:
        if _has_seniority(role_text):
            return False

    # 2. Explicit experience requirements override every positive signal
    bounds = _experience_upper_bounds(combined)
    if bounds:
        if any(v > 1 for v in bounds):
            return False
        # Every mention fits 0-1 years.
        # If it is a snippet, description-only evidence is insufficient.
        if not is_snippet or _experience_upper_bounds(title):
            return True
        # Fall through if the <=1 year requirement was only found in the truncated snippet.

    # 3. Role-level fresher/internship recognition, role context only
    for role_text in role_texts:
        if _is_internship_role(role_text) or _is_fresher_role(role_text):
            # If it is a snippet, positive evidence derived from the description is insufficient.
            # We only accept it if the signal is present in the actual authoritative title.
            if not is_snippet or role_text == title:
                return True

    # 4. Explicit "no experience needed" declarations
    for sig in zero_experience_signals:
        if re.search(sig, combined):
            # If it is a snippet, description-only positive evidence is insufficient.
            if not is_snippet or re.search(sig, title):
                return True

    # 5. Ambiguous experience -> Reject
    return False
