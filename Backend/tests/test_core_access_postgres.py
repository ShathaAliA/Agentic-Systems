"""Cross-router contract tests on a fresh, backed-up development cluster only."""
from datetime import timedelta
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.auth.config import AuthSettings
from app.auth.models import DevelopmentSession
from app.auth.service import digest
from app.main import create_app
from app.models import DepartmentMembership, RoleAssignment, utc_now
from test_authentication import auth_test_app, login_headers
from test_authentication_postgres import isolated_postgres, pytestmark
from test_users import seed_user_admin
from core_access_contract import ADMIN, PROTECTED, request


@pytest.fixture(scope="module")
def core_pg(isolated_postgres):
    bootstrap, runtime, password = isolated_postgres
    deputy = seed_user_admin(bootstrap)
    with bootstrap.begin() as c:
        c.execute(text("GRANT SELECT ON department_databases,department_creation_grants,user_audit_events,role_policies,role_policy_audit_events,usage_policy,usage_policy_audit,usage_requests,usage_entries TO auth_test_api"))
        c.execute(text("GRANT UPDATE(status) ON users TO auth_test_api"))
        c.execute(text("GRANT INSERT ON user_audit_events TO auth_test_api"))
    with TestClient(create_app(central_engine=runtime, auth_settings=AuthSettings(mode="development_password", app_env="test"))) as client:
        headers = {name: login_headers(client, password, name) for name in ("admin", "employee", "outsider")}
        yield bootstrap, runtime, client, headers, password, deputy


def test_missing_and_forged_credentials_cannot_reach_any_core_operation(core_pg):
    _, _, c, _, _, _ = core_pg
    for h in ({"X-User-ID": "admin"}, {"Authorization": "Bearer dev_" + "a" * 43}):
        for op in PROTECTED:
            result = request(c, op, h)
            assert result.status_code == 401, (op.method, op.path, result.status_code)
            assert result.headers["cache-control"] == "no-store"


def test_scoped_roles_deny_all_admin_operations_and_admin_can_read(core_pg):
    bootstrap, runtime, c, h, _, deputy = core_pg
    with Session(bootstrap) as s:
        s.add_all([RoleAssignment(user_id="employee", role_id="manager-user", department_id="hr"),
                   RoleAssignment(user_id="outsider", role_id=deputy, department_id="finance")])
        s.commit()
    for identity in ("employee", "outsider"):
        for op in ADMIN:
            assert request(c, op, {**h[identity], "X-User-ID": "admin"}).status_code == 403
    for path in ("/admin/users", "/admin/departments", "/admin/permissions", "/admin/usage/summary", "/health/db"):
        assert c.get(path, headers=h["admin"]).status_code == 200
    with runtime.connect() as conn:
        assert conn.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one() == (False, False, False, False)


def test_hr_finance_scope_and_archive_checked_on_every_request(core_pg):
    bootstrap, runtime, _, h, _, _ = core_pg
    with TestClient(auth_test_app(runtime)) as c:
        assert c.get("/test/departments/hr", headers=h["employee"]).status_code == 200
        assert c.get("/test/departments/finance", headers=h["employee"]).status_code == 403
        assert c.get("/test/departments/legal", headers=h["admin"]).status_code == 403
        for statement, restore in [
            ("UPDATE department_memberships SET status='inactive' WHERE id='membership'", "UPDATE department_memberships SET status='active' WHERE id='membership'"),
            ("UPDATE departments SET archived_at=CURRENT_TIMESTAMP WHERE id='hr'", "UPDATE departments SET archived_at=NULL WHERE id='hr'"),
            ("UPDATE departments SET status='provisioning' WHERE id='hr'", "UPDATE departments SET status='active' WHERE id='hr'")]:
            with bootstrap.begin() as conn: conn.execute(text(statement))
            try:
                assert c.get("/test/departments/hr", headers=h["employee"]).status_code == 403
                expected_admin = 403 if "departments SET" in statement else 200
                assert c.get("/test/departments/hr", headers=h["admin"]).status_code == expected_admin
            finally:
                with bootstrap.begin() as conn: conn.execute(text(restore))


def test_inactive_and_expired_identity_rejected_across_all_core_routes(core_pg):
    bootstrap, _, c, h, password, _ = core_pg
    with bootstrap.begin() as conn:
        conn.execute(text("UPDATE users SET status='inactive' WHERE id='outsider'"))
    try:
        for op in PROTECTED:
            assert request(c, op, h["outsider"]).status_code == 401
    finally:
        with bootstrap.begin() as conn:
            conn.execute(text("UPDATE users SET status='active' WHERE id='outsider'"))
    expired = login_headers(c, password, "outsider")
    with Session(bootstrap) as s:
        row = s.scalar(select(DevelopmentSession).where(DevelopmentSession.token_digest == digest(expired["Authorization"][7:])))
        row.expires_at = utc_now() - timedelta(seconds=1)
        s.commit()
    for op in PROTECTED:
        assert request(c, op, expired).status_code == 401


def test_status_write_revokes_access_across_auth_usage_and_metadata(core_pg):
    _, _, c, h, password, _ = core_pg
    old = login_headers(c, password, "employee")
    assert c.patch("/admin/users/employee/status", headers=h["admin"], json={"status": "inactive"}).status_code == 200
    assert c.patch("/admin/users/employee/status", headers=h["admin"], json={"status": "active"}).status_code == 200
    for op in PROTECTED:
        assert request(c, op, old).status_code == 401
    fresh = login_headers(c, password, "employee")
    assert c.get("/auth/me", headers=fresh).status_code == 200
    assert c.get("/me/usage", headers=fresh).status_code == 200


def test_failed_legal_metadata_does_not_enable_tenant_access(core_pg):
    bootstrap, runtime, c, _, password, _ = core_pg
    with Session(bootstrap) as s:
        s.add(DepartmentMembership(user_id="outsider", department_id="legal"))
        s.add(RoleAssignment(user_id="outsider", role_id="manager-user", department_id="legal"))
        s.commit()
    token = login_headers(c, password, "outsider")
    response = c.get("/me/department", headers=token)
    assert response.status_code == 200 and response.json()["status"] == "failed"
    with TestClient(auth_test_app(runtime)) as client:
        assert client.get("/test/departments/legal", headers=token).status_code == 403
    with bootstrap.connect() as conn:
        assert conn.scalar(text("SELECT status FROM departments WHERE id='legal'")) == "failed"
