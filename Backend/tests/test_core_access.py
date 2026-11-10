from datetime import timedelta
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.auth.config import AuthSettings
from app.auth.dependencies import get_auth_context
from app.auth.models import DevelopmentCredential, DevelopmentSession
from app.auth.service import digest
from app.main import create_app
from app.models import DepartmentMembership, RoleAssignment, User, utc_now
from test_users import users
from core_access_contract import ADMIN, OPERATIONS, PROTECTED, TOOLING, request


def effective_routes(app):
    # FastAPI 0.162 retains included routers lazily; flatten effective contexts
    # so even include_in_schema=False routes are checked, with their real prefix.
    for route in app.routes:
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        else:
            yield route


def dependency_calls(dependant):
    yield dependant.call
    for child in dependant.dependencies:
        yield from dependency_calls(child)


def test_every_api_operation_has_an_explicit_access_contract():
    app = create_app(auth_settings=AuthSettings(mode="disabled", app_env="test"))
    api_routes = [r for r in effective_routes(app) if isinstance(getattr(r, "original_route", r), APIRoute)]
    actual = {(method, r.path) for r in api_routes for method in r.methods}
    expected = {(op.method, op.path) for op in OPERATIONS}
    assert len(expected) == len(OPERATIONS), "Duplicate API policy entries"
    assert actual == expected, f"Unreviewed/missing API policies: {actual ^ expected}"
    assert {r.path for r in effective_routes(app) if not isinstance(getattr(r, "original_route", r), APIRoute)} == TOOLING
    assert all(op.policy in {"public", "login", "self", "core_admin", "department_admin", "document"} for op in OPERATIONS)
    for op in PROTECTED:
        route = next(r for r in api_routes if r.path == op.path and op.method in r.methods)
        assert get_auth_context in set(dependency_calls(route.dependant)), (op.method, op.path)


@pytest.mark.parametrize("state", ["missing", "forged", "expired", "revoked", "inactive", "unknown", "rotated"])
def test_all_protected_operations_reject_invalid_identity_without_writes(users, state):
    c, engine, headers, _, _ = users
    h = headers["admin"]
    if state == "missing":
        h = {"X-User-ID": "admin"}
    elif state == "forged":
        h = {"Authorization": "Bearer dev_" + "a" * 43, "X-User-ID": "admin"}
    else:
        with Session(engine) as s:
            record = s.scalar(select(DevelopmentSession).where(DevelopmentSession.token_digest == digest(h["Authorization"][7:])))
            if state == "expired": record.expires_at = utc_now() - timedelta(seconds=1)
            if state == "revoked": record.revoked_at = utc_now()
            if state == "inactive": s.get(User, "admin").status = "inactive"
            if state == "unknown": s.delete(s.get(User, "admin"))
            if state == "rotated": s.get(DevelopmentCredential, "admin").version = "rotated-synthetic"
            s.commit()
    from sqlalchemy import event
    statements = []
    def capture(conn, cursor, statement, parameters, context, many):
        statements.append(statement.lstrip().split()[0].upper())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for op in PROTECTED:
            response = request(c, op, h)
            assert response.status_code == 401, (state, op.method, op.path, response.status_code)
            assert response.headers["www-authenticate"] == "Bearer"
            assert response.headers["cache-control"] == "no-store"
        assert not set(statements) & {"INSERT", "UPDATE", "DELETE"}
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("role", ["employee", "manager", "deputy"])
def test_all_admin_operations_reject_department_roles(users, role):
    c, engine, h, deputy, _ = users
    if role != "employee":
        with Session(engine) as s:
            s.add(RoleAssignment(user_id="employee", role_id="manager-user" if role == "manager" else deputy, department_id="hr"))
            s.commit()
    for op in ADMIN:
        response = request(c, op, {**h["employee"], "X-User-ID": "admin"})
        assert response.status_code == 403, (role, op.method, op.path, response.status_code)
        assert response.headers["cache-control"] == "no-store"


def test_disabled_auth_fails_closed_across_every_protected_operation(users):
    _, engine, headers, _, _ = users
    with TestClient(create_app(central_engine=engine, auth_settings=AuthSettings(mode="disabled", app_env="test"))) as c:
        for op in PROTECTED:
            assert request(c, op, headers["admin"]).status_code == 503
        assert c.get("/health").status_code == 200


def test_health_is_public_but_database_health_requires_current_admin(users):
    c, engine, h, _, _ = users
    assert c.get("/health").json() == {"status": "healthy", "service": "backend"}
    response = c.get("/health/db", headers=h["admin"])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    with Session(engine) as s:
        s.query(RoleAssignment).filter_by(user_id="admin", role_id="administrator").delete()
        s.commit()
    assert c.get("/health/db", headers=h["admin"]).status_code == 403


def test_database_health_failure_does_not_disclose_driver_details(users):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    c, engine, h, _, _ = users
    sentinel = "synthetic-sensitive-driver-detail"
    def fail_ping(conn, cursor, statement, parameters, context, many):
        if statement.strip() == "SELECT 1":
            raise OperationalError("SELECT 1", {}, RuntimeError(sentinel))
    event.listen(engine, "before_cursor_execute", fail_ping)
    try:
        response = c.get("/health/db", headers=h["admin"])
        assert response.status_code == 503
        assert sentinel not in response.text
        assert response.json() == {"detail": "Database is unavailable"}
        assert response.headers["cache-control"] == "no-store"
    finally:
        event.remove(engine, "before_cursor_execute", fail_ping)


def test_personal_views_ignore_other_identity_and_never_return_other_ledgers(users):
    from app.usage import service as usage
    from test_usage import POLICY
    c, engine, h, _, _ = users
    with Session(engine) as s:
        usage.configure(s, "admin", POLICY)
        for name in ("employee", "outsider"):
            usage.reserve(s, name, name + "-request", "a" * 64, 10)
        s.commit()
    for name, other in (("employee", "outsider"), ("outsider", "employee")):
        forged = {**h[name], "X-User-ID": other}
        assert c.get("/auth/me?user_id=" + other, headers=forged).json()["id"] == name
        assert c.get("/me/usage?user_id=" + other, headers=forged).json()["user_id"] == name
        for suffix in ("ledger", "requests"):
            rows = c.get(f"/me/usage/{suffix}?user_id={other}", headers=forged).json()
            assert rows and {r["user_id"] for r in rows} == {name}
        assert c.get(f"/admin/users/{other}/memberships", headers=forged).status_code == 403
        assert c.get(f"/admin/users/{other}/roles", headers=forged).status_code == 403
        assert c.get(f"/admin/usage/users/{other}", headers=forged).status_code == 403


@pytest.mark.parametrize("state", ["active", "inactive_membership", "no_role", "archived", "provisioning", "failed"])
def test_department_permission_scope_and_lifecycle(users, state):
    from app.models import Department
    from test_authentication import auth_test_app
    c, engine, h, _, _ = users
    with Session(engine) as s:
        if state == "inactive_membership": s.get(DepartmentMembership, "membership").status = "inactive"
        if state == "no_role": s.query(RoleAssignment).filter_by(user_id="employee").delete()
        if state == "archived": s.get(Department, "hr").archived_at = utc_now()
        if state in {"provisioning", "failed"}: s.get(Department, "hr").status = state
        s.commit()
    with TestClient(auth_test_app(engine)) as client:
        assert client.get("/test/departments/hr", headers=h["employee"]).status_code == (200 if state == "active" else 403)
        assert client.get("/test/departments/finance", headers=h["employee"]).status_code == 403
        assert client.get("/test/departments/legal", headers=h["admin"]).status_code == 403
        assert client.get("/test/departments/hr/upload", headers=h["employee"]).status_code == 403


def test_head_metadata_exception_does_not_enable_failed_tenant_access(users):
    from test_authentication import auth_test_app
    c, engine, h, _, _ = users
    with Session(engine) as s:
        s.add(DepartmentMembership(user_id="employee", department_id="legal"))
        s.add(RoleAssignment(user_id="employee", role_id="manager-user", department_id="legal"))
        s.commit()
    response = c.get("/me/department", headers=h["employee"])
    assert response.status_code == 200 and response.json()["status"] == "failed"
    with TestClient(auth_test_app(engine)) as client:
        assert client.get("/test/departments/legal", headers=h["employee"]).status_code == 403


def test_scoped_management_codes_do_not_become_global_admin_access(users):
    from app.models import RolePermission
    c, engine, h, deputy, _ = users
    with Session(engine) as s:
        s.add_all([RolePermission(role_id=deputy, permission_id="users-manage"),
            RolePermission(role_id=deputy, permission_id="manage"),
            RoleAssignment(user_id="employee", role_id=deputy, department_id="hr")])
        s.commit()
    for op in ADMIN:
        assert request(c, op, h["employee"]).status_code == 403


@pytest.mark.parametrize("case", ["other_department", "failed", "archived", "registry_not_ready"])
def test_tenant_router_denies_before_creating_a_tenant_connection(users, monkeypatch, case):
    import importlib.util
    from pathlib import Path
    import sys
    from types import ModuleType
    from unittest.mock import Mock
    from app.models import Department, DepartmentDatabase
    _, engine, _, _, _ = users
    # Load only the Core router with an injected synthetic central engine. Never
    # import the legacy root tests or resolve the real runtime database settings.
    database = ModuleType("app.database")
    database.engine = engine
    monkeypatch.setitem(sys.modules, "app.database", database)
    spec = importlib.util.spec_from_file_location("core_router_isolation_probe",
        Path(__file__).resolve().parents[1] / "app" / "database_router.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    connect = Mock(side_effect=AssertionError("Unauthorized tenant connection attempted"))
    monkeypatch.setattr(module, "get_department_engine", connect)
    target, actor, error = "finance", "employee", PermissionError
    with Session(engine) as s:
        if case == "failed": target, actor = "legal", "admin"
        if case == "archived":
            target = "hr"
            s.get(Department, target).archived_at = utc_now()
        if case == "registry_not_ready":
            target, error = "hr", ValueError
            s.add(DepartmentDatabase(department_id="hr", database_name="synthetic_hr",
                credential_key="SYNTHETIC_HR", status="failed"))
        s.commit()
    with pytest.raises(error):
        with module.authorized_department_session(actor, target, "documents:read"):
            pytest.fail("Unauthorized tenant session yielded")
    connect.assert_not_called()
