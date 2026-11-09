"""Real multi-database security on a NEW backed-up cluster; no live departments."""
import json
import os
from pathlib import Path
import uuid
import pytest
import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text
from app.credentials import CredentialStore
from app.provisioning.errors import ProvisioningError
from app.provisioning.postgres import PostgresProvisioner, identities
from app.provisioning.tenant_migrations import TenantMigrator
from app.runtime_security import restrict_runtime_engine
from test_authentication_postgres import isolated_postgres, pytestmark

TABLE_POLICY = {"isolation_records": ("SELECT", "INSERT", "UPDATE", "DELETE"),
                "isolation_audit": ("SELECT", "INSERT")}
SEQUENCE_POLICY = {"isolation_records_id_seq": ("USAGE", "SELECT"),
                   "isolation_audit_id_seq": ("USAGE", "SELECT")}


@pytest.fixture(scope="module")
def physical(isolated_postgres):
    bootstrap, central_runtime, _ = isolated_postgres
    directory = Path(os.environ.get("AUTH_TEST_REPORT_DIR", "/reports"))
    assert any((directory / name).is_file() for name in ("before.dump", "auth-before.dump"))
    assert any((directory / name).is_file() for name in ("globals-before.sql", "auth-globals-before.sql"))
    admin = PostgresProvisioner(bootstrap.url)
    # Cluster-wide ACL probes must never run beside unrelated databases, even
    # if a manually supplied auth_test_db happens to have been empty initially.
    with admin.connect() as c:
        names = {row[0] for row in c.execute("SELECT datname FROM pg_database").fetchall()}
        assert names == {"auth_test_db", "postgres", "template0", "template1"}, "Not a fresh isolated test cluster"
    def snapshot(label):
        with admin.connect() as c:
            result = {"databases": c.execute("SELECT datname,pg_get_userbyid(datdba),datacl::text FROM pg_database ORDER BY datname").fetchall(),
                      "roles": c.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolcanlogin,rolbypassrls FROM pg_roles ORDER BY rolname").fetchall(),
                      "memberships": c.execute("SELECT pg_get_userbyid(roleid),pg_get_userbyid(member),admin_option FROM pg_auth_members ORDER BY roleid,member").fetchall()}
        (directory / (label + ".json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
    snapshot("isolation-before")
    (directory / "isolation-recovery-plan.json").write_text(json.dumps({
        "scope": "fresh isolated test cluster only",
        "baseline": "runner custom-format central dump and password-free globals dump, before fixture changes",
        "rebuild": "restore into a separate NEW cluster, then rerun versioned synthetic fixtures",
        "preservation": "retain this stopped cluster, snapshots and private credential store; no database deletion",
        "original_cluster_targeted": False,
    }, indent=2), encoding="utf-8")
    store = CredentialStore(directory / "isolation-credentials")
    provisioner = store.ensure("ISOLATION_PROVISIONER", "isolation_provisioner")
    canary = store.ensure("ISOLATION_GUARD_CANARY", "isolation_guard_canary")
    with admin.connect() as c:
        c.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER CREATEDB CREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(provisioner.username), sql.Literal(provisioner.password)))
        c.execute(sql.SQL("CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(canary.username), sql.Literal(canary.password)))
        c.execute("CREATE ROLE isolation_indirect NOLOGIN")
        c.execute("GRANT pg_read_all_data TO isolation_indirect")
        c.execute(sql.SQL("GRANT CONNECT ON DATABASE auth_test_db TO {},{}").format(sql.Identifier(provisioner.username), sql.Identifier(canary.username)))
    pg = PostgresProvisioner(bootstrap.url.set(username=provisioner.username, password=provisioner.password))
    tenants = []
    for label in ("Synthetic HR", "Synthetic Finance"):
        identity = str(uuid.uuid4())
        database = "dept_" + uuid.uuid4().hex
        owner, runtime = identities(database, database.upper())
        credentials = store.ensure(database.upper(), runtime)
        pg.ensure_resources(database, owner, credentials, identity)
        migrator = TenantMigrator("tenant_0001", ["_provisioning_identity"])
        assert migrator.migrate(pg.migration_url(database), owner, identity) == "tenant_0001"
        with pg.connect(database) as c, c.transaction():
            c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
            for table in TABLE_POLICY:
                c.execute(sql.SQL("CREATE TABLE public.{} (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label text NOT NULL)").format(sql.Identifier(table)))
        pg.grant_runtime(database, owner, credentials, table_privileges=TABLE_POLICY, sequence_privileges=SEQUENCE_POLICY)
        tenants.append(dict(database=database, owner=owner, credentials=credentials, identity=identity, label=label))
    # Catalog snapshot immediately before cluster ACL changes. Fresh-cluster dumps
    # above + versioned fixtures are the rebuild plan. Existing clusters are refused.
    snapshot("isolation-before-cluster-acls")
    with admin.connect() as c:
        for database, in c.execute("SELECT datname FROM pg_database WHERE datallowconn").fetchall():
            c.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
    yield dict(admin=admin, pg=pg, tenants=tenants, central=central_runtime, canary=canary,
               bootstrap=bootstrap, snapshot=snapshot, directory=directory)
    snapshot("isolation-after")


def test_own_database_identity_and_allowed_operations(physical):
    for tenant in physical["tenants"]:
        with physical["pg"].connect(tenant["database"], tenant["credentials"]) as c:
            assert c.execute("SELECT current_database(),current_user").fetchone() == (tenant["database"], tenant["credentials"].username)
            row = c.execute("INSERT INTO isolation_records(label) VALUES (%s) RETURNING id", (tenant["label"],)).fetchone()[0]
            c.execute("UPDATE isolation_records SET label=%s WHERE id=%s", (tenant["label"] + " updated", row))
            assert c.execute("SELECT label FROM isolation_records WHERE id=%s", (row,)).fetchone()[0].endswith("updated")
            c.execute("INSERT INTO isolation_audit(label) VALUES ('synthetic event')")
            c.execute("DELETE FROM isolation_records WHERE id=%s", (row,))


def test_future_tables_do_not_receive_automatic_runtime_permissions(physical):
    for tenant in physical["tenants"]:
        with physical["pg"].connect(tenant["database"]) as c, c.transaction():
            c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(tenant["owner"])))
            c.execute("CREATE TABLE future_isolation_probe(id int)")
            c.execute("CREATE SEQUENCE future_isolation_seq")
        with physical["pg"].connect(tenant["database"], tenant["credentials"]) as c:
            assert not c.execute("SELECT has_table_privilege(current_user,'public.future_isolation_probe','SELECT,INSERT,UPDATE,DELETE')").fetchone()[0]
            assert not c.execute("SELECT has_sequence_privilege(current_user,'public.future_isolation_seq','USAGE,SELECT,UPDATE')").fetchone()[0]


def test_runtime_pool_rejects_predefined_privileged_role_membership(physical):
    canary = physical["canary"]
    admin = physical["admin"]
    with admin.connect() as c:
        c.execute(sql.SQL("GRANT pg_write_all_data TO {}").format(sql.Identifier(canary.username)))
    engine = restrict_runtime_engine(create_engine(physical["bootstrap"].url.set(username=canary.username, password=canary.password), hide_parameters=True))
    try:
        with pytest.raises(RuntimeError, match="Administrative credentials"):
            with engine.connect(): pass
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(sql.SQL("REVOKE pg_write_all_data FROM {}").format(sql.Identifier(canary.username)))


def verify(physical, tenant):
    return physical["pg"].verify(tenant["database"], tenant["owner"], tenant["credentials"],
        tenant["identity"], "tenant_0001", ("_provisioning_identity", "isolation_records", "isolation_audit"),
        table_privileges=TABLE_POLICY, sequence_privileges=SEQUENCE_POLICY)


def test_no_cross_database_connect_including_central_and_maintenance(physical):
    for tenant in physical["tenants"]:
        others = [t["database"] for t in physical["tenants"] if t != tenant] + ["auth_test_db", "postgres", "template1"]
        for target in others:
            with pytest.raises(ProvisioningError, match="database_connect_denied"):
                physical["pg"].connect(target, tenant["credentials"])
    with physical["central"].connect() as c:
        assert c.scalar(text("SELECT current_database()")) == "auth_test_db"
    central_credentials = type(physical["canary"])(physical["central"].url.username, physical["central"].url.password)
    for tenant in physical["tenants"]:
        with pytest.raises(ProvisioningError, match="database_connect_denied"):
            physical["pg"].connect(tenant["database"], central_credentials)


def test_runtime_ddl_escalation_and_protected_metadata_are_denied(physical):
    for tenant in physical["tenants"]:
        with physical["pg"].connect(tenant["database"], tenant["credentials"]) as c:
            flags = c.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
            assert not any(flags)
            statements = ["CREATE SCHEMA forbidden_isolation_schema", "CREATE TABLE public.forbidden_isolation_table(id int)",
                "CREATE TEMP TABLE forbidden_isolation_temp(id int)", "UPDATE _provisioning_identity SET department_id=department_id",
                "UPDATE alembic_version SET version_num=version_num", "UPDATE isolation_audit SET label=label", "DELETE FROM isolation_audit",
                "SELECT setval('isolation_audit_id_seq',99)", "TRUNCATE isolation_audit", "ALTER TABLE isolation_records ADD COLUMN forbidden int", "SELECT * FROM pg_authid",
                "SELECT pg_read_file('isolation-no-such-file')"]
            statements += [sql.SQL("SET ROLE {}").format(sql.Identifier(role)) for role in
                (tenant["owner"], "isolation_provisioner", physical["tenants"][1]["credentials"].username if tenant == physical["tenants"][0] else physical["tenants"][0]["credentials"].username)]
            for statement in statements:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with c.transaction(force_rollback=True): c.execute(statement)
            # These cannot execute inside a transaction. Unexpected success would
            # retain the new synthetic object in this stopped cluster, never drop it.
            for statement in ("CREATE DATABASE forbidden_" + uuid.uuid4().hex,
                              "CREATE ROLE forbidden_" + uuid.uuid4().hex):
                with pytest.raises(psycopg.errors.InsufficientPrivilege): c.execute(statement)
        verify(physical, tenant)


def test_replay_preserves_database_oid_credentials_data_and_append_only_policy(physical):
    for tenant in physical["tenants"]:
        with physical["admin"].connect() as c:
            oid = c.execute("SELECT oid FROM pg_database WHERE datname=%s", (tenant["database"],)).fetchone()[0]
        with physical["pg"].connect(tenant["database"], tenant["credentials"]) as c:
            before = c.execute("SELECT * FROM isolation_audit ORDER BY id").fetchall()
        physical["pg"].ensure_resources(tenant["database"], tenant["owner"], tenant["credentials"], tenant["identity"])
        physical["pg"].grant_runtime(tenant["database"], tenant["owner"], tenant["credentials"],
            table_privileges=TABLE_POLICY, sequence_privileges=SEQUENCE_POLICY)
        with physical["admin"].connect() as c:
            assert c.execute("SELECT oid FROM pg_database WHERE datname=%s", (tenant["database"],)).fetchone()[0] == oid
        with physical["pg"].connect(tenant["database"], tenant["credentials"]) as c:
            assert c.execute("SELECT * FROM isolation_audit ORDER BY id").fetchall() == before
        verify(physical, tenant)


def test_missing_or_protected_table_policy_fails_without_changing_grants(physical):
    tenant = physical["tenants"][0]
    def grants():
        with physical["admin"].connect(tenant["database"]) as c:
            return c.execute("SELECT relname,relacl::text FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY relname").fetchall()
    before = grants()
    for policy, code in [(None, "runtime_grant_policy_missing"), ({"_provisioning_identity": ("UPDATE",)}, "runtime_grant_policy_invalid"),
                         ({"isolation_audit": ("TRUNCATE",)}, "runtime_grant_policy_invalid")]:
        with pytest.raises(ProvisioningError, match=code):
            physical["pg"].grant_runtime(tenant["database"], tenant["owner"], tenant["credentials"],
                table_privileges=policy, sequence_privileges=SEQUENCE_POLICY)
        assert grants() == before


def test_public_column_overgrant_is_detected_without_repairing_others(physical):
    tenant = physical["tenants"][0]
    with physical["admin"].connect(tenant["database"]) as c:
        c.execute("GRANT UPDATE(label) ON isolation_audit TO PUBLIC")
        before = c.execute("SELECT relname,relacl::text FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY relname").fetchall()
    try:
        with pytest.raises(ProvisioningError, match="runtime_permission_mismatch"): verify(physical, tenant)
        with pytest.raises(ProvisioningError, match="runtime_permission_mismatch"):
            physical["pg"].grant_runtime(tenant["database"], tenant["owner"], tenant["credentials"], table_privileges=TABLE_POLICY, sequence_privileges=SEQUENCE_POLICY)
        with physical["admin"].connect(tenant["database"]) as c:
            assert c.execute("SELECT relname,relacl::text FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY relname").fetchall() == before
            assert c.execute("SELECT has_any_column_privilege(%s,'public.isolation_audit','UPDATE')", (tenant["credentials"].username,)).fetchone()[0]
    finally:
        with physical["admin"].connect(tenant["database"]) as c:
            c.execute("REVOKE UPDATE(label) ON isolation_audit FROM PUBLIC")
    verify(physical, tenant)


def test_global_default_overgrant_is_rejected_not_silently_repaired(physical):
    tenant = physical["tenants"][0]
    def default(statement):
        with physical["pg"].connect(tenant["database"]) as c, c.transaction():
            c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(tenant["owner"])))
            c.execute(sql.SQL(statement).format(sql.Identifier(tenant["credentials"].username)))
    default("ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO {}")
    try:
        with pytest.raises(ProvisioningError, match="runtime_default_privileges"): verify(physical, tenant)
        with pytest.raises(ProvisioningError, match="runtime_default_privileges"):
            physical["pg"].grant_runtime(tenant["database"], tenant["owner"], tenant["credentials"], table_privileges=TABLE_POLICY, sequence_privileges=SEQUENCE_POLICY)
    finally:
        default("ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM {}")
    verify(physical, tenant)


def test_identity_revision_and_connect_overgrants_cannot_pass_verification(physical):
    tenant = physical["tenants"][0]
    for statement, restore, code in [
        ("UPDATE _provisioning_identity SET department_id='synthetic-wrong'", "UPDATE _provisioning_identity SET department_id=%s", "tenant_identity_conflict"),
        ("UPDATE alembic_version SET version_num='synthetic-wrong'", "UPDATE alembic_version SET version_num=%s", "migration_revision_mismatch")]:
        with physical["admin"].connect(tenant["database"]) as c: c.execute(statement)
        try:
            with pytest.raises(ProvisioningError, match=code): verify(physical, tenant)
        finally:
            with physical["admin"].connect(tenant["database"]) as c:
                c.execute(restore, (tenant["identity"] if code == "tenant_identity_conflict" else "tenant_0001",))
    with physical["admin"].connect() as c:
        c.execute(sql.SQL("GRANT CONNECT ON DATABASE auth_test_db TO {}").format(sql.Identifier(tenant["credentials"].username)))
    try:
        with pytest.raises(ProvisioningError, match="cross_database_connect"): verify(physical, tenant)
    finally:
        with physical["admin"].connect() as c:
            c.execute(sql.SQL("REVOKE CONNECT ON DATABASE auth_test_db FROM {}").format(sql.Identifier(tenant["credentials"].username)))
    verify(physical, tenant)


def test_runtime_pool_rejects_inherited_privilege_and_database_ownership(physical):
    canary = physical["canary"]
    url = physical["bootstrap"].url.set(username=canary.username, password=canary.password)
    with physical["admin"].connect() as c:
        c.execute(sql.SQL("GRANT isolation_indirect TO {}").format(sql.Identifier(canary.username)))
    def denied():
        engine = restrict_runtime_engine(create_engine(url, hide_parameters=True))
        try:
            with pytest.raises(RuntimeError, match="Administrative credentials"):
                with engine.connect(): pass
        finally: engine.dispose()
    try: denied()
    finally:
        with physical["admin"].connect() as c:
            c.execute(sql.SQL("REVOKE isolation_indirect FROM {}").format(sql.Identifier(canary.username)))
    with physical["admin"].connect() as c:
        owner = c.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='auth_test_db'").fetchone()[0]
        c.execute(sql.SQL("ALTER DATABASE auth_test_db OWNER TO {}").format(sql.Identifier(canary.username)))
    try: denied()
    finally:
        with physical["admin"].connect() as c:
            c.execute(sql.SQL("ALTER DATABASE auth_test_db OWNER TO {}").format(sql.Identifier(owner)))
            # ALTER OWNER folds the new owner's explicit ACL into ownership;
            # restore this synthetic canary's original CONNECT after returning it.
            c.execute(sql.SQL("GRANT CONNECT ON DATABASE auth_test_db TO {}").format(sql.Identifier(canary.username)))
    # The same ordinary identity is accepted after the excessive rights are gone.
    engine = restrict_runtime_engine(create_engine(url, hide_parameters=True))
    try:
        with engine.connect() as c: assert c.scalar(text("SELECT 1")) == 1
    finally: engine.dispose()


def test_tenant_owner_and_membership_overgrants_are_rejected(physical):
    tenant = physical["tenants"][0]
    with physical["admin"].connect() as c:
        c.execute(sql.SQL("GRANT isolation_indirect TO {}").format(sql.Identifier(tenant["credentials"].username)))
    try:
        with pytest.raises(ProvisioningError, match="runtime_role_membership"): verify(physical, tenant)
    finally:
        with physical["admin"].connect() as c:
            c.execute(sql.SQL("REVOKE isolation_indirect FROM {}").format(sql.Identifier(tenant["credentials"].username)))
    with physical["admin"].connect(tenant["database"]) as c:
        c.execute(sql.SQL("ALTER TABLE isolation_audit OWNER TO {}").format(sql.Identifier(tenant["credentials"].username)))
    try:
        with pytest.raises(ProvisioningError, match="runtime_object_owner"): verify(physical, tenant)
    finally:
        with physical["admin"].connect(tenant["database"]) as c:
            c.execute(sql.SQL("ALTER TABLE isolation_audit OWNER TO {}").format(sql.Identifier(tenant["owner"])))
        physical["pg"].grant_runtime(tenant["database"], tenant["owner"], tenant["credentials"], table_privileges=TABLE_POLICY, sequence_privileges=SEQUENCE_POLICY)
    verify(physical, tenant)


@pytest.mark.parametrize("capability", ["CREATEROLE", "REPLICATION"])
def test_api_pools_reject_provisioning_identity_and_inherited_role_flags(physical, capability):
    engine = restrict_runtime_engine(create_engine(physical["pg"].url, hide_parameters=True))
    try:
        with pytest.raises(RuntimeError, match="Administrative credentials"):
            with engine.connect(): pass
    finally: engine.dispose()
    canary = physical["canary"]
    parent = "isolation_parent_" + capability.lower()
    with physical["admin"].connect() as c:
        c.execute(sql.SQL("CREATE ROLE {} NOLOGIN {}").format(sql.Identifier(parent), sql.SQL(capability)))
        c.execute(sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE").format(sql.Identifier(parent), sql.Identifier(canary.username)))
    engine = restrict_runtime_engine(create_engine(physical["bootstrap"].url.set(username=canary.username, password=canary.password), hide_parameters=True))
    try:
        with pytest.raises(RuntimeError, match="Administrative credentials"):
            with engine.connect(): pass
    finally:
        engine.dispose()
        with physical["admin"].connect() as c:
            c.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(parent), sql.Identifier(canary.username)))


def test_managed_owner_and_runtime_have_separate_identities(physical):
    for tenant in physical["tenants"]:
        with physical["admin"].connect(tenant["database"]) as c:
            assert c.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()[0] == tenant["owner"]
            assert c.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname=%s", (tenant["owner"],)).fetchone() == (False,)
            assert c.execute("SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s))", (tenant["credentials"].username,)).fetchone() == (False,)
        engine = restrict_runtime_engine(create_engine(physical["bootstrap"].url.set(database=tenant["database"], username=tenant["credentials"].username, password=tenant["credentials"].password), hide_parameters=True))
        try:
            with engine.connect() as c:
                assert c.scalar(text("SELECT current_user")) == tenant["credentials"].username
        finally: engine.dispose()


def test_explicit_policy_reconciles_old_runtime_table_overgrants(physical):
    tenant = physical["tenants"][0]
    with physical["pg"].connect(tenant["database"]) as c, c.transaction():
        c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(tenant["owner"])))
        c.execute(sql.SQL("GRANT UPDATE,DELETE ON isolation_audit TO {}").format(sql.Identifier(tenant["credentials"].username)))
    with pytest.raises(ProvisioningError, match="runtime_permission_mismatch"): verify(physical, tenant)
    physical["pg"].grant_runtime(tenant["database"], tenant["owner"], tenant["credentials"],
        table_privileges=TABLE_POLICY, sequence_privileges=SEQUENCE_POLICY)
    verify(physical, tenant)


@pytest.mark.parametrize("extra", ["grant_option", "column_grant_option", "maintenance"])
def test_unlisted_privileges_and_grant_options_are_rejected(physical, extra):
    tenant = physical["tenants"][0]
    role = sql.Identifier(tenant["credentials"].username)
    statements = {
        "grant_option": ("GRANT SELECT ON isolation_audit TO {} WITH GRANT OPTION", "REVOKE GRANT OPTION FOR SELECT ON isolation_audit FROM {}"),
        "column_grant_option": ("GRANT SELECT(label) ON isolation_audit TO {} WITH GRANT OPTION", "REVOKE GRANT OPTION FOR SELECT(label) ON isolation_audit FROM {}"),
        "maintenance": ("GRANT MAINTAIN ON isolation_audit TO {}", "REVOKE MAINTAIN ON isolation_audit FROM {}"),
    }
    grant, revoke = statements[extra]
    with physical["admin"].connect(tenant["database"]) as c:
        c.execute(sql.SQL(grant).format(role))
    try:
        with pytest.raises(ProvisioningError, match="runtime_permission_mismatch"): verify(physical, tenant)
    finally:
        with physical["admin"].connect(tenant["database"]) as c:
            c.execute(sql.SQL(revoke).format(role))
    verify(physical, tenant)
