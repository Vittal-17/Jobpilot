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
        ("Software Engineer", "fresher", True),

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


def test_internship_role_recognition():
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
