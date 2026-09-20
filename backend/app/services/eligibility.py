import re

def is_fresher_eligible(title: str, description: str) -> bool:
    """
    Deterministic experience-eligibility gate.
    Eligible jobs must explicitly fit 0-1 years OR clearly indicate Fresher/Entry Level/Graduate/No Experience.
    Rejects explicit requirements above 1 year, ranges whose upper bound exceeds 1, and seniority signals.
    Ambiguous experience does not receive an unconditional pass.
    """
    title = (title or "").lower()
    desc = (description or "").lower()
    combined = f"{title} {desc}"

    # 1. Reject explicitly senior titles
    seniority_signals = [
        r"\bsenior\b", r"\bsr\.?\b", r"\bprincipal\b", r"\bstaff\b",
        r"\blead\b", r"\bmanager\b", r"\barchitect\b", r"\bhead\b",
        r"\bdirector\b", r"\bvp\b"
    ]
    for sig in seniority_signals:
        if re.search(sig, title):
            return False

    # 2. Extract experience ranges/requirements
    patterns = [
        # Any range: "2-4 years", "1 to 3 yrs"
        r"\b(\d+)\s*(?:-|to|–)\s*(\d+)\s*(?:years?|yrs?)\b",
        # Any plus: "3+ years", "5 + yrs", "12+ years"
        r"\b(\d+)\s*\+\s*(?:years?|yrs?)\b",
        # Context before: "minimum 3 years", "requires 2 yrs", "experience: 5 years"
        r"\b(?:minimum|min\.?|at least|requires?|required|preferred|experience|exp\.?)\s*(?:of\s*)?[:\-\s]*(\d+)\s*(?:years?|yrs?)\b",
        # Context after: "3 years experience", "2 years required", "5 years preferred"
        r"\b(\d+)\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp\b|required|preferred|minimum|min\b)",
    ]

    all_match_upper_bounds = []
    for p in patterns:
        for m in re.finditer(p, combined):
            min_val_str = m.group(1)
            max_val_str = m.group(2) if len(m.groups()) > 1 and m.group(2) else min_val_str
            try:
                max_val = int(max_val_str)
                all_match_upper_bounds.append(max_val)
            except ValueError:
                pass
    if all_match_upper_bounds:
        # If any explicit experience upper bound > 1, reject
        if any(v > 1 for v in all_match_upper_bounds):
            return False
        # If all mentions are <= 1, it passes (explicitly fits 0-1 years)
        if all(v <= 1 for v in all_match_upper_bounds):
            return True

    # 3. Internship-role recognition (title or structured role-context only)
    # Matches "Intern" or "Internship" as the role itself, not incidental mentions
    # like "mentoring interns" or "internship experience preferred".
    # Administrative/program-management titles are excluded even when they
    internship_role_pattern = r'\bintern(?:ship)?\b'
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

    def _is_internship_role(text: str) -> bool:
        """True when text names an internship position, not an admin role."""
        text = (text or "").lower()
        if not re.search(internship_role_pattern, text):
            return False
        if any(re.search(sig, text) for sig in seniority_signals):
            return False
        if any(re.search(sig, text) for sig in internship_admin_signals):
            return False
        return True

    if _is_internship_role(title):
        return True
    # Check structured role-context lines in description: "Title:", "Position:", "Role:"
    # Treat these as secondary titles: apply seniority and admin rejection before accepting.
    role_context_pattern = r'(?:title|position|role)\s*:\s*([^\n]{0,120})'
    for m in re.finditer(role_context_pattern, desc):
        if _is_internship_role(m.group(1)):
            return True

    # 4. If no explicit years mentioned, check for fresher keywords in title or description
    fresher_signals = [
        r"\bfresher\b", r"\bentry[- ]level\b", r"\bgraduate\b",
        r"\bno experience\b", r"\bjunior\b", r"\bjr\.?\b"
    ]
    for sig in fresher_signals:
        if re.search(sig, combined):
            return True

    # 5. Ambiguous experience -> Reject
    return False
