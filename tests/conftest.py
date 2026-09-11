import os
os.environ.setdefault("API_SECRET_KEY", "test-api-secret-key-for-pytest")
import pytest
import psycopg
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from app.core.config import settings
from app.db.base import Base

def get_test_db_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("FATAL: TEST_DATABASE_URL environment variable is required to run tests.")

    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    db_name = parsed.path.lstrip('/')

    unsafe_exact_names = {"postgres", "template0", "template1", "production", "prod", "development", "dev", "jobpilot"}
    if db_name in unsafe_exact_names or "_test" not in db_name:
        raise RuntimeError(f"FATAL: Database URL appears to target a non-test database! ({db_name})")

    dev_url = settings.get_database_url()
    dev_parsed = urllib.parse.urlparse(dev_url)

    if (parsed.hostname, parsed.port, parsed.path) == (dev_parsed.hostname, dev_parsed.port, dev_parsed.path):
        raise RuntimeError("FATAL: TEST_DATABASE_URL exactly matches application DATABASE_URL host/port/db.")

    return url

TEST_DATABASE_URL = get_test_db_url()

def create_test_db_if_not_exists(test_url: str):
    # Connect to the default 'postgres' database to create the test database
    import urllib.parse
    from psycopg.sql import SQL, Identifier
    parsed = urllib.parse.urlparse(test_url)
    db_name = parsed.path.lstrip('/')

    # Construct postgres url (connect to postgres db)
    postgres_url = parsed._replace(path='/postgres').geturl().replace("postgresql+psycopg://", "postgresql://")

    try:
        with psycopg.connect(postgres_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                if not cur.fetchone():
                    cur.execute(SQL("CREATE DATABASE {}").format(Identifier(db_name)))
    except psycopg.errors.DuplicateDatabase:
        pass
    except Exception as e:
        raise RuntimeError("FATAL: Failed to connect to PostgreSQL to create test database") from e

@pytest.fixture(scope="session")
def engine():
    create_test_db_if_not_exists(TEST_DATABASE_URL)
    engine = create_engine(TEST_DATABASE_URL)

    # P2.3: Alembic vs Test DB consistency. We should use alembic upgrade head here!
    from alembic.config import Config
    from alembic import command

    alembic_cfg = Config("backend/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    alembic_cfg.set_main_option("script_location", "backend/alembic")

    # Drop all tables manually just in case to start clean
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    command.upgrade(alembic_cfg, "head")

    yield engine
    # Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session")
def db_engine(engine):
    """Compatibility alias for integration tests that request db_engine."""
    return engine

@pytest.fixture(scope="function")
def db_session(engine):
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    # TRUNCATE tables to keep tests isolated safely
    # B7: Removed dynamic SQL, using SQLAlchemy delete
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
