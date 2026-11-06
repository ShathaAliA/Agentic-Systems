"""Worker + real synthetic tenant migrations + reviewed grants, fresh cluster only."""
import copy
import json
import os
from pathlib import Path
import shutil
import uuid
import pytest
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.credentials import CredentialStore
from app.models import Department, DepartmentDatabase
from app.provisioning.postgres import PostgresProvisioner
from app.provisioning.tenant_migrations import TenantMigrator
from app.provisioning.worker import Worker
from app.runtime_security import restrict_runtime_engine
from test_authentication_postgres import isolated_postgres, pytestmark
from test_tenant_contract import payload

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def integration(isolated_postgres):
    bootstrap, api, _ = isolated_postgres
    state = Path(os.getenv("AUTH_TEST_REPORT_DIR", "/reports"))
    assert any((state / n).is_file() for n in ("before.dump", "auth-before.dump"))
    admin = PostgresProvisioner(bootstrap.url)
    with admin.connect() as c:
        rows = c.execute("SELECT datname,pg_get_userbyid(datdba),datacl::text FROM pg_database ORDER BY datname").fetchall()
        assert {r[0] for r in rows} == {"auth_test_db", "postgres", "template0", "template1"}
        (state / "contract-owners-acls-before.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    store = CredentialStore(state / "contract-credentials")
    registry = store.ensure("CONTRACT_REGISTRY", "contract_registry")
    provisioning = store.ensure("CONTRACT_PROVISIONER", "contract_provisioner")
    with admin.connect() as c:
        for credential, capabilities in ((registry, "NOCREATEDB NOCREATEROLE"), (provisioning, "CREATEDB CREATEROLE")):
            c.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER {} NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
                sql.Identifier(credential.username), sql.SQL(capabilities), sql.Literal(credential.password)))
            c.execute(sql.SQL("GRANT CONNECT ON DATABASE auth_test_db TO {}").format(sql.Identifier(credential.username)))
        c.execute("GRANT USAGE ON SCHEMA public TO contract_registry")
        c.execute("GRANT SELECT,UPDATE ON departments,department_databases TO contract_registry")
        for name, _, _ in rows:
            if name != "template0": c.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
    registry_engine = restrict_runtime_engine(create_engine(bootstrap.url.set(username=registry.username, password=registry.password), hide_parameters=True))
    pg = PostgresProvisioner(bootstrap.url.set(username=provisioning.username, password=provisioning.password))
    scripts = state / "contract-migrations"
    (scripts / "versions").mkdir(parents=True)
    shutil.copyfile(BACKEND / "tenant_alembic/env.py", scripts / "env.py")
    shutil.copyfile(BACKEND / "tenant_alembic/versions/tenant_0001_foundation.py", scripts / "versions/tenant_0001_foundation.py")
    for file in (BACKEND / "tests/fixtures/tenant_contract").glob("*.py"):
        shutil.copyfile(file, scripts / "versions" / file.name)
    def migrator(data):
        file = state / ("contract-" + uuid.uuid4().hex + ".json")
        file.write_text(json.dumps(data), encoding="utf-8")
        result = TenantMigrator.from_contract(file)
        if result.available:
            def cfg():
                config = Config()
                config.set_main_option("script_location", str(scripts))
                return config
            result._alembic_config = cfg  # Test-only source, never accepted from JSON.
        return result
    def registered(data):
        identity = str(uuid.uuid4())
        database = "dept_" + uuid.uuid4().hex
        with Session(bootstrap) as s, s.begin():
            s.add(Department(id=identity, name="Synthetic Contract " + identity, status="provisioning"))
            s.add(DepartmentDatabase(department_id=identity, database_name=database, credential_key=database.upper(), status="provisioning"))
        contract = migrator(data)
        return identity, database, Worker(registry_engine, pg, store, contract)
    yield dict(bootstrap=bootstrap, api=api, admin=admin, pg=pg, store=store, registered=registered, migrator=migrator, state=state)
    with Session(bootstrap) as s:
        assert s.get(Department, "legal").status == "failed"
        assert s.scalar(select(DepartmentDatabase).where(DepartmentDatabase.id == "existing-registry")).status == "failed"
    registry_engine.dispose()


def registry_row(i, identity):
    with Session(i["bootstrap"]) as s:
        return s.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id == identity))


def test_versioned_contract_migrates_and_verifies_only_synthetic_tenant(integration):
    i = integration
    identity, database, worker = i["registered"](payload())
    assert worker.run(identity) == "active"
    before = registry_row(i, identity)
    assert before.applied_revision == "contract_test_0002"
    assert before.verified_contract == worker.migrator.contract_id
    with i["admin"].connect() as c:
        oid = c.execute("SELECT oid FROM pg_database WHERE datname=%s", (database,)).fetchone()[0]
    credential = i["store"].get(database.upper())
    with i["pg"].connect(database, credential) as c:
        c.execute("INSERT INTO contract_records(payload) VALUES ('synthetic record')")
        c.execute("INSERT INTO contract_events(payload) VALUES ('synthetic append-only event')")
        assert not c.execute("SELECT has_table_privilege(current_user,'public.contract_events','UPDATE,DELETE')").fetchone()[0]
        assert not c.execute("SELECT has_table_privilege(current_user,'public._provisioning_identity','UPDATE')").fetchone()[0]
    assert worker.run(identity) == "active"
    assert registry_row(i, identity).attempt_count == before.attempt_count
    data = payload()
    data["table_privileges"]["contract_records"] = ["SELECT"]
    worker.migrator = i["migrator"](data)
    assert worker.run(identity) == "active"
    assert registry_row(i, identity).attempt_count == before.attempt_count + 1
    assert registry_row(i, identity).verified_contract == worker.migrator.contract_id
    with i["pg"].connect(database, credential) as c:
        assert c.execute("SELECT count(*) FROM contract_records").fetchone() == (1,)
        assert not c.execute("SELECT has_table_privilege(current_user,'public.contract_records','INSERT,UPDATE,DELETE')").fetchone()[0]
    with i["admin"].connect() as c:
        assert c.execute("SELECT oid FROM pg_database WHERE datname=%s", (database,)).fetchone()[0] == oid
    assert i["store"].get(database.upper()) == credential


@pytest.mark.parametrize("case", ["missing_table", "missing_sequence", "unrelated_revision", "missing_revision"])
def test_invalid_schema_or_grant_cannot_activate(integration, case):
    data = payload()
    if case == "missing_table":
        data["required_tables"].append("contract_missing")
        data["table_privileges"]["contract_missing"] = ["SELECT"]
    if case == "missing_sequence": data["sequence_privileges"]["missing_seq"] = ["USAGE"]
    if case == "unrelated_revision": data["required_revision"] = "unrelated_test_root"
    if case == "missing_revision": data["required_revision"] = "missing_revision"
    identity, _, worker = integration["registered"](data)
    assert worker.run(identity) == "failed"
    row = registry_row(integration, identity)
    assert row.verified_contract is None and row.status == "failed"
    if case == "missing_table": assert row.last_error_code == "required_table_missing"
    if case == "missing_sequence": assert row.last_error_code == "runtime_grant_policy_invalid"
    if case == "unrelated_revision": assert row.last_error_code == "migration_revision_not_descendant"


def test_unavailable_example_never_creates_resources(integration):
    data = json.loads((BACKEND / "tenant-contract.example.json").read_text())
    identity, database, worker = integration["registered"](data)
    assert worker.run(identity) == "failed"
    with integration["admin"].connect() as c:
        assert not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone()
    assert registry_row(integration, identity).last_error_code == "tenant_contract_unavailable"


def test_rejected_contract_removes_synthetic_tenant_readiness(integration):
    identity, _, worker = integration["registered"](payload())
    assert worker.run(identity) == "active"
    worker.migrator = integration["migrator"]({**payload(), "contract_version": 99})
    assert worker.run(identity) == "failed"
    row = registry_row(integration, identity)
    assert row.verified_contract is None and row.last_error_code == "tenant_contract_version_unsupported"
