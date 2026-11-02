from contextlib import contextmanager
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.credentials import CredentialStore
from app.models import Base, Department, DepartmentDatabase
from app.provisioning.errors import ProvisioningError
from app.provisioning.postgres import identities
from app.provisioning.tenant_migrations import TenantMigrator
from app.provisioning.worker import Worker, Job


@contextmanager
def unlocked(_engine, _id):
    yield True


@pytest.fixture
def setup(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s, s.begin():
        s.add(Department(id="legal", name="Legal", status="provisioning"))
        s.add(DepartmentDatabase(id="registry", department_id="legal", database_name="dept_" + "a" * 32, credential_key="DEPT_" + "A" * 32, status="provisioning"))
    pg = Mock()
    pg.migration_url.return_value = "not-a-real-url"
    migrator = SimpleNamespace(migrate=Mock(return_value="tenant_0001"), required_tables=("_provisioning_identity",), available=True, contract_id="test-contract")
    worker = Worker(engine, pg, CredentialStore(tmp_path / "secrets"), migrator, lock=unlocked)
    yield worker, pg, migrator
    engine.dispose()


def state(worker):
    with Session(worker.engine) as s:
        r = s.scalar(select(DepartmentDatabase))
        return r.status, r.attempt_count, r.last_error_code, r.applied_revision, s.get(Department, "legal").status


def test_success_and_active_replay_do_not_recreate_or_rotate(setup):
    w, pg, migrator = setup
    assert w.run("legal") == "active"
    assert state(w) == ("active", 1, None, "tenant_0001", "active")
    secret = w.secrets.get("DEPT_" + "A" * 32)
    assert w.run("legal") == "active"
    pg.ensure_resources.assert_called_once()
    pg.verify.assert_called_once()
    assert w.secrets.get("DEPT_" + "A" * 32) == secret


@pytest.mark.parametrize("stage", ["resource", "migration", "grant", "verify"])
def test_partial_failures_are_retryable_and_never_active(setup, stage):
    w, pg, migrator = setup
    action = {"resource": pg.ensure_resources, "migration": migrator.migrate, "grant": pg.grant_runtime, "verify": pg.verify}[stage]
    action.side_effect = RuntimeError("password=CANARY_SECRET")
    assert w.run("legal") == "failed"
    assert state(w) == ("failed", 1, "infrastructure_error", None, "failed")
    assert w.run("legal") == "deferred"
    action.side_effect = None
    migrator.migrate.return_value = "tenant_0001"
    assert w.run("legal", retry_failed=True) == "active"
    assert state(w)[1] == 2


def test_required_tables_block_activation(setup):
    w, pg, migrator = setup
    migrator.migrate.side_effect = ProvisioningError("required_table_missing")
    assert w.run("legal") == "failed"
    assert state(w)[2] == "required_table_missing"
    pg.verify.assert_not_called()


def test_crashed_provisioning_is_reclaimed(setup):
    w, *_ = setup
    with Session(w.engine) as s, s.begin():
        r = s.scalar(select(DepartmentDatabase))
        r.claim_token = "dead-process"
        r.attempt_count = 1
    assert w.run("legal") == "active"
    assert state(w)[1] == 2


def test_bounded_retries_and_stale_claim(setup):
    w, pg, _ = setup
    pg.verify.side_effect = ProvisioningError("cross_database_connect")
    for _ in range(3):
        assert w.run("legal", retry_failed=True) == "failed"
    assert w.run("legal", retry_failed=True) == "failed"
    assert state(w)[1:3] == (3, "attempts_exhausted")
    with pytest.raises(ProvisioningError, match="stale_claim"):
        w.finish(Job("legal", "unused", "unused", "wrong", 1), "active")


def test_busy_worker_does_not_touch_registry(setup):
    w, pg, _ = setup
    @contextmanager
    def busy(*_):
        yield False
    w.lock = busy
    assert w.run("legal") == "busy"
    assert state(w)[1] == 0
    pg.ensure_resources.assert_not_called()


def test_unmanaged_identity_never_creates_postgres_resources(setup):
    w, pg, _ = setup
    with Session(w.engine) as s, s.begin():
        s.scalar(select(DepartmentDatabase)).database_name = "hr_db"
    assert w.run("legal") == "failed"
    assert state(w)[2] == "unmanaged_database_identity"
    pg.ensure_resources.assert_not_called()


@pytest.mark.parametrize("key", ["../escape", "a/b", "", "X; DROP ROLE test"])
def test_secret_references_cannot_escape_directory(tmp_path, key):
    with pytest.raises(ValueError):
        CredentialStore(tmp_path).ensure(key, "runtime")


def test_atomic_secret_creation_and_identity_conflict(tmp_path):
    store = CredentialStore(tmp_path)
    first = store.ensure("LEGAL", "legal_app")
    assert first == store.ensure("LEGAL", "legal_app")
    assert first.password not in repr(first)
    with pytest.raises(ValueError, match="identity conflict"):
        store.ensure("LEGAL", "other_app")


def test_migration_contract_is_mandatory():
    with pytest.raises(ProvisioningError):
        TenantMigrator("", ())


def test_missing_contract_blocks_previously_active_department(setup):
    from app.provisioning.tenant_migrations import UnavailableTenantContract
    w, pg, _ = setup
    assert w.run("legal") == "active"
    w.migrator = UnavailableTenantContract()
    assert w.run("legal") == "failed"
    assert state(w)[2] == "tenant_contract_unavailable"
    pg.ensure_resources.assert_called_once()


def test_changed_contract_is_not_an_active_noop(setup):
    w, pg, migrator = setup
    assert w.run("legal") == "active"
    migrator.contract_id = "changed-contract"
    migrator.migrate.side_effect = ProvisioningError("required_table_missing")
    assert w.run("legal") == "failed"
    assert state(w)[2] == "required_table_missing"


def test_driver_connection_errors_are_sanitized(monkeypatch):
    import psycopg, traceback
    from app.provisioning.postgres import PostgresProvisioner
    from app.credentials import Credentials
    monkeypatch.setattr(psycopg,"connect",Mock(side_effect=psycopg.OperationalError("password=CANARY_SECRET")))
    provisioner = PostgresProvisioner("postgresql://admin:CANARY_SECRET@localhost/postgres")
    credentials = Credentials("app","CANARY_SECRET")
    try:
        provisioner.connect("legal",credentials)
    except ProvisioningError as e:
        assert e.code == "database_connection_failed"
        assert "CANARY_SECRET" not in str(e)
        assert "CANARY_SECRET" not in traceback.format_exc()


def test_unknown_migration_error_codes_are_not_persisted(setup):
    w, pg, migrator = setup
    migrator.migrate.side_effect = ProvisioningError("credential=CANARY_SECRET")
    assert w.run("legal") == "failed"
    assert state(w)[2] == "infrastructure_error"


@pytest.mark.parametrize("database,key", [("postgres", "POSTGRES"), ("dept_a;DROP", "DEPT_A"), ("dept_"+"a"*32, "HR")])
def test_managed_identifier_validation(database, key):
    with pytest.raises(ProvisioningError):
        identities(database, key)
