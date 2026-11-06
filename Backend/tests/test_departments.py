from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.config import AuthSettings
from app.departments.models import CreationGrant, DepartmentAudit
from app.departments import service
from app.main import create_app
from app.models import (Base, Department, DepartmentDatabase, DepartmentMembership,
                        Role, RoleAssignment, RolePermission, User)
from app.services.authorization import has_permission
from test_authentication import login_headers, seed_auth_users


@pytest.fixture
def departments():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    password = seed_auth_users(engine)
    with Session(engine) as s:
        s.add(Role(id="manager", name="Department Manager", scope="department"))
        s.flush()
        s.add(RolePermission(role_id="manager", permission_id="read"))
        s.commit()
    app = create_app(central_engine=engine, auth_settings=AuthSettings(mode="development_password", app_env="test"))
    with TestClient(app) as client:
        headers = {name: login_headers(client, password, name) for name in ("admin", "employee", "outsider")}
        yield client, engine, headers
    engine.dispose()


def issue(client, headers, head="employee", name="القانونية التجريبية"):
    return client.post("/admin/department-creation-grants", headers=headers["admin"],
                       json={"head_user_id": head, "name": name})


def consume(client, headers, user="employee", body=None):
    return client.post("/departments/from-grant", headers=headers[user], json={} if body is None else body)


def test_grant_creates_no_department_then_scoped_head_registers_once(departments):
    client, engine, headers = departments
    with Session(engine) as s:
        before = s.scalar(select(func.count()).select_from(Department))
    response = issue(client, headers)
    assert response.status_code == 201
    grant = response.json()
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Department)) == before
    assert client.get("/me/department-creation-grant", headers=headers["employee"]).json()["id"] == grant["id"]
    assert client.get("/me/department-creation-grant", headers=headers["outsider"]).status_code == 404
    result = consume(client, headers)
    assert result.status_code == 202
    value = result.json()
    assert value["status"] == "provisioning"
    assert set(value) == {"id", "name", "status", "provisioning_status", "last_error_code", "attempt_count", "archived_at"}
    assert result.headers["cache-control"] == "no-store"
    assert consume(client, headers).status_code == 409
    assert client.get("/me/department", headers=headers["employee"]).json()["id"] == value["id"]
    assert client.get("/me/department", headers=headers["outsider"]).status_code == 404
    with Session(engine) as s:
        grant = s.get(CreationGrant, grant["id"])
        assert grant.state == "consumed" and grant.department_id == value["id"]
        assignment = s.scalar(select(RoleAssignment).where(RoleAssignment.role_id == "manager"))
        assert assignment.user_id == "employee" and assignment.department_id == value["id"]
        assert s.scalar(select(DepartmentDatabase)).status == "provisioning"
        assert s.scalar(select(func.count()).select_from(DepartmentAudit)) == 2
        assert not has_permission(s, "employee", "documents:read", value["id"])
        s.get(Department, value["id"]).status = "active"
        s.flush()
        assert has_permission(s, "employee", "documents:read", value["id"])
        assert not has_permission(s, "employee", "documents:read", "finance")


@pytest.mark.parametrize("path,body", [("/admin/departments", {"name": "Forbidden"}),
    ("/admin/department-creation-grants", {"head_user_id": "employee", "name": "Forbidden"}),
    ("/admin/departments/hr/archive", {}), ("/admin/department-creation-grants/no/revoke", {}),
    ("/admin/departments/hr/head", {"head_user_id": "employee"})])
def test_admin_operations_require_verified_global_permission(departments, path, body):
    client, engine, headers = departments
    assert client.post(path, json=body, headers={"X-User-ID": "admin"}).status_code == 401
    assert client.post(path, json=body, headers=headers["employee"]).status_code == 403
    assert client.post(path, json=body, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/admin/departments", headers=headers["employee"]).status_code == 403
    assert client.get("/admin/departments/hr", headers=headers["employee"]).status_code == 403
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(DepartmentAudit)) == 0


def test_unknown_inactive_and_existing_head_cannot_receive_grant(departments):
    client, engine, headers = departments
    assert issue(client, headers, head="unknown").status_code == 409
    with Session(engine) as s:
        s.get(User, "outsider").status = "inactive"
        s.add(RoleAssignment(user_id="employee", role_id="manager", department_id="hr"))
        s.commit()
    assert issue(client, headers, head="outsider").status_code == 409
    assert issue(client, headers).json()["detail"] == "existing_head_requires_reconciliation"


def test_name_normalization_reservations_and_revoke(departments):
    client, _, headers = departments
    grant = issue(client, headers, name="  Legal   Test  ").json()
    assert grant["name"] == "Legal Test"
    assert issue(client, headers, head="outsider", name="LEGAL TEST").status_code == 409
    assert issue(client, headers, name="Another").status_code == 409
    assert client.post("/admin/departments", headers=headers["admin"], json={"name": "legal test"}).status_code == 409
    assert client.post(f"/admin/department-creation-grants/{grant['id']}/revoke", headers=headers["admin"]).status_code == 200
    assert consume(client, headers).status_code == 403
    assert issue(client, headers, name="Another").status_code == 201
    assert issue(client, headers, head="outsider", name="Legal Test").status_code == 201


@pytest.mark.parametrize("body", [{"name": "Forged"}, {"user_id": "outsider"}, {"head_user_id": "outsider"},
                                   {"status": "active"}, {"grant_id": "another"}])
def test_consume_does_not_accept_identity_name_status_or_other_grant(departments, body):
    client, _, headers = departments
    issue(client, headers)
    assert consume(client, headers, body=body).status_code == 422
    assert consume(client, headers, user="outsider").status_code == 403


def test_failure_and_archival_do_not_reset_creation_right_or_erase_state(departments):
    client, engine, headers = departments
    issue(client, headers)
    identity = consume(client, headers).json()["id"]
    with Session(engine) as s:
        department = s.get(Department, identity)
        department.status = "failed"
        registry = s.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id == identity))
        registry.status, registry.last_error_code = "failed", "tenant_contract_unavailable"
        registry.claim_token = "old-worker"
        s.commit()
    assert client.post(f"/admin/departments/{identity}/archive", headers=headers["employee"]).status_code == 403
    response = client.post(f"/admin/departments/{identity}/archive", headers=headers["admin"])
    assert response.json()["status"] == "archived"
    assert response.json()["provisioning_status"] == "failed"
    assert response.json()["last_error_code"] == "tenant_contract_unavailable"
    assert consume(client, headers).status_code == 409
    assert issue(client, headers, name="Second").status_code == 409
    assert client.post(f"/admin/department-creation-grants/{client.get('/me/department-creation-grant', headers=headers['employee']).json()['id']}/revoke", headers=headers["admin"]).status_code == 409
    assert client.post(f"/admin/departments/{identity}/archive", headers=headers["admin"]).status_code == 200
    with Session(engine) as s:
        assert s.get(Department, identity).status == "failed"
        assert s.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id == identity)).claim_token is None
        assert s.scalar(select(func.count()).select_from(DepartmentAudit).where(DepartmentAudit.action == "department_archived")) == 1


def test_admin_multiple_unowned_departments_and_explicit_binding(departments):
    client, engine, headers = departments
    ids = [client.post("/admin/departments", headers=headers["admin"], json={"name": name}).json()["id"]
           for name in ("One", "Two")]
    assert len(set(ids)) == 2
    response = client.post(f"/admin/departments/{ids[0]}/head", headers=headers["admin"], json={"head_user_id": "employee"})
    assert response.status_code == 200
    assert client.get("/me/department", headers=headers["employee"]).json()["id"] == ids[0]
    assert client.post(f"/admin/departments/{ids[1]}/head", headers=headers["admin"], json={"head_user_id": "employee"}).status_code == 409
    assert issue(client, headers).status_code == 409
    assert client.get("/admin/departments?limit=1", headers=headers["admin"]).status_code == 200
    assert len(client.get("/admin/departments?state=provisioning", headers=headers["admin"]).json()) == 2
    assert client.get("/admin/departments?limit=101", headers=headers["admin"]).status_code == 422
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(CreationGrant).where(CreationGrant.state == "consumed")) == 2


def test_missing_head_role_and_audit_failure_roll_back_entire_registration(departments):
    client, engine, headers = departments
    grant = issue(client, headers).json()
    with Session(engine) as s:
        s.delete(s.get(Role, "manager"))
        s.commit()
    assert consume(client, headers).status_code == 503
    with Session(engine) as s:
        assert s.get(CreationGrant, grant["id"]).state == "pending"
        assert s.scalar(select(func.count()).select_from(DepartmentDatabase)) == 0
        s.add(Role(id="manager2", name="Department Manager", scope="department"))
        s.commit()
    def broken_audit(*args, **kwargs):
        raise service.DepartmentError(503, "test_audit_failure")
    with patch.object(service, "audit", broken_audit):
        assert consume(client, headers).status_code == 503
    with Session(engine) as s:
        assert s.get(CreationGrant, grant["id"]).state == "pending"
        assert s.scalar(select(func.count()).select_from(DepartmentDatabase)) == 0
        assert s.scalar(select(func.count()).select_from(RoleAssignment).where(RoleAssignment.role_id == "manager2")) == 0


def test_archived_active_department_denies_global_and_department_access(departments):
    client, engine, headers = departments
    client.post("/admin/departments/hr/archive", headers=headers["admin"])
    with Session(engine) as s:
        assert not has_permission(s, "employee", "documents:read", "hr")
        assert not has_permission(s, "admin", "documents:read", "hr")
    assert client.get("/auth/me", headers=headers["employee"]).json()["roles"] == []


def test_production_demo_seed_fails_closed(monkeypatch):
    from seed import seed
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError):
        seed(development_fixtures=True)
    monkeypatch.setenv("APP_ENV", "development")
    with pytest.raises(RuntimeError):
        seed()


def test_day1_registration_wrapper_uses_same_ledger(departments):
    _, engine, _ = departments
    from app.services.department_provisioning import request_department_creation
    result = request_department_creation("admin", "Wrapper", central_engine=engine)
    assert result["status"] == "provisioning" and result["database_name"].startswith("dept_")
    with pytest.raises(PermissionError):
        request_department_creation("employee", "No", central_engine=engine)
    with pytest.raises(ValueError):
        request_department_creation("admin", "WRAPPER", central_engine=engine)


def test_explicit_legacy_head_binding_and_inactive_session_denial(departments):
    client, engine, headers = departments
    with Session(engine) as s:
        s.add(CreationGrant(name="HR", normalized_name="hr", department_id="hr", state="consumed"))
        s.add(RoleAssignment(user_id="employee", role_id="manager", department_id="hr"))
        s.commit()
    assert issue(client, headers).status_code == 409
    response = client.post("/admin/departments/hr/head", headers=headers["admin"], json={"head_user_id": "employee"})
    assert response.status_code == 200
    assert client.post("/admin/departments/hr/head", headers=headers["admin"], json={"head_user_id": "outsider"}).status_code == 409
    assert issue(client, headers, name="Second").status_code == 409
    with Session(engine) as s:
        s.get(User, "admin").status = "inactive"
        s.commit()
    assert client.get("/admin/departments", headers=headers["admin"]).status_code == 401


def test_admin_head_creation_and_safe_errors(departments):
    client, engine, headers = departments
    response = client.post("/admin/departments", headers=headers["admin"],
        json={"name": "Direct Head", "head_user_id": "employee"})
    assert response.status_code == 202
    assert client.get("/me/department", headers=headers["employee"]).json()["id"] == response.json()["id"]
    assert client.post("/admin/departments", headers=headers["admin"],
        json={"name": "Second", "head_user_id": "employee"}).status_code == 409
    for name in ("   ", "Bad\u200bName"):
        assert client.post("/admin/departments", headers=headers["admin"], json={"name": name}).status_code == 422
    assert client.get("/admin/departments/unknown", headers=headers["admin"]).status_code == 404
    with Session(engine) as s:
        registry = s.scalar(select(DepartmentDatabase))
        registry.last_error_code = "internal-sensitive-unrecognized-message"
        s.commit()
    assert client.get(f"/admin/departments/{response.json()['id']}", headers=headers["admin"]).json()["last_error_code"] is None


@pytest.mark.parametrize("names", [[" HR ", "hr"], ["   "], ["bad\u200bname"]])
def test_migration_rejects_ambiguous_legacy_names_before_schema_changes(monkeypatch, names):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import inspect, text
    path = Path(__file__).resolve().parents[1] / "alembic/versions/dept001_creation_grants_archival.py"
    spec = importlib.util.spec_from_file_location("department_migration_test", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE departments(id TEXT PRIMARY KEY,name TEXT NOT NULL)"))
        for index, name in enumerate(names):
            c.execute(text("INSERT INTO departments(id,name) VALUES (:id,:name)"), {"id": str(index), "name": name})
        monkeypatch.setattr(migration.op, "get_bind", lambda: c)
        with pytest.raises(RuntimeError, match="explicit reconciliation"):
            migration.upgrade()
        assert {column["name"] for column in inspect(c).get_columns("departments")} == {"id", "name"}
        assert c.scalar(text("SELECT count(*) FROM departments")) == len(names)
    engine.dispose()


def test_explicit_development_seed_is_idempotent_and_reconcilable(monkeypatch):
    from seed import seed
    monkeypatch.setenv("APP_ENV", "development")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    seed(development_fixtures=True, central_engine=engine)
    seed(development_fixtures=True, central_engine=engine)
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Department)) == 2
        assert s.scalar(select(func.count()).select_from(CreationGrant)) == 2
        admin = s.scalar(select(User).where(User.email == "admin@example.com"))
        head = s.scalar(select(User).where(User.email == "hr.manager@example.com"))
        hr = s.scalar(select(Department).where(Department.name == "HR"))
        service.bind_head(s, admin.id, hr.id, head.id)
        s.commit()
        assert s.scalar(select(CreationGrant.head_user_id).where(CreationGrant.department_id == hr.id)) == head.id
    engine.dispose()
