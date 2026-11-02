"""Offline enrollment for existing users; never mounted or invoked by FastAPI."""
import argparse
from datetime import datetime, timezone
from getpass import getpass
import uuid

from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings
from app.auth.models import DevelopmentCredential, DevelopmentSession
from app.auth.passwords import hash_password
from app.auth.service import find_user
from app.credentials import configured_url


def set_password(session, email, password):
    user = find_user(session, email)
    if user is None or user.status != "active":
        raise ValueError("A unique active existing user is required")
    encoded = hash_password(password)
    credential = session.get(DevelopmentCredential, user.id)
    if credential is None:
        credential = DevelopmentCredential(user_id=user.id)
        session.add(credential)
    credential.password_hash = encoded
    credential.version = str(uuid.uuid4())
    credential.updated_at = datetime.now(timezone.utc)
    session.execute(update(DevelopmentSession).where(DevelopmentSession.user_id == user.id)
                    .values(revoked_at=datetime.now(timezone.utc)))
    session.commit()


def main():
    parser = argparse.ArgumentParser(description="Enroll/rotate an existing development user's password")
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    settings = AuthSettings.from_environment()
    if settings.mode != "development_password":
        parser.exit(2, "Development password authentication must be explicitly enabled\n")
    password = getpass("New development password (12-128 characters): ")
    if password != getpass("Confirm password: "):
        parser.exit(2, "Passwords do not match\n")
    engine = None
    try:
        engine = create_engine(configured_url("AUTH_BOOTSTRAP_DATABASE_URL"), hide_parameters=True)
        with Session(engine) as session:
            set_password(session, args.email, password)
    except Exception:
        # Raw database exceptions can contain hashes and connection information.
        parser.exit(2, "Enrollment failed; check the active user, password policy and bootstrap configuration\n")
    finally:
        if engine is not None:
            engine.dispose()
    print("Development password enrolled; previous sessions invalidated")


if __name__ == "__main__":
    main()
