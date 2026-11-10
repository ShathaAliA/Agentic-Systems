"""Full real document schema/ACL worker test. Requires pgvector in fresh cluster."""
import json
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from app.provisioning.postgres import identities
from app.provisioning.tenant_migrations import TenantMigrator
from test_authentication_postgres import isolated_postgres, pytestmark
from test_tenant_contract_postgres import integration, registry_row
from test_tenant_contract import payload


def test_real_document_contract_can_be_verified_without_runtime_admin(integration, tmp_path):
    i = integration
    identity, database, worker = i["registered"](payload())
    contract = json.loads((Path(__file__).parents[1] / "tenant-contract.json").read_text())
    assert contract["ready"] is False
    contract["ready"] = True  # synthetic copy only; never publish the real manifest
    path = tmp_path / "synthetic-approved.json"
    path.write_text(json.dumps(contract))
    worker.migrator = TenantMigrator.from_contract(path)
    owner, runtime = identities(database, database.upper())
    credential = i["store"].ensure(database.upper(), runtime)
    i["pg"].ensure_resources(database, owner, credential, identity)
    # vector is untrusted: only a separate bootstrap identity installs it, only
    # in this newly created test DB. Worker/API never receive SUPERUSER.
    extension_engine = create_engine(i["bootstrap"].url.set(database=database), hide_parameters=True)
    with extension_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION vector"))
    extension_engine.dispose()
    assert worker.run(identity) == "active"
    row = registry_row(i, identity)
    assert row.applied_revision == "tenant_0007" and row.verified_contract == worker.migrator.contract_id
    with i["pg"].connect(database, credential) as connection:
        doc = connection.execute("INSERT INTO documents(filename,file_path,department_id) VALUES ('synthetic','unused',%s) RETURNING id", (identity,)).fetchone()[0]
        assert doc > 0  # documents_id_seq must be usable
        connection.execute("INSERT INTO question_results(request_id,user_id,payload) VALUES ('synthetic','synthetic','{}')")
        assert not connection.execute("SELECT has_table_privilege(current_user,'question_results','UPDATE,DELETE')").fetchone()[0]
        assert connection.execute("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone()[0] is False
