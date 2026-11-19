from concurrent.futures import ThreadPoolExecutor
import secrets
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings
from app.auth.enroll import set_password
from app.auth.models import DevelopmentSession
from app.main import create_app
from app.models import User, RoleAssignment
from app.users.models import UserAudit
from app.users.service import admin_ids
from test_authentication import login_headers
from test_authentication_postgres import isolated_postgres, pytestmark
from test_users import seed_user_admin


@pytest.fixture(scope="module")
def user_pg(isolated_postgres):
    bootstrap, runtime, password = isolated_postgres
    deputy = seed_user_admin(bootstrap)
    with bootstrap.begin() as c:
        c.execute(text("GRANT INSERT ON users,department_memberships,role_assignments TO auth_test_api"))
        c.execute(text("GRANT UPDATE(status) ON users,department_memberships TO auth_test_api"))
        c.execute(text("GRANT DELETE ON role_assignments TO auth_test_api"))
        c.execute(text("GRANT SELECT ON department_creation_grants TO auth_test_api"))
        c.execute(text("GRANT SELECT,INSERT ON user_audit_events TO auth_test_api"))
    app = create_app(central_engine=runtime, auth_settings=AuthSettings(mode="development_password", app_env="test"))
    with TestClient(app) as client:
        headers = login_headers(client, password, "admin")
        yield bootstrap, runtime, app, client, headers, deputy, password


def create_enrolled_user(bootstrap, client, headers):
    email = str(uuid.uuid4()) + "@example.invalid"
    response = client.post("/admin/users", headers=headers, json={"name": "Synthetic", "email": email})
    assert response.status_code == 201
    password = secrets.token_urlsafe(24)
    with Session(bootstrap) as s:
        set_password(s, email, password)
    return response.json()["id"], email, password


def test_concurrent_canonical_email_creation_and_safe_profile(user_pg):
    _, _, app, _, headers, _, _ = user_pg
    email = str(uuid.uuid4()) + "@example.invalid"
    def attempt(index):
        with TestClient(app) as c:
            return c.post("/admin/users", headers=headers,
                json={"name": "Concurrent", "email": email.upper() if index else email}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sorted(results) == [201, 409, 409, 409]


def test_disable_reactivate_and_scoped_membership_roles(user_pg):
    bootstrap, _, _, client, headers, deputy, _ = user_pg
    identity, email, password = create_enrolled_user(bootstrap, client, headers)
    assert client.put(f"/admin/users/{identity}/memberships/hr", headers=headers, json={"status": "active"}).status_code == 200
    assert client.post(f"/admin/users/{identity}/roles", headers=headers,
        json={"role_id": deputy, "department_id": "hr"}).status_code == 201
    assert client.post(f"/admin/users/{identity}/roles", headers=headers,
        json={"role_id": "manager-user", "department_id": "hr"}).status_code == 409
    response = client.post("/auth/dev/login", json={"email": email, "password": password})
    assert response.status_code == 200
    bearer = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert client.get("/auth/me", headers=bearer).json()["roles"][0]["department_id"] == "hr"
    assert client.patch(f"/admin/users/{identity}/status", headers=headers, json={"status": "inactive"}).status_code == 200
    assert client.get("/auth/me", headers=bearer).status_code == 401
    assert client.patch(f"/admin/users/{identity}/status", headers=headers, json={"status": "active"}).status_code == 200
    assert client.get("/auth/me", headers=bearer).status_code == 401


def test_audit_failure_rolls_back_user_and_sessions(user_pg):
    bootstrap, _, _, client, headers, _, _ = user_pg
    identity, email, password = create_enrolled_user(bootstrap, client, headers)
    response = client.post("/auth/dev/login", json={"email": email, "password": password})
    assert response.status_code == 200
    bearer = {"Authorization": "Bearer " + response.json()["access_token"]}
    with bootstrap.begin() as c:
        c.execute(text("REVOKE INSERT ON user_audit_events FROM auth_test_api"))
    try:
        assert client.patch(f"/admin/users/{identity}/status", headers=headers, json={"status": "inactive"}).status_code == 503
    finally:
        with bootstrap.begin() as c:
            c.execute(text("GRANT INSERT ON user_audit_events TO auth_test_api"))
    assert client.get("/auth/me", headers=bearer).status_code == 200
    with Session(bootstrap) as s:
        assert s.get(User, identity).status == "active"


def test_api_cannot_change_credentials_policy_or_audit(user_pg):
    _, runtime, _, _, _, _, _ = user_pg
    for statement in ("DELETE FROM users", "UPDATE users SET email='forged@example.invalid'",
        "UPDATE roles SET scope='global'", "DELETE FROM permissions", "DELETE FROM user_audit_events",
        "UPDATE user_audit_events SET action='forged'", "UPDATE auth_development_credentials SET password_hash='forged'"):
        with runtime.connect() as c, pytest.raises(DBAPIError) as error:
            c.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"


def test_two_admins_cannot_concurrently_disable_the_last_admin(user_pg):
    bootstrap, _, app, client, headers, _, password = user_pg
    identity, email, candidate_password = create_enrolled_user(bootstrap, client, headers)
    assert client.post(f"/admin/users/{identity}/roles", headers=headers, json={"role_id": "administrator"}).status_code == 201
    response = client.post("/auth/dev/login", json={"email": email, "password": candidate_password})
    assert response.status_code == 200
    other = {"Authorization": "Bearer " + response.json()["access_token"]}
    def disable(item):
        target, credential = item
        with TestClient(app) as c:
            return c.patch(f"/admin/users/{target}/status", headers=credential, json={"status": "inactive"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(disable, [("admin", headers), (identity, other)]))
    assert sorted(results) == [200, 409]
    with Session(bootstrap) as s:
        assert len(admin_ids(s)) == 1
    # Independently test loss of the Admin role, using fresh enrolled identities.
    with Session(bootstrap) as s:
        for target in ("admin", identity):
            s.get(User, target).status = "active"
        s.commit()
    credentials = [("admin", login_headers(client, password, "admin"))]
    response = client.post("/auth/dev/login", json={"email": email, "password": candidate_password})
    assert response.status_code == 200
    credentials.append((identity, {"Authorization": "Bearer " + response.json()["access_token"]}))
    with Session(bootstrap) as s:
        assignments = {row.user_id: row.id for row in s.scalars(select(RoleAssignment).where(RoleAssignment.role_id == "administrator"))}
    def revoke(item):
        target, credential = item
        with TestClient(app) as c:
            return c.delete(f"/admin/users/{target}/roles/{assignments[target]}", headers=credential).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(revoke, credentials))
    assert sorted(results) == [204, 409]
    with Session(bootstrap) as s:
        assert len(admin_ids(s)) == 1
