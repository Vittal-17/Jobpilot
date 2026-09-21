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
# seniority: "Any Graduate" is an education qualification, "mentor junior
# developers" is a responsibility, and neither says the advertised position is
# open to freshers.
fresher_role_signals = [
    r"\bfreshers?\b", r"\bentry[- ]level\b", r"\bgraduate\b",
    r"\bjunior\b", r"\bjr\.?\b"
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


def _is_fresher_role(text: str) -> bool:
    """True when text names a fresher/junior/graduate position."""
    return any(re.search(sig, text) for sig in fresher_role_signals)


def _experience_upper_bounds(combined: str) -> list[int]:
    """Extracts the upper bound of every explicit experience requirement."""
    bounds = []
    for p in experience_patterns:
        for m in re.finditer(p, combined):
            min_val_str = m.group(1)
            max_val_str = m.group(2) if len(m.groups()) > 1 and m.group(2) else min_val_str
            try:
                bounds.append(int(max_val_str))
            except ValueError:
                pass
    return bounds


def is_fresher_eligible(title: str, description: str) -> bool:
    """
    Deterministic experience-eligibility gate.

    Decision precedence, highest first:

    1. Role-level seniority in the title or a structured role-context line
       rejects outright.
    2. An explicit numeric experience requirement anywhere in the posting is
       authoritative: any upper bound above 1 year rejects, and a requirement
       wholly within 0-1 years accepts.
    3. A role-level fresher or internship signal accepts, but only when it
       appears in the title or a structured role-context line.
    4. An explicit declaration that the position takes no experience accepts.
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
        # Every mention fits 0-1 years
        return True

    # 3. Role-level fresher/internship recognition, role context only
    for role_text in role_texts:
        if _is_internship_role(role_text) or _is_fresher_role(role_text):
            return True

    # 4. Explicit "no experience needed" declarations
    for sig in zero_experience_signals:
        if re.search(sig, combined):
            return True

    # 5. Ambiguous experience -> Reject
    return False
