import pytest
from app.services.job_url_classifier import classify_job_url


LIVE_ACCEPTANCE_JOBS = [
    (914, "Hiring Python Fresher jobs in Bengaluru, Karnataka", "https://in.indeed.com/q-hiring-python-fresher-l-bengaluru,-karnataka-jobs.html", False, "indeed_listing"),
    (915, "Python Developer Jobs in Bengaluru (1,000+ Open Roles)", "https://in.linkedin.com/jobs/python-developer-jobs-bengaluru", False, "linkedin_listing"),
    (916, "18340 Python Fresher Job Vacancies In Bangalore", "https://www.naukri.com/python-fresher-jobs-in-bangalore", False, "naukri_listing"),
    (917, "Python Developer Jobs in Bengaluru, India - 2026", "https://wellfound.com/role/l/python-developer/bangalore", False, "wellfound_listing"),
    (918, "python developer jobs in bengaluru, karnataka", "https://www.simplyhired.co.in/search?q=python+developer&l=bengaluru%2C+karnataka", False, "simplyhired_listing"),
    (919, "Python Developer(Fresher)", "https://cutshort.io/job/Python-Developer-Fresher-Bengaluru-Bangalore-CEDRETO-MARKETING-PRIVATE-LIMITED-ZefdPWHo", True, "individual_posting"),
    (920, "24 python fresher jobs in Bengaluru, October 2026", "https://www.glassdoor.co.in/Job/bengaluru-python-fresher-jobs-SRCH_IL.0,9_IC2940587_KO10,24.htm", False, "glassdoor_listing"),
    (921, "Python Fresher Jobs in Bangalore - 86 Vacancies in Oct 2026", "https://internshala.com/fresher-jobs/python-jobs-in-bangalore/", False, "internshala_listing"),
    (922, "Python Jobs, 183638 Python Openings", "https://www.naukri.com/python-jobs", False, "naukri_listing"),
    (923, "Full Stack Python Developer jobs in Bengaluru, Karnataka", "https://in.indeed.com/q-full-stack-python-developer-l-bengaluru,-karnataka-jobs.html", False, "indeed_listing"),
    (924, "Backend Engineer Jobs in Bengaluru, India - 2027", "https://wellfound.com/role/l/backend-engineer/bangalore", False, "wellfound_listing"),
    (925, "10000+ Back End Developer Jobs in Bengaluru", "https://in.linkedin.com/jobs/back-end-developer-jobs-bengaluru", False, "linkedin_listing"),
    (926, "Backend Developer jobs in Bengaluru, Karnataka", "https://in.indeed.com/q-backend-developer-l-bengaluru,-karnataka-jobs.html", False, "indeed_listing"),
    (927, "4898 Backend developer jobs in Bengaluru", "https://www.glassdoor.co.in/Job/bengaluru-backend-developer-jobs-SRCH_IL.0,9_IC2940587_KO10,27.htm", False, "glassdoor_listing"),
    (928, "Backend Developer Jobs In Bangalore - Bengaluru", "https://www.naukri.com/backend-developer-jobs-in-bangalore", False, "naukri_listing"),
    (929, "33 Backend Development Jobs for Freshers in Bangalore", "https://internshala.com/fresher-jobs/backend-development-jobs-in-bangalore/", False, "internshala_listing"),
    (930, "Backend Developer", "https://hbspl.in/jobs/backend-developer-node-js-django-spring-boot-express-js/", True, "individual_posting"),
    (931, "Apply to 2387 Backend Development job openings in Bangalore", "https://www.instahyre.com/backend-development-jobs-in-bangalore/", False, "instahyre_listing"),
    (932, "backend developer jobs in bengaluru, karnataka", "https://www.simplyhired.co.in/search?q=backend+developer&l=bengaluru%2C+karnataka", False, "simplyhired_listing"),
    (933, "2660+ Backend Developer Jobs in Bangalore (Bengaluru)", "https://cutshort.io/jobs/backend-developer-jobs-in-bangalore-bengaluru", False, "cutshort_listing"),
]


@pytest.mark.parametrize("job_id,title,url,expected_valid,expected_reason", LIVE_ACCEPTANCE_JOBS)
def test_classifier_on_20_live_acceptance_jobs(job_id, title, url, expected_valid, expected_reason):
    is_valid, reason = classify_job_url(url, title)
    assert is_valid is expected_valid, f"Job {job_id} expected valid={expected_valid} but got {is_valid} (reason: {reason})"
    if not expected_valid:
        # Check that it's a known rejection reason
        assert reason == expected_reason or "listing" in reason or "aggregate" in reason


PLATFORM_TEST_CASES = [
    # Indeed
    ("Indeed", "https://in.indeed.com/viewjob?jk=1234567890abcdef", "Python Developer", True, "individual_posting"),
    ("Indeed", "https://in.indeed.com/rc/clk?jk=1234567890abcdef", "Python Developer", True, "individual_posting"),
    ("Indeed", "https://in.indeed.com/q-python-developer-jobs.html", "Python Developer Jobs", False, "indeed_listing"),
    # LinkedIn
    ("LinkedIn", "https://www.linkedin.com/jobs/view/3928172615/", "Junior Python Developer", True, "individual_posting"),
    ("LinkedIn", "https://in.linkedin.com/jobs/python-developer-jobs-bengaluru", "Python Developer Jobs", False, "linkedin_listing"),
    # Naukri
    ("Naukri", "https://www.naukri.com/job-listings-python-developer-acme-technologies-bengaluru-0-to-2-years-010224001234", "Python Developer", True, "individual_posting"),
    ("Naukri", "https://www.naukri.com/python-jobs", "Python Jobs", False, "naukri_listing"),
    # Cutshort
    ("Cutshort", "https://cutshort.io/job/python-developer-fresher-acme-12345", "Python Developer", True, "individual_posting"),
    ("Cutshort", "https://cutshort.io/jobs/backend-developer-jobs-in-bangalore", "Backend Developer Jobs", False, "cutshort_listing"),
    # Glassdoor
    ("Glassdoor", "https://www.glassdoor.co.in/job-listing/python-developer-acme-corp-JV_IC2940587_KO0,16.htm", "Python Developer", True, "individual_posting"),
    ("Glassdoor", "https://www.glassdoor.co.in/Job/bengaluru-python-fresher-jobs-SRCH_IL.0,9_IC2940587_KO10,24.htm", "Python Jobs", False, "glassdoor_listing"),
    # Internshala
    ("Internshala", "https://internshala.com/job/detail/python-developer-123456", "Python Developer", True, "individual_posting"),
    ("Internshala", "https://internshala.com/fresher-jobs/python-jobs-in-bangalore/", "Python Jobs", False, "internshala_listing"),
    # Instahyre
    ("Instahyre", "https://www.instahyre.com/job/123456-backend-developer-at-startup", "Backend Developer", True, "individual_posting"),
    ("Instahyre", "https://www.instahyre.com/backend-development-jobs-in-bangalore/", "Backend Jobs", False, "instahyre_listing"),
    # Wellfound
    ("Wellfound", "https://wellfound.com/jobs/12345-backend-engineer", "Backend Engineer", True, "individual_posting"),
    ("Wellfound", "https://wellfound.com/role/l/python-developer/bangalore", "Python Developer Jobs", False, "wellfound_listing"),
    # SimplyHired
    ("SimplyHired", "https://www.simplyhired.co.in/job/abc123xyz", "Python Developer", True, "individual_posting"),
    ("SimplyHired", "https://www.simplyhired.co.in/search?q=python+developer&l=bengaluru", "Python Developer Jobs", False, "simplyhired_listing"),
    # Greenhouse
    ("Greenhouse", "https://boards.greenhouse.io/acme/jobs/4012345", "Software Engineer", True, "individual_posting"),
    ("Greenhouse", "https://boards.greenhouse.io/acme", "Careers at Acme", False, "greenhouse_listing"),
    # Lever
    ("Lever", "https://jobs.lever.co/stripe/a1b2c3d4-e5f6-7890-abcd-1234567890ab", "Backend Engineer", True, "individual_posting"),
    ("Lever", "https://jobs.lever.co/stripe", "Careers at Stripe", False, "lever_listing"),
    # Ashby
    ("Ashby", "https://jobs.ashbyhq.com/openai/12345678-abcd-1234-abcd-1234567890ab", "Software Engineer", True, "individual_posting"),
    ("Ashby", "https://jobs.ashbyhq.com/openai", "OpenAI Careers", False, "ashby_listing"),
    # Workday
    ("Workday", "https://acme.wd1.myworkdayjobs.com/en-US/AcmeCareers/job/Bengaluru/Software-Engineer_JR12345", "Software Engineer", True, "individual_posting"),
    ("Workday", "https://acme.wd1.myworkdayjobs.com/en-US/AcmeCareers", "Acme Careers", False, "workday_listing"),
]


@pytest.mark.parametrize("platform,url,title,expected_valid,expected_reason", PLATFORM_TEST_CASES)
def test_classifier_all_supported_platforms_accepted_and_rejected(platform, url, title, expected_valid, expected_reason):
    valid, reason = classify_job_url(url, title)
    assert valid is expected_valid, f"{platform} URL {url} expected valid={expected_valid}, got {valid} ({reason})"
    assert reason == expected_reason, f"{platform} URL {url} expected reason={expected_reason}, got {reason}"


ADVERSARIAL_TEST_CASES = [
    # Lever directory attacks & apply subpath
    ("Lever", "https://jobs.lever.co/stripe/departments", "Departments at Stripe", False, "lever_listing"),
    ("Lever", "https://jobs.lever.co/stripe/teams", "Teams at Stripe", False, "lever_listing"),
    ("Lever", "https://jobs.lever.co/stripe/locations", "Locations at Stripe", False, "lever_listing"),
    ("Lever", "https://jobs.lever.co/stripe/all", "All Jobs at Stripe", False, "lever_listing"),
    ("Lever", "https://jobs.lever.co/stripe/jobs", "Jobs at Stripe", False, "lever_listing"),
    ("Lever", "https://jobs.lever.co/stripe/a1b2c3d4-e5f6-7890-abcd-1234567890ab/apply", "Backend Engineer", True, "individual_posting"),
    # Ashby directory attacks & application subpath
    ("Ashby", "https://jobs.ashbyhq.com/openai/departments", "Departments at OpenAI", False, "ashby_listing"),
    ("Ashby", "https://jobs.ashbyhq.com/openai/teams", "Teams at OpenAI", False, "ashby_listing"),
    ("Ashby", "https://jobs.ashbyhq.com/openai/locations", "Locations at OpenAI", False, "ashby_listing"),
    ("Ashby", "https://jobs.ashbyhq.com/openai/all", "All Roles at OpenAI", False, "ashby_listing"),
    ("Ashby", "https://jobs.ashbyhq.com/openai/jobs", "Jobs at OpenAI", False, "ashby_listing"),
    ("Ashby", "https://jobs.ashbyhq.com/openai/12345678-abcd-1234-abcd-1234567890ab/application", "Software Engineer", True, "individual_posting"),
    # Workday bare & directory attacks
    ("Workday", "https://acme.wd1.myworkdayjobs.com/en-US/AcmeCareers/job/", "Acme Jobs", False, "workday_listing"),
    ("Workday", "https://acme.wd1.myworkdayjobs.com/en-US/AcmeCareers/job/search", "Search Jobs", False, "workday_listing"),
    ("Workday", "https://acme.wd1.myworkdayjobs.com/en-US/AcmeCareers/job/browse", "Browse Jobs", False, "workday_listing"),
    ("Workday", "https://acme.wd1.myworkdayjobs.com/job/New-York-NY/Senior-Analyst_R-98765", "Senior Analyst", True, "individual_posting"),
    # Indeed bare endpoints
    ("Indeed", "https://in.indeed.com/viewjob", "Python Developer", False, "indeed_listing"),
    ("Indeed", "https://in.indeed.com/rc/clk", "Python Developer", False, "indeed_listing"),
    ("Indeed", "https://www.indeed.com/viewjob?jk=1234567890abcdef&from=serp", "Senior Python Dev", True, "individual_posting"),
    # LinkedIn bare & non-id paths
    ("LinkedIn", "https://www.linkedin.com/jobs/view/", "LinkedIn Jobs", False, "linkedin_listing"),
    ("LinkedIn", "https://www.linkedin.com/jobs/view/search", "LinkedIn Jobs", False, "linkedin_listing"),
    ("LinkedIn", "https://www.linkedin.com/jobs/view/python-engineer-at-tech-corp-12345678", "Python Engineer", True, "individual_posting"),
    # Naukri bare paths
    ("Naukri", "https://www.naukri.com/job-listings", "Naukri Jobs", False, "naukri_listing"),
    ("Naukri", "https://www.naukri.com/job-listings/", "Naukri Jobs", False, "naukri_listing"),
    ("Naukri", "https://www.naukri.com/job-listings/python-engineer-bengaluru-998877665544", "Python Engineer", True, "individual_posting"),
    # Glassdoor bare paths
    ("Glassdoor", "https://www.glassdoor.co.in/job-listing/", "Glassdoor Jobs", False, "glassdoor_listing"),
    ("Glassdoor", "https://www.glassdoor.com/job-listing/", "Glassdoor Jobs", False, "glassdoor_listing"),
    # Cutshort reserved slugs
    ("Cutshort", "https://cutshort.io/job/search", "Cutshort Search", False, "cutshort_listing"),
    ("Cutshort", "https://cutshort.io/job/browse", "Cutshort Browse", False, "cutshort_listing"),
    # Internshala bare paths
    ("Internshala", "https://internshala.com/job/detail/", "Internshala Jobs", False, "internshala_listing"),
    ("Internshala", "https://internshala.com/internship/detail/", "Internshala Internships", False, "internshala_listing"),
    # Instahyre hyphenated slugs
    ("Instahyre", "https://www.instahyre.com/job-123456-backend-developer-at-startup", "Backend Developer", True, "individual_posting"),
    ("Instahyre", "https://www.instahyre.com/job/", "Instahyre Jobs", False, "instahyre_listing"),
    # SimplyHired bare & search slugs
    ("SimplyHired", "https://www.simplyhired.co.in/job/", "SimplyHired Jobs", False, "simplyhired_listing"),
    ("SimplyHired", "https://www.simplyhired.com/job/search", "SimplyHired Search", False, "simplyhired_listing"),
    # Generic openings paths
    ("Generic", "https://company.com/openings/backend-engineer-1234", "Backend Engineer", True, "individual_posting"),
    ("Generic", "https://company.com/openings/", "Current Openings", False, "generic_directory_root"),
    ("Generic", "https://company.com/careers/search?q=developer", "Search Results", False, "search_query_url"),
    ("Generic", "https://company.com/careers/departments", "Departments", False, "ambiguous_pattern"),
]


@pytest.mark.parametrize("platform,url,title,expected_valid,expected_reason", ADVERSARIAL_TEST_CASES)
def test_classifier_adversarial_security_matrix(platform, url, title, expected_valid, expected_reason):
    valid, reason = classify_job_url(url, title)
    assert valid is expected_valid, f"{platform} URL {url} expected valid={expected_valid}, got {valid} ({reason})"
    assert reason == expected_reason, f"{platform} URL {url} expected reason={expected_reason}, got {reason}"


def test_classifier_malformed_and_boundary_cases():
    assert classify_job_url("") == (False, "missing_url")
    assert classify_job_url("   ") == (False, "missing_url")
    assert classify_job_url("ftp://example.com/job") == (False, "unsupported_scheme")
    assert classify_job_url("https://example.com/search?q=python") == (False, "search_query_url")
    assert classify_job_url("https://example.com/jobs") == (False, "generic_directory_root")
    assert classify_job_url("https://example.com/careers/") == (False, "generic_directory_root")
    # Ambiguous single segment fails closed
    assert classify_job_url("https://example.com/python-developer") == (False, "ambiguous_pattern")
