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

    # 3. If no explicit years mentioned, check for fresher keywords in title or description
    fresher_signals = [
        r"\bfresher\b", r"\bentry[- ]level\b", r"\bgraduate\b",
        r"\bno experience\b", r"\bjunior\b", r"\bjr\.?\b"
    ]
    for sig in fresher_signals:
        if re.search(sig, combined):
            return True

    # 4. Ambiguous experience -> Reject
    return False
