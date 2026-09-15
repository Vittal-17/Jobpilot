import re
from app.schemas.match import RecommendationPreferences, MatchResult, MatchReason
from app.schemas.job import JobResponse

def calculate_match(job: JobResponse, prefs: RecommendationPreferences) -> MatchResult:
    """
    Deterministically calculate a match score (0-100) and provide reasons.
    This function evaluates a single job against preferences.
    It does not perform ranking or tie-breaking across collections.
    """
    score = 0
    max_score = 0
    reasons = []

    # 1. Role/Title (Weight: 30)
    if prefs.preferred_roles:
        max_score += 30
        job_title = (job.title or "").lower()
        roles = [r.strip().lower() for r in prefs.preferred_roles.split(",") if r.strip()]

        # Token-based match to avoid false positive substring matches
        if any(re.search(rf"\b{re.escape(role)}\b", job_title) for role in roles if role):
            score += 30
            reasons.append(MatchReason(code="ROLE_MATCH", message="Job title matches preferred roles"))
        else:
            reasons.append(MatchReason(code="ROLE_MISMATCH", message="Job title does not match preferred roles"))

    # 2. Skills (Weight: 30)
    if prefs.skills:
        max_score += 30
        job_text = f"{job.title or ''} {job.description or ''}".lower()
        skills = [s.strip().lower() for s in prefs.skills.split(",") if s.strip()]
        if skills:
            # Token-based match to avoid false positive substring matches (e.g. java in javascript)
            matched = [s for s in skills if re.search(rf"\b{re.escape(s)}\b", job_text)]
            ratio = len(matched) / len(skills)
            pts = int(30 * ratio)
            score += pts
            if pts > 0:
                reasons.append(MatchReason(code="SKILLS_MATCH", message=f"Matched {len(matched)} of {len(skills)} skills"))
            else:
                reasons.append(MatchReason(code="SKILLS_MISMATCH", message="No preferred skills found in job description"))

    # 3. Location (Weight: 20)
    if prefs.preferred_locations:
        max_score += 20
        job_loc = (job.location or "").lower()
        locs = [l.strip().lower() for l in prefs.preferred_locations.split(",") if l.strip()]
        if job_loc and any(l in job_loc for l in locs if l):
            score += 20
            reasons.append(MatchReason(code="LOCATION_MATCH", message="Job location matches preferred locations"))
        else:
            reasons.append(MatchReason(code="LOCATION_MISMATCH", message="Job location does not match preferred locations"))

    # 4. Remote (Weight: 20)
    if prefs.remote_preference:
        pref_remote = prefs.remote_preference.strip().lower()
        # Only evaluate explicit "remote" or "onsite" due to boolean job.remote constraint.
        # Other values like "hybrid" cannot be strictly verified, so they do not increase max_score.
        if pref_remote in ["remote", "onsite"]:
            max_score += 20
            if pref_remote == "remote" and job.remote is True:
                score += 20
                reasons.append(MatchReason(code="REMOTE_MATCH", message="Job offers preferred remote option"))
            elif pref_remote == "onsite" and job.remote is False:
                score += 20
                reasons.append(MatchReason(code="REMOTE_MATCH", message="Job offers preferred onsite option"))
            elif job.remote is None:
                score += 10
                reasons.append(MatchReason(code="REMOTE_UNKNOWN", message="Job remote policy is unspecified"))
            else:
                reasons.append(MatchReason(code="REMOTE_MISMATCH", message="Job remote policy does not match preference"))
        else:
            reasons.append(MatchReason(code="REMOTE_UNSUPPORTED", message="Remote preference not fully supported by binary job remote data"))

    # 5. Experience (Weight: 20)
    # Documented as a deterministic title-based heuristic
    if prefs.experience_years is not None:
        max_score += 20
        title = (job.title or "").lower()
        is_senior = any(re.search(rf"\b{w}\b", title) for w in ["senior", "lead", "principal", "manager", "staff"])
        is_junior = any(re.search(rf"\b{w}\b", title) for w in ["junior", "entry", "associate", "intern", "graduate"])

        if prefs.experience_years >= 5:
            if is_senior:
                score += 20
                reasons.append(MatchReason(code="EXPERIENCE_MATCH", message="Senior job matches high experience level"))
            elif is_junior:
                reasons.append(MatchReason(code="EXPERIENCE_MISMATCH", message="Junior job may not match high experience level"))
            else:
                score += 10
                reasons.append(MatchReason(code="EXPERIENCE_NEUTRAL", message="No explicit experience level in job title"))
        elif prefs.experience_years < 3:
            if is_junior:
                score += 20
                reasons.append(MatchReason(code="EXPERIENCE_MATCH", message="Junior job matches early career level"))
            elif is_senior:
                reasons.append(MatchReason(code="EXPERIENCE_MISMATCH", message="Senior job may require more experience"))
            else:
                score += 10
                reasons.append(MatchReason(code="EXPERIENCE_NEUTRAL", message="No explicit experience level in job title"))
        else: # 3 to 4 years
            if is_senior or is_junior:
                score += 10
                reasons.append(MatchReason(code="EXPERIENCE_PARTIAL", message="Job experience level differs slightly from profile"))
            else:
                score += 20
                reasons.append(MatchReason(code="EXPERIENCE_MATCH", message="Mid-level job matches experience level"))

    # Normalization
    if max_score == 0:
        reasons.insert(0, MatchReason(code="NO_PREFERENCES", message="No actionable preferences provided, default neutral score"))
        return MatchResult(job_id=job.id, score=50, reasons=reasons)

    final_score = int((score / max_score) * 100)

    # Absolute bounds constraint
    final_score = max(0, min(100, final_score))

    return MatchResult(job_id=job.id, score=final_score, reasons=reasons)
