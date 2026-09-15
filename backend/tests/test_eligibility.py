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
