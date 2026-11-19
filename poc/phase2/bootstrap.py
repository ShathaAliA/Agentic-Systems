"""Offline setup for a brand-new Phase 2A cluster only. No files/models/providers."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback
import uuid

from safety import CENTRAL, PURPOSE, REPO, load, target, write_json

BACKEND = Path(os.getenv("POC2_BACKEND_ROOT", REPO / "Backend"))
sys.path.insert(0, str(BACKEND))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"


def bootstrap(folder, state, identity_only=False):
    from sqlalchemy import create_engine, text, select
    from sqlalchemy.engine import URL
    from sqlalchemy.orm import Session
    from psycopg import sql
    from alembic import command
    from alembic.config import Config
    from app.models import User, Department, DepartmentDatabase, DepartmentMembership, Role, Permission, RolePermission, RoleAssignment
    from app.auth.enroll import set_password
    from app.credentials import CredentialStore
    from app.provisioning.postgres import PostgresProvisioner, identities
    from app.provisioning.tenant_migrations import TenantMigrator
    from app.provisioning.worker import Worker
    from app.runtime_security import restrict_runtime_engine

    host, port = target(state)
    secrets = json.loads((folder / "secrets.json").read_text())
    def url(role, database=CENTRAL):
        return URL.create("postgresql+psycopg", username=role, password=secrets["database"][role],
                          host=host, port=port, database=database)
    admin = create_engine(url("poc2_bootstrap"), hide_parameters=True)
    with admin.connect() as c:
        if (set(c.scalars(text("SELECT datname FROM pg_database"))) != {CENTRAL, "postgres", "template0", "template1"}
                or c.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) != 0):
            raise RuntimeError("brand_new_cluster_required")
        rows = c.execute(text("SELECT datname,pg_get_userbyid(datdba),datacl::text FROM pg_database ORDER BY datname")).all()
        write_json(folder / "owners-acls-before.json", [list(row) for row in rows])
    if not all((folder / name).is_file() and (folder / name).stat().st_size
               for name in ("before.dump", "globals-before.sql")):
        raise RuntimeError("pre_migration_backup_required")
    with admin.begin() as c:
        c.execute(text("CREATE TABLE public._poc2_identity(run_id text PRIMARY KEY,purpose text NOT NULL)"))
        c.execute(text("INSERT INTO public._poc2_identity VALUES (:run,:purpose)"), {"run": state["run_id"], "purpose": PURPOSE})
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        pg = c.connection.driver_connection
        for role in ("poc2_owner", "poc2_migrator", "poc2_api", "poc2_registry", "poc2_provisioner"):
            login = "NOLOGIN" if role == "poc2_owner" else "LOGIN"
            capabilities = "CREATEDB CREATEROLE" if role == "poc2_provisioner" else "NOCREATEDB NOCREATEROLE"
            pg.execute(sql.SQL("CREATE ROLE {} {} NOINHERIT NOSUPERUSER NOREPLICATION NOBYPASSRLS {} PASSWORD {}").format(
                sql.Identifier(role), sql.SQL(login), sql.SQL(capabilities), sql.Literal(secrets["database"][role])))
        pg.execute("GRANT poc2_owner TO poc2_migrator WITH INHERIT TRUE, SET TRUE")
        pg.execute("ALTER DATABASE poc2_central OWNER TO poc2_owner")
        pg.execute("ALTER SCHEMA public OWNER TO poc2_owner")
        for database in (CENTRAL, "postgres", "template1"):
            pg.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        pg.execute("GRANT CONNECT ON DATABASE poc2_central TO poc2_migrator,poc2_api,poc2_registry,poc2_provisioner")
        pg.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        pg.execute("GRANT USAGE ON SCHEMA public TO poc2_api,poc2_registry")
    write_json(folder / "migration-url.json", {"url": url("poc2_migrator").render_as_string(hide_password=False)})
    os.environ["CENTRAL_MIGRATION_DATABASE_URL_FILE"] = str(folder / "migration-url.json")
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")
    owner = create_engine(url("poc2_migrator"), hide_parameters=True)
    documents = ["documents:read", "documents:upload", "documents:update", "documents:delete", "documents:manage_permissions"]
    profiles = {"employee": "Employee", "head": "Department Manager", "deputy": "Deputy Department Manager",
                "other": "Employee", "denied": "PoC Denied", "admin": "Admin"}
    mapping = {"users": {}, "roles": {}, "tenants": state["tenants"]}
    with Session(owner) as session, session.begin():
        for name in set(profiles.values()):
            role = session.scalar(select(Role).where(Role.name == name))
            if role is None:
                role = Role(id=str(uuid.uuid4()), name=name, scope="global" if name == "Admin" else "department")
                session.add(role)
                session.flush()
            mapping["roles"][name] = role.id
        permission_ids = {}
        for code in documents + ["users:manage", "departments:manage"]:
            permission = session.scalar(select(Permission).where(Permission.code == code))
            if permission is None:
                permission = Permission(id=str(uuid.uuid4()), code=code)
                session.add(permission)
                session.flush()
            permission_ids[code] = permission.id
        for name in set(profiles.values()):
            allowed = [] if name == "PoC Denied" else (["documents:read"] if name == "Employee" else documents)
            if name == "Admin": allowed += ["users:manage", "departments:manage"]
            for code in allowed:
                session.add(RolePermission(role_id=mapping["roles"][name], permission_id=permission_ids[code]))
        for actor, role_name in profiles.items():
            identity = str(uuid.uuid4())
            mapping["users"][actor] = identity
            session.add(User(id=identity, name="Synthetic " + actor, email=f"poc2-{actor}@example.invalid", status="active"))
        session.flush()
        for key, item in state["tenants"].items():
            session.add(Department(id=item["id"], name="Experimental Resources Department" if key == "primary" else "Other Synthetic Department", status="provisioning"))
            session.flush()
            session.add(DepartmentDatabase(department_id=item["id"], database_name=item["database"],
                credential_key=item["database"].upper(), status="provisioning"))
        session.flush()
        for actor, role_name in profiles.items():
            department = state["tenants"]["other" if actor == "other" else "primary"]["id"]
            if actor != "admin":
                session.add(DepartmentMembership(user_id=mapping["users"][actor], department_id=department, status="active"))
            session.add(RoleAssignment(user_id=mapping["users"][actor], role_id=mapping["roles"][role_name],
                department_id=None if actor == "admin" else department))
        session.get(Department, state["tenants"]["primary"]["id"]).head_user_id = mapping["users"]["head"]
    with Session(owner) as session:
        for actor in profiles:
            set_password(session, f"poc2-{actor}@example.invalid", secrets["accounts"][actor])
    with owner.begin() as c:
        c.execute(text("GRANT SELECT ON users,departments,department_memberships,roles,permissions,role_permissions,role_assignments,department_databases,auth_development_credentials,user_audit_events TO poc2_api"))
        c.execute(text("GRANT SELECT ON role_policies,role_policy_audit_events,department_creation_grants,department_audit_events TO poc2_api"))
        c.execute(text("GRANT SELECT,INSERT,UPDATE ON auth_development_sessions,auth_login_windows TO poc2_api"))
        c.execute(text("GRANT SELECT,UPDATE ON departments,department_databases TO poc2_registry"))
    with admin.begin() as c:
        c.execute(text("GRANT SELECT ON _poc2_identity TO poc2_api"))
    store = CredentialStore(folder / "api/tenants")
    provisioner = PostgresProvisioner(url("poc2_provisioner"))
    registry = restrict_runtime_engine(create_engine(url("poc2_registry"), hide_parameters=True))
    manifest = json.loads((BACKEND / "tenant-contract.json").read_text())
    if manifest["ready"] is not False:
        raise RuntimeError("unexpected_repository_contract")
    manifest["ready"] = not identity_only  # synthetic full-schema mode only; repo untouched
    write_json(folder / "synthetic-contract.json", manifest)
    for item in state["tenants"].values():
        database = item["database"]
        db_owner, app_role = identities(database, database.upper())
        credentials = store.ensure(database.upper(), app_role)
        provisioner.ensure_resources(database, db_owner, credentials, item["id"])
        if identity_only:
            migrator = TenantMigrator("tenant_0001", ["_provisioning_identity"])
            migrator.migrate(provisioner.migration_url(database), db_owner, item["id"])
            provisioner.grant_runtime(database, db_owner, credentials)
            provisioner.verify(database, db_owner, credentials, item["id"], "tenant_0001", ["_provisioning_identity"])
            # Foundation checks are insufficient to activate a business tenant.
        else:
            extension = create_engine(url("poc2_bootstrap", database), hide_parameters=True)
            with extension.begin() as c: c.execute(text("CREATE EXTENSION vector"))
            extension.dispose()
            worker = Worker(registry, provisioner, store, TenantMigrator.from_contract(folder / "synthetic-contract.json"))
            if worker.run(item["id"]) != "active":
                raise RuntimeError("synthetic_tenant_provisioning_failed")
    api = folder / "api"
    write_json(api / "url.json", {"url": url("poc2_api").render_as_string(hide_password=False)})
    state["identity_only"] = identity_only
    state["phase"] = "identities_ready_tenants_pending" if identity_only else "foundation_ready"
    write_json(folder / "mapping.json", mapping)
    write_json(api / "run.json", {**state, "users": mapping["users"]})
    write_json(folder / "run.json", state)
    registry.dispose()
    owner.dispose()
    admin.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--operation", choices=["bootstrap", "verify"], required=True)
    parser.add_argument("--identity-only", action="store_true")
    args = parser.parse_args()
    folder, state = load(args.run)
    try:
        if args.operation == "bootstrap": bootstrap(folder, state, args.identity_only)
        else:
            from verify import verify
            verify(folder, state)
    except Exception as exc:
        state["phase"] = "failed"
        write_json(folder / "failure.json", {"error_class": type(exc).__name__,
            "frames": [{"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                       for f in traceback.extract_tb(exc.__traceback__)]})
        write_json(folder / "run.json", state)
        raise SystemExit("Phase 2A operation failed; private diagnostic retained (no secrets printed)") from None
