"""Opt-in integration against the existing local development cluster only.

No database is dropped. HR/Finance contents and their registry entries are checked
before/after. Cluster CONNECT ACL hardening is explicit and separately reported.
"""
import hashlib
import json
import os
import secrets
from pathlib import Path
from contextlib import contextmanager
import pytest
import psycopg
from psycopg import sql
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config
from app.credentials import CredentialStore
from app.models import Department, DepartmentDatabase, User
from app.provisioning.postgres import PostgresProvisioner, identities
from app.provisioning.tenant_migrations import TenantMigrator, UnavailableTenantContract
from app.provisioning.worker import Worker, department_lock
from app.provisioning.errors import ProvisioningError
from app.runtime_security import restrict_runtime_engine

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.getenv("RUN_DEVELOPMENT_PROVISIONING") != "1", reason="explicit development infrastructure opt-in required")]


def test_existing_legal_provisioning_without_modifying_hr_finance():
    admin = make_url(json.loads(Path(os.environ["DEV_ADMIN_URL_FILE"]).read_text())["url"])
    assert admin.host in {"ai_platform_postgres", "127.0.0.1", "localhost"}
    assert admin.database == "ai_platform_db"
    assert os.getenv("DEV_BACKUP_ACK") == "1", "Capture owners/ACLs and backups before opting into development setup"
    state = Path(os.environ["DEV_STATE_DIR"])
    state.mkdir(parents=True, exist_ok=True)
    credential_store = CredentialStore(state / "tenants")

    def connect(database):
        return psycopg.connect(host=admin.host,port=admin.port or 5432,dbname=database,user=admin.username,password=admin.password,autocommit=True)

    def fingerprint(database):
        result = {}
        with connect(database) as c:
            rows=c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
            for (table,) in rows:
                values=c.execute(sql.SQL("SELECT count(*), md5(coalesce(string_agg(md5(t::text),'' ORDER BY md5(t::text)),'')) FROM public.{} t").format(sql.Identifier(table))).fetchone()
                result[table]=list(values)
            result['_columns']=c.execute("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
        return result

    before={db:fingerprint(db) for db in ["hr_db","finance_db"]}
    with connect("ai_platform_db") as c:
        # Already registered Legal is mandatory. Never seed/re-register it here.
        legal=c.execute("SELECT d.id,r.database_name,r.credential_key FROM departments d JOIN department_databases r ON r.department_id=d.id WHERE d.name='Legal'").fetchone()
        assert legal, "Legal must already be registered in the development registry"
        existing=c.execute("SELECT d.id,d.name,d.status,r.database_name,r.credential_key,r.status FROM departments d JOIN department_databases r ON r.department_id=d.id WHERE d.name IN ('HR','Finance') ORDER BY d.name").fetchall()
        assert len(existing)==2
        assert c.execute("SELECT count(*) FROM users WHERE email IN ('admin@example.com','hr.manager@example.com','finance.employee@example.com')").fetchone()[0] == 3
        acl_snapshot=c.execute("SELECT datname,pg_get_userbyid(datdba),datacl::text FROM pg_database WHERE NOT datistemplate ORDER BY datname").fetchall()
        (state/"owners-acls-before.json").write_text(json.dumps(acl_snapshot,indent=2))
        # Keep these credentials out of source, environment listings, and reports.
        accounts={}
        for name in ["day1_api","day1_registry","day1_provisioner"]:
            cred=CredentialStore(state/"control").ensure(name.upper(),name)
            accounts[name]=cred
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",(name,)).fetchone():
                capabilities="CREATEDB CREATEROLE" if name=="day1_provisioner" else "NOCREATEDB NOCREATEROLE"
                c.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER {} NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(name),sql.SQL(capabilities),sql.Literal(cred.password)))
        admin_file=state/"central-migration.json"
        admin_file.write_text(json.dumps({"url":admin.render_as_string(hide_password=False)})); admin_file.chmod(0o600)
        os.environ["CENTRAL_MIGRATION_DATABASE_URL_FILE"]=str(admin_file)
        command.upgrade(Config("alembic.ini"),"head")
        c.execute("GRANT CONNECT ON DATABASE ai_platform_db TO day1_api,day1_registry,day1_provisioner")
        c.execute("GRANT CONNECT ON DATABASE postgres TO day1_provisioner")
        c.execute("GRANT USAGE ON SCHEMA public TO day1_api,day1_registry")
        c.execute("GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO day1_api")
        c.execute("REVOKE UPDATE ON department_databases FROM day1_api")
        c.execute("GRANT SELECT,UPDATE ON departments,department_databases TO day1_registry")
        c.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO day1_api")
    # Preserve existing department runtime access while removing PUBLIC CONNECT.
    # The worker never performs these changes to other databases.
    with connect("postgres") as c:
        for database,role in [("hr_db","hr_app"),("finance_db","finance_app")]:
            c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database),sql.Identifier(role)))
        for database in ["postgres","ai_platform_db","hr_db","finance_db"]:
            c.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))

    def url_for(name,database="ai_platform_db"):
        return admin.set(username=name,password=accounts[name].password,database=database)

    registry=create_engine(url_for("day1_registry"),hide_parameters=True,pool_size=3,max_overflow=2)
    pg=PostgresProvisioner(url_for("day1_provisioner","postgres"))
    migrator=TenantMigrator("tenant_0001",["_provisioning_identity"])
    worker=Worker(registry,pg,credential_store,UnavailableTenantContract())
    legal_id,database,key=legal
    with Session(registry) as s:
        r=s.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id==legal_id))
        already_blocked=r.status=="failed" and r.last_error_code=="tenant_contract_unavailable"
    if not already_blocked:
        assert worker.run(legal_id,retry_failed=True)=="failed"
    with Session(registry) as s:
        r=s.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id==legal_id))
        assert r.status=="failed" and r.last_error_code=="tenant_contract_unavailable"
        assert s.get(Department,legal_id).status=="failed"
    # Infrastructure integration fixture exercises real DDL/migrations/checks,
    # but deliberately never promotes Legal while document schema is unavailable.
    owner,runtime=identities(database,key)
    secret_before=credential_store.ensure(key,runtime)
    pg.ensure_resources(database,owner,secret_before,legal_id)
    assert migrator.migrate(pg.migration_url(database),owner,legal_id)=="tenant_0001"
    pg.grant_runtime(database,owner,secret_before)
    # Real advisory-lock contention must not start another attempt.
    with department_lock(registry,legal_id) as locked:
        assert locked
        assert worker.run(legal_id)=="busy"
    with connect("postgres") as c:
        oid_before=c.execute("SELECT oid FROM pg_database WHERE datname=%s",(database,)).fetchone()[0]
    assert worker.run(legal_id)=="deferred"
    assert credential_store.get(key)==secret_before
    with connect("postgres") as c:
        assert c.execute("SELECT oid FROM pg_database WHERE datname=%s",(database,)).fetchone()[0]==oid_before
    pg.verify(database,owner,secret_before,legal_id,"tenant_0001",migrator.required_tables)
    # Replaying resource setup must not recreate the database or rotate credentials.
    pg.ensure_resources(database,owner,secret_before,legal_id)
    with pg.connect(database,secret_before) as c:
        for statement in ["CREATE DATABASE day1_forbidden_db", "CREATE ROLE day1_forbidden_role"]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(statement)
    for other in ["hr_db","finance_db","ai_platform_db","postgres"]:
        with pytest.raises(ProvisioningError, match="database_connect_denied"):
            pg.connect(other,secret_before)
    elevated=restrict_runtime_engine(create_engine(admin,hide_parameters=True))
    try:
        with pytest.raises(RuntimeError,match="Administrative credentials"):
            elevated.connect()
    finally:
        elevated.dispose()
    # Route through the real RBAC and credential resolver as the non-admin API DB role.
    api_file=state/"api.json"; api_file.write_text(json.dumps({"url":url_for("day1_api").render_as_string(hide_password=False)})); api_file.chmod(0o600)
    os.environ.update(DATABASE_URL_FILE=str(api_file),DEPARTMENT_CREDENTIALS_DIR=str(state/"tenants"),DB_HOST=admin.host,DB_PORT=str(admin.port or 5432))
    from app.database_router import authorized_department_session
    from app.database import engine as api_engine
    with Session(api_engine) as s:
        admin_id=s.scalar(select(User.id).where(User.email=="admin@example.com"))
        employee_id=s.scalar(select(User.id).where(User.email=="finance.employee@example.com"))
        assert admin_id and employee_id
    with pytest.raises(PermissionError):
        with authorized_department_session(admin_id,legal_id,"documents:read"):
            pytest.fail("unready department was routed")
    with pytest.raises(PermissionError):
        with authorized_department_session(employee_id,legal_id,"documents:read"):
            pytest.fail("cross-department access allowed")
    after={db:fingerprint(db) for db in before}
    assert before==after
    with connect("ai_platform_db") as c:
        assert c.execute("SELECT d.id,d.name,d.status,r.database_name,r.credential_key,r.status FROM departments d JOIN department_databases r ON r.department_id=d.id WHERE d.name IN ('HR','Finance') ORDER BY d.name").fetchall()==existing
    report={"legal_id":legal_id,"database":database,"status":"failed","blocker":"tenant_contract_unavailable","foundation_revision":"tenant_0001","document_schema":"pending document developer; foundation cannot activate Legal","hr_finance_data_unchanged":True,"hr_finance_registry_unchanged":True,"resource_replay_noop":True,"runtime_privilege_checks":"passed","cross_database_connections":"denied","acl_change":"PUBLIC CONNECT revoked on four development databases; existing HR/Finance app CONNECT preserved explicitly","snapshots":before}
    (state/"verification.json").write_text(json.dumps(report,indent=2,default=str))
    print(json.dumps({k:v for k,v in report.items() if k!="snapshots"}))
    registry.dispose(); api_engine.dispose()
