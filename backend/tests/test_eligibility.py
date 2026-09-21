from app.services.eligibility import is_fresher_eligible

def test_fresher_eligibility_cases():
    cases = [
        # Explicit <=1 year passes
        ("Software Engineer", "0 years of experience", True),
        ("Software Engineer", "1 year of experience", True),
        ("Software Engineer", "0-1 years of experience", True),
        ("Software Engineer", "1 year required", True),
        ("Software Engineer", "experience: 1 year", True),
        # Explicit >1 year rejects
        ("Software Engineer", "1-2 years of experience", False),
        ("Software Engineer", "2+ years of experience", False),
        ("Software Engineer", "12+ years required", False),
        ("Software Engineer", "minimum 3 years", False),
        ("Software Engineer", "at least 2 years", False),
        ("Software Engineer", "2-4 yrs", False),
        ("Software Engineer", "requires 2-4 yrs", False),
        ("Software Engineer", "3 years preferred", False),
        ("Software Engineer", "5 years required", False),
        ("Software Engineer", "experience: 2 years", False),

        # Contradictory text -> numeric >1 year takes precedence -> Rejects
        ("Software Engineer", "12+ years required but freshers welcome", False),
        ("Backend Dev", "minimum 3 years required, entry-level considered", False),

        # Fresher keywords with NO numeric experience passes
        ("Junior Software Engineer", "Looking for someone", True),
        ("Entry Level Software Engineer", "Welcome", True),
        ("Software Engineer Graduate", "Fresh out of college", True),
        ("Backend Dev", "No experience necessary", True),
        # A bare "fresher" token in the body is NOT accepted; an explicit
        # declaration addressed to the applicant is. See
        # test_role_context_vs_incidental_context for the adversarial pairs.
        ("Software Engineer", "Freshers can apply", True),

        # Ambiguous rejects
        ("Software Engineer", "Great opportunity", False),
        ("Software Engineer", "We have 10 years of history", False),
        # Seniority titles -> always reject
        ("Senior Software Engineer", "0-1 years of experience", False),
        ("Principal Engineer", "No experience required", False),
        ("Lead Developer", "1 year experience", False),
        ("Manager", "No experience required", False),
        ("Principal Software Engineer - AI", "Requires 12+ years of experience", False),
    ]
    for title, desc, expected in cases:
        assert is_fresher_eligible(title, desc) == expected, f"Failed: {title} | {desc} (Expected {expected})"


def test_production_derived_false_positives():
    """Regression: the experienced roles that leaked through in production.

    Observed as fresher-eligible on live Adzuna results. The years requirement
    is frequently truncated out of the provider description, so these must be
    rejected both with and without the numeric requirement present.
    """
    cases = [
        # "Python Developer" advertised at 3-4 years, every observed format
        ("Python Developer", "Experience: 3-4 years in Python and Django.", False),
        ("Python Developer", "Experience: 3 - 4 years", False),
        ("Python Developer", "3–4 years of experience required", False),
        ("Python Developer", "3-4 yrs experience", False),
        # "Python Developer" advertised at 6-7 years
        ("Python Developer", "6-7 years of hands-on experience", False),
        ("Python Developer", "Experience: 6 – 7 years", False),
        ("Python Developer", "6 to 7 years exp", False),
        # "Python Django Developer" at 2-6 years, with junior mentions in the
        # responsibilities. Neither the range nor the mentoring line may help.
        ("Python Django Developer", "Experience: 2-6 years. You will manage and mentor junior developers.", False),
        ("Python Django Developer", "2 to 6 years exp. Mentor junior developers.", False),
        # The exact truncated-description shape that reached production: the
        # range is gone and only the incidental "junior developers" survives.
        ("Python Django Developer", "You will mentor junior developers and own delivery.", False),
        # "Any Graduate" is an education qualification, not a fresher role
        ("Python Developer", "Any Graduate", False),
        ("Python Developer", "B.Tech Graduate in Computer Science", False),
    ]
    for title, desc, expected in cases:
        assert is_fresher_eligible(title, desc) == expected, f"Failed: {title} | {desc} (Expected {expected})"


def test_production_derived_internship_positive():
    """Regression: the legitimate internship role must stay eligible."""
    assert is_fresher_eligible(
        "Python Backend Developer Intern (FastAPI)", "Build APIs with FastAPI."
    ) is True


def test_role_context_vs_incidental_context():
    """Positive signals count only from the title or a Title:/Position:/Role: line.

    Each generic word is paired: incidental in prose (reject) versus naming the
    advertised position (accept).
    """
    cases = [
        # --- "junior" ---
        ("Backend Developer", "You will mentor junior developers", False),
        ("Backend Developer", "Manage junior developers across teams", False),
        ("Backend Developer", "mentor junior engineers on the team", False),
        ("Backend Developer", "Support junior team members", False),
        ("Junior Python Developer", "Join our backend team.", True),
        ("Jr. Backend Developer", "Work on payment services.", True),
        ("Jr Backend Developer", "Work on payment services.", True),
        ("Open Role", "Position: Junior Python Developer\nLocation: Pune", True),
        # --- "graduate" ---
        ("Backend Developer", "Collaborate with our graduate trainees", False),
        ("Backend Developer", "Our graduate hiring program runs annually", False),
        ("Software Engineer Graduate", "Fresh out of college", True),
        ("Hiring", "Role: Graduate Engineer\nTeam: Platform", True),
        # --- "fresher" ---
        ("Backend Developer", "Our fresher training program is well regarded", False),
        ("Backend Developer", "Mentoring freshers is preferred", False),
        ("Backend Developer", "You will onboard freshers each quarter", False),
        ("Fresher Software Engineer", "Apply now", True),
        ("Freshers Hiring - Python Developer", "Apply now", True),
        # --- "entry level" ---
        ("Backend Developer", "We run an entry-level bootcamp for new hires", False),
        ("Backend Developer", "This is not an entry level position", False),
        ("Entry Level Software Engineer", "Welcome", True),
        # --- "intern"/"internship" ---
        ("Software Engineer", "internship experience preferred", False),
        ("Software Engineer", "You will be mentoring interns", False),
        ("Software Engineer", "Our internship program is well-regarded", False),
        ("Python Backend Developer Intern", "Build APIs.", True),
        ("Open Position", "Title: Software Engineering Intern\nLocation: Bengaluru", True),
        # --- explicit applicant-facing declarations are honoured in prose ---
        ("Backend Dev", "No experience necessary", True),
        ("Backend Dev", "No experience required", True),
        ("Software Engineer", "Freshers can apply", True),
        ("Software Engineer", "Freshers may apply", True),
        ("Software Engineer", "Freshers are welcome", True),
        ("Software Engineer", "freshers welcome", True),
        ("Software Engineer", "Open to freshers", True),
        ("Software Engineer", "Hiring freshers for our Bengaluru office", True),
        # --- seniority words in prose must not reject a genuine junior role ---
        ("Junior Python Developer", "You will report to a senior architect and the team lead.", True),
        ("Software Engineering Intern", "You will work with the principal engineer and tech lead.", True),
    ]
    for title, desc, expected in cases:
        assert is_fresher_eligible(title, desc) == expected, f"Failed: {title} | {desc} (Expected {expected})"


def test_decision_precedence():
    """The four tiers resolve in a fixed order."""
    cases = [
        # Tier 1 (role-level seniority) beats an explicit <=1-year requirement
        ("Senior Python Developer", "0-1 years of experience", False),
        ("Hiring", "Position: Senior Python Developer\n0-1 years of experience", False),
        ("Principal Engineer", "No experience required", False),
        ("Engineering Manager", "Freshers can apply", False),
        # Tier 2 (numeric >1) beats every role-level and declaration signal
        ("Junior Python Developer", "Experience: 2-6 years", False),
        ("Software Engineering Intern", "3+ years of experience required", False),
        ("Graduate Engineer", "minimum 3 years", False),
        ("Software Engineer", "12+ years required but freshers welcome", False),
        # Tier 2 (numeric <=1) accepts on its own
        ("Software Engineer", "0-1 years of experience", True),
        # Tier 3 (role-level) accepts with no numeric evidence
        ("Junior Python Developer", "Join our backend team.", True),
        # Tier 4 (declaration) accepts with no numeric or role evidence
        ("Software Engineer", "Freshers can apply", True),
        # Tier 5: ambiguity fails closed
        ("Python Developer", "Build backend services with Django.", False),
        ("Software Engineer", "", False),
        ("", "", False),
        (None, None, False),
    ]
    for title, desc, expected in cases:
        assert is_fresher_eligible(title, desc) == expected, f"Failed: {title} | {desc} (Expected {expected})"

    """Regression: internship-role postings are fresher-eligible."""
    cases = [
        # --- Positive: title indicates internship role ---
        # Job 280 pattern
        ("Software Engineering Intern (Full Stack)", "Join our team", True),
        ("Frontend Developer Intern", "Work on React projects", True),
        ("Intern - Python Developer", "Build APIs", True),
        ("Software Engineering Internship", "6-month program", True),
        ("Data Science Intern", "Analyze datasets", True),
        ("Marketing Intern", "Social media campaigns", True),
        ("Operations Intern", "Support daily operations", True),
        ("Program Intern", "Assist with project execution", True),
        ("Operations Engineering Intern", "Help optimize operational systems", True),
        ("Project Coordinator Intern", "Support project management office", True),
        ("HR Coordinator Intern", "Coordinate interview schedules", True),
        ("Administrative Intern", "Assist office administration", True),
        ("Internship Program Intern", "Join our annual internship cohort", True),
        ("Internship Program - Software Engineer Intern", "Work with our full-stack team", True),
        ("Rotational Program Intern", "Rotate across engineering teams", True),

        # --- Positive: structured role-context in description ---
        ("Open Position", "Title: Software Engineering Intern\nLocation: Bengaluru", True),
        ("Hiring", "Position: Backend Developer Intern\nTeam: Platform", True),
        ("New Opening", "Role: Data Analyst Internship\nDuration: 6 months", True),

        # --- Negative: incidental internship mentions in body ---
        ("Software Engineer", "You will be mentoring interns", False),
        ("Software Engineer", "internship experience preferred", False),
        ("Engineering Manager", "managed interns across 3 teams", False),
        ("Software Engineer", "Our internship program is well-regarded", False),
        ("Software Engineer", "Previous intern projects included ML pipelines", False),

        # --- Negative: experience >1 year overrides internship title ---
        ("Software Engineering Intern", "3+ years of experience required", False),
        ("Backend Intern", "minimum 2 years experience", False),

        # --- Negative: seniority title overrides internship ---
        ("Senior Intern Coordinator", "Manage intern program", False),
        ("Lead Intern Manager", "Oversee interns", False),

        # --- Negative: administrative internship titles (not an intern position) ---
        ("Internship Coordinator", "Manage the intern program", False),
        ("Internship Program Manager", "Oversee all interns", False),
        ("Internship Program Coordinator", "Coordinate program events", False),
        ("Internship Program Administrator", "Administer internship records", False),
        ("Internship Program Specialist", "Manage program metrics", False),
        ("Internship Program", "Oversee our annual internship program", False),
        ("Intern Program Specialist", "Coordinate internship activities", False),
        ("Internship Administrator", "Handle intern onboarding", False),
        ("Internship Operations Specialist", "Run intern operations", False),
        ("Intern Operations Lead", "Lead intern ops", False),
        ("Internship Program for Intern Recruitment", "Drive university recruitment", False),
        ("Internship Program for Intern Support", "Provide pastoral care and support", False),
        ("Internship Program: Intern Recruitment Coordinator", "Coordinate intern hiring pipelines", False),

        # --- Negative: structured role-context with seniority ---
        ("Open Position", "Title: Senior Intern Coordinator\nLocation: Bengaluru", False),
        ("Hiring", "Position: Lead Intern Manager\nTeam: Platform", False),
        ("Hiring", "Role: Manager - Intern Program\nTeam: HR", False),

        # --- Negative: structured role-context with admin signals ---
        ("Hiring", "Title: Internship Coordinator\nLocation: Mumbai", False),
        ("Hiring", "Position: Internship Program Specialist\nTeam: HR", False),

        # --- Negative: experience >1 year overrides structured internship role ---
        ("Open Position", "Title: Software Engineering Intern\n3+ years required", False),
    ]
    for title, desc, expected in cases:
        assert is_fresher_eligible(title, desc) == expected, f"Failed: {title} | {desc} (Expected {expected})"


def test_skill_specific_vs_whole_candidate_zero_experience():
    """
    Regression: statements about lacking experience with a specific skill/domain
    must NOT count as evidence of zero overall professional experience.
    Explicit whole-candidate zero-experience declarations must remain eligible.
    """
    cases = [
        # --- Negative: Skill-specific zero-experience declarations (rejects) ---
        ("Software Engineer", "No experience with AWS is required", False),
        ("Software Engineer", "No prior experience with React is required", False),
        ("Software Engineer", "No experience in SQL is necessary", False),
        ("Backend Developer", "No experience on Kubernetes is needed", False),
        ("Frontend Developer", "No experience using Docker is required", False),
        ("Software Engineer", "No experience required with AWS", False),
        ("Software Engineer", "No experience is required with React", False),
        ("Software Engineer", "No experience is necessary in SQL", False),
        ("Software Engineer", "No experience needed on AWS", False),
        ("Software Engineer", "No prior experience required using Docker", False),
        ("Software Engineer", "Requires no prior experience with Python", False),
        ("Software Engineer", "Requires no experience in machine learning", False),
        ("Software Engineer", "No experience is required for AWS", False),
        ("Software Engineer", "No experience required for Docker", False),
        ("Software Engineer", "No experience is needed for Kubernetes", False),
        ("Backend Developer", "No experience necessary for React", False),
        ("Software Engineer", "Requires no experience for AWS", False),
        ("Software Engineer", "Requires no prior experience for Docker", False),
        ("Software Engineer", "No experience is required for the hiring process", False),
        ("Software Engineer", "No experience is required for employment", False),
        ("Software Engineer", "No experience is required for consideration", False),
        ("Software Engineer", "No experience is required for hiring", False),
        ("Software Engineer", "No experience is required for this role in AWS", False),
        ("Software Engineer", "No experience is required for an open position in AWS", False),
        ("Software Engineer", "No experience required for the job using Kubernetes", False),
        ("Software Engineer", "No experience required for applicants with Python experience", False),

        # --- Positive: Unambiguous whole-candidate zero-experience declarations (accepts) ---
        ("Software Engineer", "No prior experience is required", True),
        ("Software Engineer", "No prior experience required", True),
        ("Software Engineer", "No experience is required", True),
        ("Software Engineer", "No experience required", True),
        ("Software Engineer", "No experience necessary", True),
        ("Software Engineer", "No experience is necessary", True),
        ("Software Engineer", "No prior experience necessary", True),
        ("Software Engineer", "No prior experience is necessary", True),
        ("Software Engineer", "No experience needed", True),
        ("Software Engineer", "No experience is needed", True),
        ("Software Engineer", "No prior experience needed", True),
        ("Software Engineer", "No prior experience is needed", True),
        ("Software Engineer", "No previous experience required", True),
        ("Software Engineer", "No previous experience is required", True),
        ("Software Engineer", "No work experience required", True),
        ("Software Engineer", "No professional experience is required", True),
        ("Software Engineer", "No experience required to apply", True),
        ("Software Engineer", "No prior experience is required for this role", True),
        ("Software Engineer", "No experience is required in order to apply", True),
        ("Software Engineer", "No experience is required for this position", True),
        ("Software Engineer", "No experience required for this job", True),
        ("Software Engineer", "No experience is required for the position", True),
        ("Software Engineer", "No experience is needed for our role", True),
        ("Software Engineer", "No experience is required for a new role", True),
        ("Software Engineer", "No experience is required for the new role", True),
        ("Software Engineer", "No experience is required for an opportunity", True),
        ("Software Engineer", "No experience is required for a role", True),
        ("Software Engineer", "No experience is required for an open position", True),
        ("Software Engineer", "No experience required for freshers", True),
        ("Software Engineer", "No experience is required for applicants", True),
        ("Software Engineer", "No experience required for beginners", True),
        ("Software Engineer", "No experience is needed for entry-level candidates", True),
        ("Software Engineer", "This position requires no prior experience", True),
        ("Software Engineer", "Requires no experience", True),
        ("Software Engineer", "Requires no experience for this role", True),
        ("Software Engineer", "Freshers are welcome to apply", True),
        ("Software Engineer", "Freshers can apply", True),
        ("Software Engineer", "Open to freshers", True),
        ("Software Engineer", "Hiring freshers", True),

        # --- Seniority precedence: senior titles still reject despite zero-experience declarations ---
        ("Senior Software Engineer", "No prior experience is required", False),
        ("Principal Engineer", "No prior experience is required", False),
        ("Lead Developer", "No experience necessary", False),

        # --- Numeric experience precedence: >1 year still rejects despite zero-experience declarations ---
        ("Software Engineer", "No prior experience is required. 2+ years of experience required.", False),
        ("Software Engineer", "No experience necessary. Minimum 3 years.", False),
    ]
    for title, desc, expected in cases:
        assert is_fresher_eligible(title, desc) == expected, f"Failed: {title} | {desc} (Expected {expected})"
