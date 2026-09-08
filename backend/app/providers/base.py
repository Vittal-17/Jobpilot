from abc import ABC, abstractmethod
from typing import List
from app.models.job import Job
from app.schemas.job_search import JobSearchQuery

class JobProvider(ABC):
    @abstractmethod
    def search_jobs(self, query: JobSearchQuery) -> List[Job]:
        pass
