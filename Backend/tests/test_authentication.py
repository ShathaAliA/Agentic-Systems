from datetime import datetime, timedelta, timezone
import secrets
import uuid

from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.config import AuthSettings
from app.auth.dependencies import require_department_permission, require_global_permission
from app.auth.enroll import set_password
from app.auth.models import DevelopmentCredential, DevelopmentSession, LoginWindow
from app.auth.service import digest
from app.main import create_app
from app.models import (Base, Department, DepartmentMembership, Permission, Role,
                        RoleAssignment, RolePermission, User)


def seed_auth_users(engine):
    """Only synthetic users; passwords generated afresh and never printed."""
    password = secrets.token_urlsafe(24)
    with Session(engine) as s:
        s.add_all([User(id="employee", name="Employee", email="employee@example.invalid"),
                   User(id="admin", name="Admin", email="admin@example.invalid"),
                   User(id="outsider", name="Outsider", email="outsider@example.invalid"),
                   Department(id="hr", name="HR"), Department(id="finance", name="Finance"),
                   Department(id="legal", name="Legal", status="failed"),
                   Role(id="reader", name="Reader", scope="department"),
                   Role(id="administrator", name="Admin", scope="global"),
                   Permission(id="read", code="documents:read"),
                   Permission(id="manage", code="departments:manage")])
        s.flush()
        s.add_all([DepartmentMembership(id="membership", user_id="employee", department_id="hr"),
                   RolePermission(role_id="reader", permission_id="read"),
                   RolePermission(role_id="administrator", permission_id="read"),
                   RolePermission(role_id="administrator", permission_id="manage"),
                   RoleAssignment(user_id="employee", role_id="reader", department_id="hr"),
                   RoleAssignment(user_id="admin", role_id="administrator")])
        s.commit()
        for name in ("employee", "admin", "outsider"):
            set_password(s, f"{name}@example.invalid", password)
    return password


def auth_test_app(engine, settings=None):
    app = create_app(central_engine=engine, auth_settings=settings or AuthSettings(mode="development_password", app_env="test"))

    # These are test-only routes; no Subtask 2.2 business API is introduced.
    @app.get("/test/departments/{department_id}")
    def department(user=Depends(require_department_permission("documents:read"))):
        return {"id": user.id}

    @app.get("/test/global")
    def global_permission(user=Depends(require_global_permission("departments:manage"))):
        return {"id": user.id}

    @app.get("/test/departments/{department_id}/upload")
    def upload(user=Depends(require_department_permission("documents:upload"))):
        return {"id": user.id}
    return app


@pytest.fixture
def auth():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    password = seed_auth_users(engine)
    with TestClient(auth_test_app(engine)) as client:
        yield client, engine, password
    engine.dispose()


def login_headers(client, password, user="employee"):
    response = client.post("/auth/dev/login", json={"email": f"{user}@example.invalid", "password": password})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.mark.parametrize("headers", [{}, {"X-User-ID": "admin"}, {"Authorization": "Basic bogus"},
                                   {"Authorization": "Bearer bogus"},
                                   {"Authorization": "Bearer dev_" + "a" * 43}])
def test_missing_invalid_and_unverified_identity(auth, headers):
    client, _, _ = auth
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/auth/me?user_id=admin&access_token=bogus").status_code == 401


def test_valid_profile_safe_storage_and_server_identity(auth):
    client, engine, password = auth
    headers = login_headers(client, password)
    response = client.get("/auth/me", headers={**headers, "X-User-ID": "admin"})
    assert response.status_code == 200
    profile = response.json()
    assert profile["id"] == "employee"
    assert profile["production_ready"] is False
    assert profile["roles"] == [{"name": "Reader", "scope": "department", "department_id": "hr"}]
    assert set(profile) == {"id", "name", "email", "status", "roles", "authentication_provider", "production_ready"}
    token = headers["Authorization"].split()[1]
    with Session(engine) as s:
        record = s.scalar(select(DevelopmentSession))
        assert record.token_digest == digest(token)
        assert record.token_digest != token
        assert s.get(DevelopmentCredential, "employee").password_hash.startswith("$argon2id$")


@pytest.mark.parametrize("state", ["expired", "revoked", "inactive", "unknown", "rotated", "unenrolled"])
def test_session_and_user_state_checked_on_every_request(auth, state):
    client, engine, password = auth
    headers = login_headers(client, password)
    with Session(engine) as s:
        record = s.scalar(select(DevelopmentSession))
        if state == "expired":
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif state == "revoked":
            record.revoked_at = datetime.now(timezone.utc)
        elif state == "inactive":
            s.get(User, "employee").status = "inactive"
        elif state == "unknown":
            s.delete(s.get(User, "employee"))
        elif state == "rotated":
            s.get(DevelopmentCredential, "employee").version = str(uuid.uuid4())
        else:
            s.delete(s.get(DevelopmentCredential, "employee"))
        s.commit()
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_logout_revokes_only_current_session(auth):
    client, _, password = auth
    first = login_headers(client, password)
    second = login_headers(client, password)
    assert first != second
    assert client.post("/auth/logout", headers=first).status_code == 204
    assert client.get("/auth/me", headers=first).status_code == 401
    assert client.post("/auth/logout", headers=first).status_code == 401
    assert client.get("/auth/me", headers=second).status_code == 200


def test_unknown_inactive_and_wrong_password_are_indistinguishable(auth):
    client, engine, password = auth
    with Session(engine) as s:
        s.get(User, "outsider").status = "inactive"
        s.commit()
    results = [client.post("/auth/dev/login", json={"email": email, "password": candidate})
               for email, candidate in [("absent@example.invalid", password),
                                        ("outsider@example.invalid", password),
                                        ("employee@example.invalid", secrets.token_urlsafe(24))]]
    assert all(r.status_code == 401 for r in results)
    assert results[0].json() == results[1].json() == results[2].json()


def test_password_rotation_revokes_sessions_and_rejects_old_password(auth):
    client, engine, password = auth
    headers = login_headers(client, password)
    new_password = secrets.token_urlsafe(24)
    with Session(engine) as s:
        set_password(s, "employee@example.invalid", new_password)
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.post("/auth/dev/login", json={"email": "employee@example.invalid", "password": password}).status_code == 401
    assert client.get("/auth/me", headers=login_headers(client, new_password)).status_code == 200


def test_rbac_global_department_and_deny_default(auth):
    client, engine, password = auth
    employee = login_headers(client, password)
    admin = login_headers(client, password, "admin")
    outsider = login_headers(client, password, "outsider")
    assert client.get("/test/departments/hr", headers=employee).status_code == 200
    assert client.get("/test/departments/finance", headers=employee).status_code == 403
    assert client.get("/test/global", headers=employee).status_code == 403
    assert client.get("/test/departments/hr/upload", headers=employee).status_code == 403
    assert client.get("/test/departments/hr", headers=outsider).status_code == 403
    assert client.get("/test/global", headers=admin).status_code == 200
    assert client.get("/test/departments/finance", headers=admin).status_code == 200
    assert client.get("/test/departments/legal", headers=admin).status_code == 403
    assert client.get("/test/departments/absent", headers=admin).status_code == 403
    with Session(engine) as s:
        s.get(DepartmentMembership, "membership").status = "inactive"
        s.commit()
    assert client.get("/test/departments/hr", headers=employee).status_code == 403
    assert client.get("/auth/me", headers=employee).json()["roles"] == []


def test_validation_does_not_echo_password_or_accept_identity_claims(auth):
    client, _, password = auth
    for extra in ({"user_id": "admin"}, {"role": "Admin"}, {"unknown": password}):
        response = client.post("/auth/dev/login", json={"email": "employee@example.invalid", "password": password, **extra})
        assert response.status_code == 422
        assert password not in response.text
    response = client.post("/auth/dev/login", json={"email": [], "password": password * 20})
    assert response.status_code == 422
    assert password not in response.text


def test_shared_rate_limit_survives_new_app_and_forwarded_ip_spoof(auth):
    client, engine, password = auth
    body = {"email": "employee@example.invalid", "password": secrets.token_urlsafe(24)}
    for _ in range(5):
        assert client.post("/auth/dev/login", json=body).status_code == 401
    # A second process/app uses the same database counters.
    with TestClient(auth_test_app(engine)) as second:
        response = second.post("/auth/dev/login", json=body, headers={"X-Forwarded-For": "192.0.2.1"})
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0
    with Session(engine) as s:
        assert s.scalar(select(LoginWindow.attempts).where(LoginWindow.attempts == 6)) == 6


def test_case_insensitive_login_and_ambiguous_email_rejected(auth):
    client, engine, password = auth
    response = client.post("/auth/dev/login", json={"email": " EMPLOYEE@EXAMPLE.INVALID ", "password": password})
    assert response.status_code == 200
    # Exercise a legacy pre-users001 database with ambiguous email identities.
    next(index for index in User.__table__.indexes if index.name == "uq_users_normalized_email").drop(engine)
    with Session(engine) as s:
        s.add(User(id="duplicate", name="Duplicate", email="EMPLOYEE@example.invalid"))
        s.commit()
    assert client.post("/auth/dev/login", json={"email": "employee@example.invalid", "password": password}).status_code == 401


@pytest.mark.parametrize("mode,environment", [("development_password", "production"), ("entra", "development"), ("unknown", "test")])
def test_unsafe_auth_configuration_refused(mode, environment):
    with pytest.raises(ValidationError):
        AuthSettings(mode=mode, app_env=environment)


def test_default_disabled_and_bounded_expiry(auth):
    _, engine, _ = auth
    with TestClient(create_app(central_engine=engine, auth_settings=AuthSettings())) as client:
        assert client.get("/auth/me").status_code == 503
        assert client.post("/auth/dev/login", json={"email": "x@y", "password": "x"}).status_code == 503
    for ttl in (0, 3601):
        with pytest.raises(ValidationError):
            AuthSettings(session_seconds=ttl)


@pytest.mark.parametrize("limiter", ["ip", "global"])
def test_ip_and_global_limits_bound_rotating_accounts(auth, limiter):
    _, engine, password = auth
    limits = {f"login_{limiter}_limit": 2}
    settings = AuthSettings(mode="development_password", app_env="test", **limits)
    with TestClient(auth_test_app(engine, settings)) as client:
        for index in range(2):
            assert client.post("/auth/dev/login", json={"email": f"absent{index}@example.invalid", "password": password}).status_code == 401
        assert client.post("/auth/dev/login", json={"email": "employee@example.invalid", "password": password}).status_code == 429


def test_role_removal_and_department_deactivation_apply_without_new_login(auth):
    client, engine, password = auth
    headers = login_headers(client, password, "admin")
    with Session(engine) as s:
        s.get(Department, "hr").status = "inactive"
        s.commit()
    assert client.get("/test/departments/hr", headers=headers).status_code == 403
    with Session(engine) as s:
        s.delete(s.scalar(select(RoleAssignment).where(RoleAssignment.user_id == "admin")))
        s.commit()
    assert client.get("/test/global", headers=headers).status_code == 403


def test_session_ttl_uses_server_configuration(auth):
    _, engine, password = auth
    settings = AuthSettings(mode="development_password", app_env="test", session_seconds=120)
    with TestClient(auth_test_app(engine, settings)) as client:
        response = client.post("/auth/dev/login", json={"email": "employee@example.invalid", "password": password})
        assert response.status_code == 200
        assert response.json()["expires_in"] == 120
    with Session(engine) as s:
        record = s.scalar(select(DevelopmentSession))
        assert (record.expires_at - record.created_at).total_seconds() <= 120
        assert (record.expires_at - record.created_at).total_seconds() >= 115


def test_enrollment_refuses_unknown_inactive_and_short_password(auth):
    _, engine, password = auth
    with Session(engine) as s:
        with pytest.raises(ValueError):
            set_password(s, "absent@example.invalid", password)
        s.get(User, "outsider").status = "inactive"
        s.commit()
        with pytest.raises(ValueError):
            set_password(s, "outsider@example.invalid", password)
        with pytest.raises(ValueError):
            set_password(s, "employee@example.invalid", "short")


def test_database_errors_do_not_expose_parameters(auth):
    _, engine, password = auth
    # Failed DB lookup is a generic 503, with no exception/SQL/hash response.
    DevelopmentCredential.__table__.drop(engine)
    with TestClient(auth_test_app(engine), raise_server_exceptions=False) as client:
        response = client.post("/auth/dev/login", json={"email": "employee@example.invalid", "password": password})
        assert response.status_code == 503
        assert response.json() == {"detail": "Database is unavailable"}
        assert response.headers["cache-control"] == "no-store"


def test_explicit_environment_required_for_development_mode(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "development_password")
    monkeypatch.delenv("APP_ENV", raising=False)
    # Prevent an operator's real local .env from changing this configuration test.
    monkeypatch.setattr("app.auth.config.load_dotenv", lambda *args, **kwargs: None)
    with pytest.raises(ValidationError):
        AuthSettings.from_environment()
