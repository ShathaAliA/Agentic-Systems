"""Opaque sessions belong exclusively to the development identity provider."""
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from app.auth.models import DevelopmentCredential, DevelopmentSession, LoginWindow
from app.auth.passwords import verify_password
from app.models import User


def unauthorized():
    return HTTPException(401, "Invalid or missing authentication", headers={"WWW-Authenticate": "Bearer"})


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def aware(value):
    # SQLite test timestamps are naive; PostgreSQL columns are timestamptz.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def find_user(session, email):
    users = session.scalars(select(User).where(func.lower(User.email) == email.strip().lower()).limit(2)).all()
    return users[0] if len(users) == 1 else None


def limit_login(session, settings, email, client_ip, now):
    """Atomic shared counters: no worker-local limiter or forwarded-header trust."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError("Authentication requires PostgreSQL (SQLite is test-only)")
    bucket = int(now.timestamp()) // settings.login_window_seconds
    expires = datetime.fromtimestamp((bucket + 1) * settings.login_window_seconds, timezone.utc)
    for kind, value, maximum in (
        ("global", "all", settings.login_global_limit),
        ("ip", client_ip, settings.login_ip_limit),
        ("account", email.strip().lower(), settings.login_account_limit),
    ):
        key = digest(f"{kind}:{bucket}:{value}")
        statement = insert(LoginWindow).values(key=key, attempts=1, expires_at=expires)
        statement = statement.on_conflict_do_update(
            index_elements=[LoginWindow.key], set_={"attempts": LoginWindow.attempts + 1}
        ).returning(LoginWindow.attempts)
        attempts = session.scalar(statement)
        if attempts > maximum:
            session.commit()
            retry = max(1, int((expires - now).total_seconds()) + 1)
            raise HTTPException(429, "Login rate limit exceeded", headers={"Retry-After": str(retry)})
    session.commit()  # Failed password checks must still consume their attempts.


def login(session, settings, email, password, client_ip):
    now = datetime.now(timezone.utc)
    limit_login(session, settings, email, client_ip, now)
    user = find_user(session, email)
    credential = session.get(DevelopmentCredential, user.id) if user else None
    valid = verify_password(credential.password_hash if credential else None, password)
    if not valid or user is None or user.status != "active":
        raise unauthorized()
    token = "dev_" + secrets.token_urlsafe(32)
    record = DevelopmentSession(user_id=user.id, token_digest=digest(token),
                                credential_version=credential.version,
                                expires_at=now + timedelta(seconds=settings.session_seconds))
    session.add(record)
    session.commit()
    return token


@dataclass(frozen=True)
class AuthContext:
    user: User
    session_record: DevelopmentSession


class DevelopmentSessionValidator:
    """Do not reuse this validator for Microsoft tokens or map identity from headers."""

    def validate(self, session, token):
        if not re.fullmatch(r"dev_[A-Za-z0-9_-]{43}", token):
            raise unauthorized()
        record = session.scalar(select(DevelopmentSession).where(DevelopmentSession.token_digest == digest(token)))
        now = datetime.now(timezone.utc)
        if record is None or record.revoked_at is not None or aware(record.expires_at) <= now:
            raise unauthorized()
        user = session.get(User, record.user_id)
        credential = session.get(DevelopmentCredential, record.user_id)
        if user is None or user.status != "active" or credential is None or credential.version != record.credential_version:
            raise unauthorized()
        return AuthContext(user, record)
