import secrets
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.config import AuthSettings
from app.auth.models import DevelopmentCredential, DevelopmentSession
from app.departments.models import CreationGrant
from app.departments.service import DepartmentError
from app.main import create_app
from app.models import (Base, Department, DepartmentMembership, Permission, Role, RoleAssignment, RolePermission, User)
from app.services.authorization import has_permission
from app.users import service
from app.users.models import UserAudit
from test_authentication import login_headers, seed_auth_users


def seed_user_admin(engine):
    with Session(engine) as s:
        s.add(Permission(id="users-manage", code="users:manage"))
        s.add_all([Role(id="manager-user", name="Department Manager", scope="department"),
                   Role(id="employee-user", name="Employee", scope="department")])
        deputy = s.scalar(select(Role).where(Role.name == "Deputy Department Manager"))
        if deputy is None:
            deputy = Role(id="deputy-user", name="Deputy Department Manager", scope="department")
            s.add(deputy)
        s.flush()
        deputy_id = deputy.id
        s.add_all([RolePermission(role_id="administrator", permission_id="users-manage"),
                   RolePermission(role_id="employee-user", permission_id="read"),
                   RolePermission(role_id=deputy_id, permission_id="read")])
        s.commit()
    return deputy_id


@pytest.fixture
def users():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    password = seed_auth_users(engine)
    deputy = seed_user_admin(engine)
    app = create_app(central_engine=engine, auth_settings=AuthSettings(mode="development_password", app_env="test"))
    with TestClient(app) as client:
        headers = {name: login_headers(client, password, name) for name in ("admin", "employee", "outsider")}
        yield client, engine, headers, deputy, password
    engine.dispose()


@pytest.mark.parametrize("method,path,body", [("get", "/admin/users", None),
    ("post", "/admin/users", {"name": "New", "email": "new@example.invalid"}),
    ("patch", "/admin/users/employee/status", {"status": "inactive"}),
    ("put", "/admin/users/employee/memberships/finance", {"status": "active"}),
    ("post", "/admin/users/employee/roles", {"role_id": "administrator"}),
    ("get", "/admin/roles", None), ("get", "/admin/users/admin/audit", None)])
def test_admin_boundaries_and_header_spoofing(users, method, path, body):
    client, engine, headers, _, _ = users
    for credential, expected in [({"X-User-ID": "admin"}, 401), (headers["employee"], 403),
                                 ({"Authorization": "Bearer invalid"}, 401)]:
        response = client.request(method.upper(), path, headers=credential, json=body)
        assert response.status_code == expected
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(UserAudit)) == 0


def test_safe_user_creation_uniqueness_and_no_password_or_roles(users):
    client, engine, headers, _, _ = users
    body = {"name": " مستخدم تجريبي ", "email": " NEW@EXAMPLE.INVALID "}
    response = client.post("/admin/users", headers=headers["admin"], json=body)
    assert response.status_code == 201
    value = response.json()
    assert set(value) == {"id", "name", "email", "status", "created_at"}
    assert value["email"] == "new@example.invalid"
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/admin/users", headers=headers["admin"], json=body).status_code == 409
    for field in ("password", "role_id", "status", "actor_user_id"):
        assert client.post("/admin/users", headers=headers["admin"], json={**body, field: "forged"}).status_code == 422
    assert client.get("/admin/users?limit=101", headers=headers["admin"]).status_code == 422
    assert client.get("/admin/users/missing", headers=headers["admin"]).status_code == 404
    with Session(engine) as s:
        assert s.get(DevelopmentCredential, value["id"]) is None
        assert s.scalar(select(RoleAssignment).where(RoleAssignment.user_id == value["id"])) is None
        assert s.scalar(select(UserAudit)).actor_user_id == "admin"
    assert client.post(f"/admin/users/{value['id']}/roles", headers=headers["admin"],
        json={"role_id": "administrator"}).status_code == 409


def test_disable_reactivate_never_restores_old_session(users):
    client, engine, headers, _, password = users
    assert client.patch("/admin/users/employee/status", headers=headers["admin"], json={"status": "inactive"}).status_code == 200
    assert client.get("/auth/me", headers=headers["employee"]).status_code == 401
    assert client.patch("/admin/users/employee/status", headers=headers["admin"], json={"status": "active"}).status_code == 200
    assert client.get("/auth/me", headers=headers["employee"]).status_code == 401
    assert client.get("/auth/me", headers=login_headers(client, password)).status_code == 200
    with Session(engine) as s:
        assert s.scalar(select(DevelopmentSession.revoked_at).where(DevelopmentSession.user_id == "employee")) is not None


def test_last_admin_and_self_promotion_protected(users):
    client, _, headers, _, _ = users
    assert client.patch("/admin/users/admin/status", headers=headers["admin"], json={"status": "inactive"}).status_code == 409
    assignment = client.get("/admin/users/admin/roles", headers=headers["admin"]).json()[0]["id"]
    assert client.delete(f"/admin/users/admin/roles/{assignment}", headers=headers["admin"]).status_code == 409
    assert client.post("/admin/users/admin/roles", headers=headers["admin"], json={"role_id": "administrator"}).status_code == 403
    assert client.put("/admin/users/admin/memberships/hr", headers=headers["admin"], json={"status": "active"}).status_code == 403
    # A different account may be explicitly made Admin by the current Admin.
    assert client.post("/admin/users/outsider/roles", headers=headers["admin"], json={"role_id": "administrator"}).status_code == 201
    assert client.patch("/admin/users/admin/status", headers=headers["admin"], json={"status": "inactive"}).status_code == 200


def test_membership_and_fixed_roles_isolate_departments_and_refresh_access(users):
    client, engine, headers, deputy, _ = users
    assert client.post("/admin/users/employee/roles", headers=headers["admin"],
        json={"role_id": deputy, "department_id": "finance"}).status_code == 409
    assert client.put("/admin/users/employee/memberships/finance", headers=headers["admin"], json={"status": "active"}).status_code == 200
    for department in ("hr", "finance"):
        assert client.post("/admin/users/employee/roles", headers=headers["admin"],
            json={"role_id": "employee-user", "department_id": department}).status_code == 201
    assert client.post("/admin/users/employee/roles", headers=headers["admin"],
        json={"role_id": deputy, "department_id": "finance"}).status_code == 201
    assert client.put("/admin/users/outsider/memberships/legal", headers=headers["admin"], json={"status": "active"}).status_code == 409
    with Session(engine) as s:
        assert has_permission(s, "employee", "documents:read", "finance")
        assert not has_permission(s, "outsider", "documents:read", "finance")
    assert client.put("/admin/users/employee/memberships/finance", headers=headers["admin"], json={"status": "inactive"}).status_code == 200
    with Session(engine) as s:
        assert not has_permission(s, "employee", "documents:read", "finance")
    assert client.get("/auth/me", headers=headers["employee"]).status_code == 401
    assert client.get("/admin/users/employee/memberships", headers=headers["admin"]).status_code == 200


def test_role_scope_and_ownership_cannot_be_bypassed(users):
    client, engine, headers, deputy, _ = users
    for payload in ({"role_id": "reader", "department_id": "hr"}, {"role_id": deputy},
                    {"role_id": "administrator", "department_id": "hr"}):
        assert client.post("/admin/users/employee/roles", headers=headers["admin"], json=payload).status_code == 422
    assert client.post("/admin/users/employee/roles", headers=headers["admin"],
        json={"role_id": "manager-user", "department_id": "hr"}).status_code == 409
    with Session(engine) as s:
        s.add(CreationGrant(name="HR", normalized_name="hr", department_id="hr", head_user_id="employee", state="consumed"))
        s.commit()
    result = client.post("/admin/users/employee/roles", headers=headers["admin"],
        json={"role_id": "manager-user", "department_id": "hr"})
    assert result.status_code == 201
    assignment = result.json()["id"]
    assert client.delete(f"/admin/users/outsider/roles/{assignment}", headers=headers["admin"]).status_code == 404
    assert client.delete(f"/admin/users/employee/roles/{assignment}", headers=headers["admin"]).status_code == 204
    with Session(engine) as s:
        assert s.scalar(select(CreationGrant).where(CreationGrant.head_user_id == "employee")).state == "consumed"


def test_audit_failure_rolls_back_user_status_and_session_revocation(users):
    client, engine, headers, _, _ = users
    with patch.object(service, "audit", side_effect=DepartmentError(503, "synthetic_audit_failure")):
        assert client.patch("/admin/users/employee/status", headers=headers["admin"], json={"status": "inactive"}).status_code == 503
    assert client.get("/auth/me", headers=headers["employee"]).status_code == 200
    with Session(engine) as s:
        assert s.get(User, "employee").status == "active"
    assert client.get("/admin/users/employee/audit", headers=headers["admin"]).json() == []


def test_unenrolled_admin_does_not_satisfy_last_admin_guard(users):
    client, engine, headers, _, _ = users
    with Session(engine) as s:
        s.add(User(id="unenrolled", name="Offline", email="offline@example.invalid"))
        s.flush()
        s.add(RoleAssignment(user_id="unenrolled", role_id="administrator"))
        s.commit()
    assert client.patch("/admin/users/admin/status", headers=headers["admin"], json={"status": "inactive"}).status_code == 409


@pytest.mark.parametrize("bad_scope", [False, True])
def test_user_migration_preflight_preserves_ambiguous_legacy_data(monkeypatch, bad_scope):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import inspect, text
    path = Path(__file__).resolve().parents[1] / "alembic/versions/users001_user_administration.py"
    spec = importlib.util.spec_from_file_location("users_migration_test", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE users(id TEXT,email TEXT)"))
        c.execute(text("CREATE TABLE roles(id TEXT,name TEXT,scope TEXT)"))
        c.execute(text("INSERT INTO users VALUES ('one','Example@example.invalid')"))
        if bad_scope:
            c.execute(text("INSERT INTO roles VALUES ('bad','Deputy Department Manager','global')"))
        else:
            c.execute(text("INSERT INTO users VALUES ('two',' example@example.invalid ')"))
        monkeypatch.setattr(migration.op, "get_bind", lambda: c)
        with pytest.raises(RuntimeError, match="reconciliation"):
            migration.upgrade()
        assert "user_audit_events" not in inspect(c).get_table_names()
        assert c.scalar(text("SELECT count(*) FROM users")) == (1 if bad_scope else 2)
    engine.dispose()
