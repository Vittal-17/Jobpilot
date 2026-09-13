from sqlalchemy import create_engine
from app.db.base import Base
from app.db.models import *

engine = create_engine('sqlite:///test.db')
Base.metadata.create_all(engine)
