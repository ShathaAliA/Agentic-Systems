"""Opt-in integration; refuses the existing Day 1 development cluster."""
import json
import os
from pathlib import Path
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings
from app.auth.models import DevelopmentCredential, LoginWindow
from app.auth.service import limit_login
from app.runtime_security import restrict_runtime_engine
from app.models import Department, DepartmentDatabase, User
from test_authentication import auth_test_app, login_headers, seed_auth_users

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv("RUN_AUTH_POSTGRES_TESTS") != "1", reason="isolated authentication PostgreSQL opt-in required")]


@pytest.fixture(scope="module")
def isolated_postgres():
    admin_url = make_url(json.loads(Path(os.environ["AUTH_TEST_ADMIN_URL_FILE"]).read_text())["url"])
    native = os.getenv("AUTH_TEST_NATIVE_POSTGRES") == "1"
    if native:
        assert admin_url.host == "127.0.0.1" and admin_url.port != 5435
        assert os.getenv("AUTH_TEST_REPORT_DIR")
    else:
        assert admin_url.host.startswith("ai-auth-test-")
    assert admin_url.database == "auth_test_db"
    assert os.getenv("AUTH_TEST_BACKUP_ACK") == "1"
    admin = create_engine(admin_url, hide_parameters=True)
    with admin.connect() as c:
        assert c.scalar(text("SELECT current_database()")) == "auth_test_db"
        # An empty, newly created test cluster is mandatory. Never reuse company data.
        assert c.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) == 0
        owners = c.execute(text("SELECT datname,pg_get_userbyid(datdba),datacl::text FROM pg_database ORDER BY datname")).all()
        report_dir = Path(os.getenv("AUTH_TEST_REPORT_DIR", "/reports"))
        (report_dir / "owners-acls-before.json").write_text(json.dumps([list(r) for r in owners], indent=2))
    os.environ["CENTRAL_MIGRATION_DATABASE_URL_FILE"] = os.environ["AUTH_TEST_ADMIN_URL_FILE"]
    cfg = Config("alembic.ini")
    # Begin at Backend's unchanged historical revision, not an already-Core schema.
    command.upgrade(cfg, "b8e1db57a78c")
    with admin.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM departments")) == 0  # No production/demo seeds.
    # Insert through the historical schema, before later model columns exist.
    with admin.begin() as c:
        c.execute(text("INSERT INTO users(id,name,email,status,created_at) VALUES ('existing-user','Existing','existing@example.invalid','active',CURRENT_TIMESTAMP)"))
        c.execute(text("INSERT INTO departments(id,name,status) VALUES ('existing-department','Existing Department','failed')"))
        c.execute(text("INSERT INTO roles(id,name,scope) VALUES ('existing-role','Legacy Synthetic Role','department')"))
        c.execute(text("INSERT INTO permissions(id,code) VALUES ('existing-permission','legacy:synthetic')"))
        c.execute(text("INSERT INTO department_memberships(id,user_id,department_id,status) VALUES ('existing-membership','existing-user','existing-department','active')"))
        c.execute(text("INSERT INTO role_permissions(id,role_id,permission_id) VALUES ('existing-role-permission','existing-role','existing-permission')"))
        c.execute(text("INSERT INTO role_assignments(id,user_id,role_id,department_id,created_at) VALUES ('existing-assignment','existing-user','existing-role','existing-department',CURRENT_TIMESTAMP)"))
    historical_columns = {
        "users": "id,name,email,status,created_at", "departments": "id,name,status",
        "roles": "id,name,scope,description", "permissions": "id,code,description",
        "department_memberships": "id,user_id,department_id,status",
        "role_permissions": "id,role_id,permission_id",
        "role_assignments": "id,user_id,role_id,department_id,created_at",
    }
    def historical_rows(connection):
        # Identifiers are fixed test constants; no operator or request input.
        return {table: connection.execute(text(
            f"SELECT {columns} FROM {table} WHERE id LIKE 'existing-%' ORDER BY id")).all()
            for table, columns in historical_columns.items()}
    with admin.connect() as c:
        historical = historical_rows(c)
        assert all(len(rows) == 1 for rows in historical.values())
    command.upgrade(cfg, "p002")
    with admin.connect() as c:
        assert historical_rows(c) == historical
    with admin.begin() as c:
        c.execute(text("INSERT INTO department_databases(id,department_id,database_name,credential_key,status,last_error_code,created_at) VALUES ('existing-registry','existing-department','existing_test_reference','EXISTING_TEST_REFERENCE','failed','tenant_contract_unavailable',CURRENT_TIMESTAMP)"))
    with admin.connect() as c:
        before = {table: c.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
                  for table in ("users", "departments", "department_databases")}
    command.upgrade(cfg, "auth001")
    command.upgrade(cfg, "auth001")  # Idempotent Alembic rerun.
    with admin.connect() as c:
        for table, expected in before.items():
            assert c.execute(text(f"SELECT * FROM {table} ORDER BY id")).all() == expected
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "auth001"
    # Application models also include the subsequent additive department metadata.
    command.upgrade(cfg, "head")
    with admin.connect() as c:
        assert historical_rows(c) == historical
        assert c.scalar(text("SELECT status FROM department_databases WHERE id='existing-registry'")) == "failed"
    password = seed_auth_users(admin)
    api_password = secrets.token_urlsafe(32)
    params = dict(host=admin_url.host, port=admin_url.port or 5432, dbname=admin_url.database,
                  user=admin_url.username, password=admin_url.password)
    with psycopg.connect(**params, autocommit=True) as c:
        # Privilege changes affect only this fresh disposable test database.
        c.execute(sql.SQL("CREATE ROLE auth_test_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Literal(api_password)))
        c.execute("REVOKE ALL ON DATABASE auth_test_db FROM PUBLIC")
        c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        c.execute("GRANT CONNECT ON DATABASE auth_test_db TO auth_test_api")
        c.execute("GRANT USAGE ON SCHEMA public TO auth_test_api")
        c.execute("GRANT SELECT ON users,departments,department_memberships,roles,permissions,role_permissions,role_assignments,auth_development_credentials TO auth_test_api")
        c.execute("GRANT SELECT,INSERT,UPDATE ON auth_development_sessions,auth_login_windows TO auth_test_api")
    runtime = restrict_runtime_engine(create_engine(admin_url.set(username="auth_test_api", password=api_password), hide_parameters=True))
    yield admin, runtime, password
    runtime.dispose()
    admin.dispose()


def test_migrations_and_authentication_with_least_privilege_runtime(isolated_postgres):
    admin, runtime, password = isolated_postgres
    with runtime.connect() as c:
        assert not any(c.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one())
        assert not c.scalar(text("SELECT has_schema_privilege(current_user,'public','CREATE')"))
    for statement in ("UPDATE users SET status='inactive'", "DELETE FROM auth_development_sessions",
                      "UPDATE auth_development_credentials SET password_hash=password_hash",
                      "DELETE FROM auth_development_credentials", "SELECT * FROM pg_authid",
                      "CREATE TABLE public.forbidden_test(id integer)"):
        with runtime.connect() as c, pytest.raises(DBAPIError) as error:
            c.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"
    with TestClient(auth_test_app(runtime)) as client:
        employee = login_headers(client, password)
        assert client.get("/auth/me", headers=employee).json()["id"] == "employee"
        assert client.get("/test/departments/hr", headers=employee).status_code == 200
        assert client.get("/test/departments/finance", headers=employee).status_code == 403
        assert client.get("/test/global", headers=employee).status_code == 403
        administrator = login_headers(client, password, "admin")
        assert client.get("/test/global", headers=administrator).status_code == 200
        assert client.get("/test/departments/legal", headers=administrator).status_code == 403
        assert client.post("/auth/logout", headers=employee).status_code == 204
        assert client.get("/auth/me", headers=employee).status_code == 401
        headers = login_headers(client, password)
        with Session(admin) as s:
            s.get(DevelopmentCredential, "employee").version = secrets.token_hex(16)
            s.commit()
        assert client.get("/auth/me", headers=headers).status_code == 401
    guarded_admin = restrict_runtime_engine(create_engine(admin.url, hide_parameters=True))
    try:
        with pytest.raises(RuntimeError, match="Administrative credentials"):
            guarded_admin.connect()
    finally:
        guarded_admin.dispose()


def test_concurrent_login_counters_are_atomic(isolated_postgres):
    admin, runtime, _ = isolated_postgres
    with admin.begin() as c:
        c.execute(text("DELETE FROM auth_login_windows"))
    now = datetime.now(timezone.utc)
    settings = AuthSettings(mode="development_password", app_env="test")

    def attempt(_):
        with Session(runtime) as s:
            try:
                limit_login(s, settings, "concurrent@example.invalid", "test-ip", now)
                return 200
            except HTTPException as error:
                return error.status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(15)))
    assert outcomes.count(200) == 5
    assert outcomes.count(429) == 10
    with Session(admin) as s:
        assert sorted(s.scalars(select(LoginWindow.attempts)).all()) == [15, 15, 15]
