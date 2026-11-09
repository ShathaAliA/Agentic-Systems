from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.auth.config import AuthSettings
from app.main import create_app
from app.models import RoleAssignment
from app.role_policies.models import RolePolicyAudit
from app.role_policies import service
from test_authentication import login_headers
from test_authentication_postgres import isolated_postgres, pytestmark
from test_users import seed_user_admin


@pytest.fixture(scope="module")
def policy_pg(isolated_postgres):
    bootstrap, runtime, password = isolated_postgres
    deputy = seed_user_admin(bootstrap)
    with bootstrap.begin() as c:
        c.execute(text("GRANT SELECT,INSERT,UPDATE ON role_policies TO auth_test_api"))
        c.execute(text("GRANT INSERT,DELETE ON role_permissions TO auth_test_api"))
        c.execute(text("GRANT SELECT,INSERT ON role_policy_audit_events TO auth_test_api"))
    app = create_app(central_engine=runtime, auth_settings=AuthSettings(mode="development_password", app_env="test"))
    with TestClient(app) as client:
        yield bootstrap, runtime, app, client, login_headers(client, password, "admin"), deputy, password


def test_concurrent_first_policy_write_has_one_winner(policy_pg):
    bootstrap, _, app, client, headers, _, _ = policy_pg
    def attempt(_):
        with TestClient(app) as c:
            return c.put("/admin/roles/employee-user/permissions", headers=headers,
                json={"expected_version": 1, "permissions": []}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(attempt, range(4))) == [200, 409, 409, 409]
    with Session(bootstrap) as s:
        assert service.snapshot(s, "employee-user")["version"] == 2
        assert len(list(s.scalars(select(RolePolicyAudit).where(RolePolicyAudit.role_id == "employee-user")))) == 1
    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.put("/admin/roles/administrator/permissions", headers=headers,
        json={"expected_version": 1, "permissions": []}).status_code == 403


def test_database_audit_failure_is_atomic_and_sessions_revoke_all_holders(policy_pg):
    bootstrap, _, _, c, h, deputy, password = policy_pg
    with Session(bootstrap) as s:
        s.add_all([RoleAssignment(user_id="employee", role_id=deputy, department_id="hr"),
            RoleAssignment(user_id="outsider", role_id=deputy, department_id="finance")])
        s.commit()
    tokens = [login_headers(c, password, name) for name in ("employee", "outsider")]
    with bootstrap.begin() as conn:
        conn.execute(text("REVOKE INSERT ON role_policy_audit_events FROM auth_test_api"))
    try:
        assert c.put(f"/admin/roles/{deputy}/permissions", headers=h,
            json={"expected_version": 1, "permissions": []}).status_code == 503
    finally:
        with bootstrap.begin() as conn:
            conn.execute(text("GRANT INSERT ON role_policy_audit_events TO auth_test_api"))
    for token in tokens:
        assert c.get("/auth/me", headers=token).status_code == 200
    with Session(bootstrap) as s:
        assert service.snapshot(s, deputy)["permissions"] == ["documents:read"]
        assert service.snapshot(s, deputy)["version"] == 1
    assert c.put(f"/admin/roles/{deputy}/permissions", headers=h,
        json={"expected_version": 1, "permissions": []}).status_code == 200
    for token in tokens:
        assert c.get("/auth/me", headers=token).status_code == 401


def test_runtime_cannot_rewrite_audit_or_core_roles(policy_pg):
    _, runtime, _, _, _, _, _ = policy_pg
    for statement in ("DELETE FROM role_policy_audit_events", "UPDATE role_policy_audit_events SET version=999",
                      "UPDATE roles SET scope='global'", "DELETE FROM permissions", "CREATE TABLE forbidden(id int)"):
        with pytest.raises(DBAPIError):
            with runtime.begin() as c:
                c.execute(text(statement))
    with runtime.connect() as c:
        assert c.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")).one() == (False, False, False)


def test_writer_rechecks_admin_after_waiting_for_shared_lock(policy_pg):
    bootstrap, runtime, _, _, _, _, _ = policy_pg
    entered = Event()
    def attempt():
        with Session(runtime) as s:
            entered.set()
            try:
                service.replace(s, "admin", "manager-user", 1, ["documents:read"])
                s.commit()
                return 200
            except service.DepartmentError as error:
                s.rollback()
                return error.status
    with bootstrap.connect() as gate:
        transaction = gate.begin()
        gate.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('user-administration',0))"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(attempt)
            try:
                assert entered.wait(5)
                deadline = time.monotonic() + 5
                while not gate.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE usename='auth_test_api' AND wait_event='advisory'")):
                    assert time.monotonic() < deadline, "Writer did not wait on the shared administration lock"
                    time.sleep(0.02)
                gate.execute(text("UPDATE users SET status='inactive' WHERE id='admin'"))
                transaction.commit()
                assert future.result(timeout=15) == 403
            finally:
                if transaction.is_active:
                    transaction.rollback()
                with bootstrap.begin() as restore:
                    restore.execute(text("UPDATE users SET status='active' WHERE id='admin'"))
    with Session(bootstrap) as s:
        assert service.snapshot(s, "manager-user")["version"] == 1


def test_migration_rerun_preserves_policy_and_legacy_registry(policy_pg):
    bootstrap, _, _, _, _, _, _ = policy_pg
    with Session(bootstrap) as s:
        before = service.snapshot(s, "employee-user")
    command.upgrade(Config("alembic.ini"), "head")
    with Session(bootstrap) as s:
        assert service.snapshot(s, "employee-user") == before
        assert s.scalar(text("SELECT status FROM department_databases WHERE id='existing-registry'")) == "failed"
