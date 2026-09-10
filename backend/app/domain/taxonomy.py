from pydantic import BaseModel
from typing import List, Optional

class RoleModel(BaseModel):
    id: str
    canonical: str
    family: str
    priority: int
    aliases: List[str] = []

class LocationModel(BaseModel):
    id: str
    canonical: str
    tier: int
    priority: int
    aliases: List[str] = []
    related_areas: List[str] = []

class CoverageModel(BaseModel):
    roles: List[RoleModel]
    locations: List[LocationModel]

# The authoritative 005.5A taxonomy
ROLE_CATALOG = [
    RoleModel(id='ROLE-PY-001', canonical='Python Developer', family='Python / Backend', priority=1, aliases=['Junior Python Developer', 'Python Developer Fresher', 'Python Software Developer', 'Junior Python Software Developer']),
    RoleModel(id='ROLE-PY-002', canonical='Backend Developer', family='Python / Backend', priority=1, aliases=['Junior Backend Developer', 'Backend Developer Fresher', 'Backend Software Developer', 'Backend Software Engineer', 'Junior Backend Engineer', 'Python Backend Developer', 'Junior Python Backend Developer']),
    RoleModel(id='ROLE-PY-003', canonical='Django Developer', family='Python / Backend', priority=1, aliases=['Junior Django Developer', 'Django Developer Fresher', 'Django Backend Developer']),
    RoleModel(id='ROLE-PY-004', canonical='FastAPI Developer', family='Python / Backend', priority=2, aliases=['Junior FastAPI Developer', 'FastAPI Developer Fresher']),
    RoleModel(id='ROLE-PY-005', canonical='Flask Developer', family='Python / Backend', priority=2, aliases=['Junior Flask Developer', 'Flask Developer Fresher']),
    RoleModel(id='ROLE-DBA-001', canonical='Database Administrator', family='Database / DBA / SQL', priority=1, aliases=['Junior DBA', 'DBA Fresher', 'DBA Trainee', 'Database Administrator Fresher', 'Junior Database Administrator', 'Trainee Database Administrator']),
    RoleModel(id='ROLE-DBA-002', canonical='Database Support Engineer', family='Database / DBA / SQL', priority=1, aliases=['Database Support Fresher', 'Junior Database Support Engineer', 'Database Support Analyst', 'Junior Database Analyst', 'Database Analyst', 'Database Operations Analyst', 'Data Operations Analyst']),
    RoleModel(id='ROLE-DBA-003', canonical='SQL Developer', family='Database / DBA / SQL', priority=1, aliases=['Junior SQL Developer', 'SQL Developer Fresher', 'SQL Support Engineer', 'SQL Support Fresher', 'Junior SQL Support Engineer', 'SQL Analyst', 'Junior SQL Analyst']),
    RoleModel(id='ROLE-DBA-004', canonical='Database Engineer', family='Database / DBA / SQL', priority=1, aliases=['Database Engineer Fresher', 'Junior Database Engineer', 'Database Operations Engineer', 'Database Operations Fresher']),
    RoleModel(id='ROLE-FS-001', canonical='Full Stack Developer', family='Full Stack / Web', priority=2, aliases=['Junior Full Stack Developer', 'Full Stack Developer Fresher', 'Full Stack Software Engineer']),
    RoleModel(id='ROLE-FS-002', canonical='Web Developer', family='Full Stack / Web', priority=2, aliases=['Junior Web Developer', 'Web Developer Fresher', 'Entry Level Web Developer']),
    RoleModel(id='ROLE-FS-003', canonical='React Developer', family='Full Stack / Web', priority=3, aliases=['React Developer Fresher', 'Junior React Developer']),
    RoleModel(id='ROLE-FS-004', canonical='Node.js Developer', family='Full Stack / Web', priority=3, aliases=['Node.js Developer Fresher', 'Junior Node.js Developer']),
    RoleModel(id='ROLE-SWE-001', canonical='Software Developer', family='General Software Engineering', priority=1, aliases=['Junior Software Developer', 'Software Developer Fresher', 'Graduate Software Developer', 'Associate Software Developer', 'Trainee Software Developer', 'Junior Developer', 'Software Development Trainee']),
    RoleModel(id='ROLE-SWE-002', canonical='Software Engineer', family='General Software Engineering', priority=1, aliases=['Junior Software Engineer', 'Software Engineer Fresher', 'Graduate Software Engineer', 'Associate Software Engineer', 'Trainee Software Engineer', 'Entry Level Software Engineer']),
    RoleModel(id='ROLE-AI-001', canonical='RAG Engineer', family='AI / Generative AI / RAG / ML', priority=1, aliases=['Junior RAG Engineer', 'RAG Developer', 'Junior RAG Developer', 'Retrieval Augmented Generation Engineer']),
    RoleModel(id='ROLE-AI-002', canonical='AI Engineer', family='AI / Generative AI / RAG / ML', priority=1, aliases=['AI Engineer Fresher', 'Junior AI Engineer', 'AI Developer Fresher', 'Junior AI Developer', 'AI/ML Engineer Fresher', 'Junior AI/ML Engineer']),
    RoleModel(id='ROLE-AI-003', canonical='Generative AI Engineer', family='AI / Generative AI / RAG / ML', priority=1, aliases=['Generative AI Engineer Fresher', 'Junior Generative AI Engineer', 'GenAI Developer Fresher', 'Junior GenAI Developer']),
    RoleModel(id='ROLE-AI-004', canonical='Machine Learning Engineer', family='AI / Generative AI / RAG / ML', priority=2, aliases=['Machine Learning Engineer Fresher', 'Junior Machine Learning Engineer', 'ML Engineer Fresher']),
    RoleModel(id='ROLE-DA-001', canonical='Data Analyst', family='Data / Analytics / Data Engineering', priority=2, aliases=['Data Analyst Fresher', 'Junior Data Analyst', 'SQL Data Analyst', 'Junior SQL Data Analyst']),
    RoleModel(id='ROLE-DA-002', canonical='Data Engineer', family='Data / Analytics / Data Engineering', priority=2, aliases=['Data Engineer Fresher', 'Junior Data Engineer', 'Data Engineering Trainee', 'Associate Data Engineer']),
    RoleModel(id='ROLE-DA-003', canonical='Business Intelligence Analyst', family='Data / Analytics / Data Engineering', priority=3, aliases=['Junior BI Analyst', 'Business Intelligence Analyst Fresher']),
    RoleModel(id='ROLE-QA-001', canonical='QA Engineer', family='QA / Testing', priority=3, aliases=['QA Engineer Fresher', 'Junior QA Engineer', 'QA Automation Engineer Fresher']),
    RoleModel(id='ROLE-QA-002', canonical='QA Tester', family='QA / Testing', priority=3, aliases=['QA Tester Fresher', 'Junior QA Tester', 'Software Tester Fresher', 'Junior Software Tester']),
    RoleModel(id='ROLE-QA-003', canonical='Test Engineer', family='QA / Testing', priority=3, aliases=['Test Engineer Fresher', 'Junior Test Engineer', 'Automation Test Engineer Fresher', 'Junior Automation Test Engineer']),
    RoleModel(id='ROLE-CL-001', canonical='DevOps Engineer', family='DevOps / Cloud', priority=3, aliases=['DevOps Engineer Fresher', 'Junior DevOps Engineer', 'DevOps Trainee']),
    RoleModel(id='ROLE-CL-002', canonical='Cloud Engineer', family='DevOps / Cloud', priority=3, aliases=['Cloud Engineer Fresher', 'Junior Cloud Engineer', 'Cloud Support Engineer Fresher', 'Junior Cloud Support Engineer', 'Site Reliability Engineer Fresher', 'Junior SRE']),
    RoleModel(id='ROLE-IT-001', canonical='Application Support Engineer', family='IT / Application Support', priority=3, aliases=['Application Support Engineer Fresher', 'Junior Application Support Engineer', 'Production Support Engineer Fresher', 'Junior Production Support Engineer']),
    RoleModel(id='ROLE-IT-002', canonical='Technical Support Engineer', family='IT / Application Support', priority=3, aliases=['Technical Support Engineer Fresher', 'Junior Technical Support Engineer', 'IT Support Engineer Fresher'])
]

LOCATION_CATALOG = [
    LocationModel(id='LOC-BLR-001', canonical='Bengaluru', tier=0, priority=1, aliases=['Bangalore', 'Bengaluru, Karnataka', 'Bangalore, Karnataka']),
    LocationModel(id='LOC-BLR-002', canonical='Whitefield', tier=1, priority=1, aliases=[], related_areas=['ITPL', 'Brookefield', 'Hoodi']),
    LocationModel(id='LOC-BLR-003', canonical='Electronic City', tier=1, priority=1, aliases=['Electronic City Phase 1', 'Electronic City Phase 2'], related_areas=['Bommasandra']),
    LocationModel(id='LOC-BLR-004', canonical='Outer Ring Road', tier=1, priority=1, aliases=['ORR'], related_areas=['Bellandur', 'Kadubeesanahalli', 'Marathahalli']),
    LocationModel(id='LOC-BLR-005', canonical='Manyata Tech Park', tier=1, priority=1, aliases=['Manyata'], related_areas=['Hebbal', 'Thanisandra']),
    LocationModel(id='LOC-BLR-006', canonical='Sarjapur Road', tier=1, priority=2, aliases=[]),
    LocationModel(id='LOC-BLR-007', canonical='CV Raman Nagar', tier=1, priority=2, aliases=[], related_areas=['Bagmane Tech Park', 'Old Airport Road']),
    LocationModel(id='LOC-BLR-008', canonical='Koramangala', tier=2, priority=2, aliases=[]),
    LocationModel(id='LOC-BLR-009', canonical='HSR Layout', tier=2, priority=2, aliases=[]),
    LocationModel(id='LOC-BLR-010', canonical='Indiranagar', tier=2, priority=2, aliases=[]),
    LocationModel(id='LOC-BLR-011', canonical='KR Puram', tier=2, priority=3, aliases=[], related_areas=['Mahadevapura']),
    LocationModel(id='LOC-BLR-012', canonical='Yelahanka', tier=2, priority=3, aliases=[])
]

def get_authoritative_taxonomy() -> CoverageModel:
    return CoverageModel(roles=ROLE_CATALOG, locations=LOCATION_CATALOG)
