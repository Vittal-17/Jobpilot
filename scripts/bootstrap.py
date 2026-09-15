#!/usr/bin/env python3
import os
import sys
import logging
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

from sqlalchemy.orm import Session
from sqlalchemy import text
from app.db.database import SessionLocal
from app.db.models.user import User
from app.db.models.user_profile import UserProfile
from app.db.models.user_search import UserSearch
from app.core.security import get_password_hash

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def bootstrap_single_user(db: Session, email: str, password: str):
    if not password:
        raise ValueError("Bootstrap password must be provided.")

    user = db.query(User).filter(User.id == 1).first()

    email_conflict = db.query(User).filter(User.email == email, User.id != 1).first()
    if email_conflict:
        raise ValueError(f"Email '{email}' is already in use by User ID {email_conflict.id}. Canonical bootstrap requires User ID 1.")

    if not user:
        logger.info(f"Creating canonical bootstrap User 1: {email}")
        user = User(
            id=1,
            email=email,
            password_hash=get_password_hash(password),
            display_name="JobPilot Administrator"
        )
        db.add(user)
        db.flush()
        db.execute(text("SELECT setval(pg_get_serial_sequence('users', 'id'), (SELECT MAX(id) FROM users));"))
        db.flush()
    else:
        if user.email != email:
            raise ValueError(f"User ID 1 already exists but with a different email ('{user.email}'). Cannot bootstrap over existing identity.")
        logger.info(f"Canonical User ID 1 already exists ({email}). Skipping user creation.")

    profile = db.query(UserProfile).filter(UserProfile.user_id == 1).first()
    if not profile:
        logger.info("Creating default UserProfile.")
        profile = UserProfile(
            user_id=1,
            headline="Software Engineer",
            experience_years=3,
            preferred_roles="Software Engineer, Backend Developer",
            preferred_locations="Remote, New York",
            remote_preference="remote"
        )
        db.add(profile)

    search = db.query(UserSearch).filter(UserSearch.user_id == 1).first()
    if not search:
        logger.info("Creating default UserSearch.")
        search = UserSearch(
            user_id=1,
            query="Software Engineer",
            location="Remote",
            remote_only=True
        )
        db.add(search)

    db.commit()
    logger.info("Bootstrap complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bootstrap JobPilot canonical User 1")
    parser.add_argument("--email", type=str, default="admin@jobpilot.local", help="Email for the canonical admin user")
    args = parser.parse_args()

    password = os.environ.get("BOOTSTRAP_PASSWORD")
    if not password:
        logger.error("BOOTSTRAP_PASSWORD environment variable is required.")
        sys.exit(1)

    try:
        with SessionLocal() as db:
            bootstrap_single_user(db, email=args.email, password=password)
    except Exception as e:
        logger.error(f"Bootstrap failed: {e}")
        sys.exit(1)
