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
from .contract import TenantContract, read_contract
from pydantic import ValidationError

BASE = Path(__file__).resolve().parents[2]


class TenantMigrator:
    """A pinned revision is mandatory; never silently use create_all or stamp head."""

    def __init__(self, required_revision, required_tables, *, table_privileges=None, sequence_privileges=None):
        # Direct construction is for trusted infrastructure tests. The public
        # file loader never enables the foundation-only test exception.
        try:
            contract = TenantContract.model_validate({"contract_version": 1, "ready": True,
                "required_revision": required_revision, "required_tables": list(required_tables),
                "table_privileges": table_privileges if table_privileges is not None else {},
                "sequence_privileges": sequence_privileges if sequence_privileges is not None else {}},
                context={"foundation_test": True})
        except (ValidationError, ValueError, TypeError):
            raise ProvisioningError("tenant_contract_invalid") from None
        self._initialize(contract)

    def _initialize(self, contract):
        self.available = True
        self.required_revision = contract.required_revision
        self.required_tables = tuple(sorted(contract.required_tables))
        self._canonical = contract.canonical()  # Immutable snapshot; no mutable policy alias.
        self.contract_id = hashlib.sha256(self._canonical.encode("utf-8")).hexdigest()

    def runtime_privileges(self):
        payload = json.loads(self._canonical)
        return {"table_privileges": payload["table_privileges"],
                "sequence_privileges": payload["sequence_privileges"]}

    @classmethod
    def from_contract(cls, path):
        try:
            contract = read_contract(path)
        except ProvisioningError as error:
            return UnavailableTenantContract(error.code)
        if not contract.ready:
            return UnavailableTenantContract()
        result = cls.__new__(cls)
        result._initialize(contract)
        return result

    def _alembic_config(self):
        return Config(str(BASE / "tenant_alembic.ini"))

    def migrate(self, url, owner, department_id):
        database = make_url(url).database or ""
        expected_owner, _ = identities(database, database.upper())
        if owner != expected_owner:
            raise ProvisioningError("unmanaged_database_identity")
        cfg = self._alembic_config()
        script = ScriptDirectory.from_config(cfg)
        revision = script.get_revision(self.required_revision)
        if revision is None or revision.revision != self.required_revision:
            raise ProvisioningError("migration_revision_not_pinned")
        if "tenant_0001" not in {r.revision for r in script.walk_revisions(base="base", head=revision.revision)}:
            raise ProvisioningError("migration_revision_not_descendant")
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
    def __init__(self, error_code="tenant_contract_unavailable"):
        self.error_code = error_code

    available = False
    contract_id = "unavailable"
    required_revision = None
    required_tables = ()
