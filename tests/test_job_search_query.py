import pytest
from app.schemas.job_search import JobSearchQuery

def test_job_search_query_validation():
    with pytest.raises(ValueError):
        JobSearchQuery(keywords="   ", location="Bangalore")
    with pytest.raises(ValueError):
        JobSearchQuery(keywords="Python", location="   ")
    with pytest.raises(ValueError):
        JobSearchQuery(keywords="Python", location="Bangalore", page_size=200)

    q = JobSearchQuery(keywords="Python", location="Bangalore")
    assert q.keywords == "Python"
