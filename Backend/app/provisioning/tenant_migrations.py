from pathlib import Path
import hashlib
import json
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from .errors import ProvisioningError
from .postgres import identities

BASE = Path(__file__).resolve().parents[2]


class TenantMigrator:
    """A pinned revision is mandatory; never silently use create_all or stamp head."""

    def __init__(self, required_revision, required_tables):
        self.available = True
        self.required_revision = required_revision
        self.required_tables = tuple(required_tables)
        if not required_revision or not self.required_tables:
            raise ProvisioningError("migration_contract_missing")
        if any(not isinstance(t, str) or not t.isidentifier() for t in self.required_tables):
            raise ProvisioningError("invalid_required_table")
        self.contract_id = hashlib.sha256(json.dumps([required_revision, sorted(self.required_tables)]).encode()).hexdigest()

    @classmethod
    def from_contract(cls, path):
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
        if contract.get("ready") is not True:
            return UnavailableTenantContract()
        revision = contract.get("required_revision")
        required = contract.get("required_tables", [])
        # The public CLI must not turn a foundation-only test fixture into an
        # active department by changing ready=false to true. Document migrations
        # must extend tenant_0001 and declare at least one business table.
        if (not isinstance(revision, str) or revision in {"tenant_0001", "head", "heads", "base"}
                or not isinstance(required, list)
                or "_provisioning_identity" not in required
                or not any(isinstance(t, str) and t not in {"_provisioning_identity", "alembic_version"} for t in required)):
            return UnavailableTenantContract()
        return cls(revision, required)

    def migrate(self, url, owner, department_id):
        database = make_url(url).database or ""
        expected_owner, _ = identities(database, database.upper())
        if owner != expected_owner:
            raise ProvisioningError("unmanaged_database_identity")
        cfg = Config(str(BASE / "tenant_alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        revision = script.get_revision(self.required_revision)
        if revision is None or revision.revision != self.required_revision:
            raise ProvisioningError("migration_revision_not_pinned")
        engine = create_engine(url, hide_parameters=True)
        try:
            with engine.begin() as conn:
                # Owner is derived from a validated managed DB identity.
                conn.exec_driver_sql("SET LOCAL ROLE " + conn.dialect.identifier_preparer.quote(owner))
                cfg.attributes["connection"] = conn
                command.upgrade(cfg, revision.revision)
                conn.execute(text("INSERT INTO public._provisioning_identity(singleton,department_id) VALUES (1,:id) ON CONFLICT(singleton) DO NOTHING"), {"id": department_id})
                if conn.scalar(text("SELECT department_id FROM public._provisioning_identity WHERE singleton=1")) != department_id:
                    raise ProvisioningError("tenant_identity_conflict")
                for table in self.required_tables:
                    if conn.scalar(text("SELECT to_regclass(:name)"), {"name": "public." + table}) is None:
                        raise ProvisioningError("required_table_missing")
                if conn.scalar(text("SELECT version_num FROM public.alembic_version")) != revision.revision:
                    raise ProvisioningError("migration_revision_mismatch")
            return revision.revision
        finally:
            engine.dispose()


class UnavailableTenantContract:
    available = False
    contract_id = "unavailable"
    required_revision = None
    required_tables = ()
