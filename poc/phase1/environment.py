"""PoC-only schema/bootstrap and verification; never imports application modules."""
import json
from argon2 import PasswordHasher
from psycopg import sql
from dataset import DEPARTMENT, OTHER


def literal(value):
    return sql.Literal(value).as_string()


def seed(execute, data, run_id, passwords):
    # execute(db, SQL, identity='bootstrap') is bound by the controller to this
    # newly created cluster. No URL, host or existing database can be selected.
    names = json.loads(execute("poc_registry", "SELECT json_agg(datname ORDER BY datname) FROM pg_database"))
    if set(names) != {"poc_registry", "postgres", "template0", "template1"}:
        raise RuntimeError("PoC requires a newly created cluster")
    if execute("poc_registry", "SELECT count(*) FROM pg_tables WHERE schemaname='public'").strip() != "0":
        raise RuntimeError("Refusing populated registry")
    for role, password_key in [("poc_registry_app", "registry"), ("poc_tenant_app", "tenant")]:
        execute("poc_registry", f"CREATE ROLE {role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {literal(passwords[password_key])}")
    execute("poc_registry", "CREATE ROLE poc_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
    execute("poc_registry", "CREATE DATABASE poc_experimental OWNER poc_owner TEMPLATE template0")
    for name in ["poc_registry", "poc_experimental", "postgres", "template1"]:
        execute("poc_registry", f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
    execute("poc_registry", "GRANT CONNECT ON DATABASE poc_registry TO poc_registry_app")
    execute("poc_registry", "GRANT CONNECT ON DATABASE poc_experimental TO poc_tenant_app")
    identity = literal(run_id)
    execute("poc_registry", f"""
        BEGIN;
        REVOKE ALL ON SCHEMA public FROM PUBLIC;
        CREATE SCHEMA poc_data;
        CREATE TABLE poc_data.identity(run_id text PRIMARY KEY, purpose text NOT NULL);
        INSERT INTO poc_data.identity VALUES ({identity}, 'synthetic-phase1-only');
        CREATE TABLE poc_data.departments(id text PRIMARY KEY, name text NOT NULL);
        INSERT INTO poc_data.departments VALUES
          ({literal(DEPARTMENT)}, 'Experimental Resources Department'),
          ({literal(OTHER)}, 'Other Synthetic Department');
        CREATE TABLE poc_data.users(id text PRIMARY KEY, profile jsonb NOT NULL);
        CREATE TABLE poc_data.credentials(user_id text PRIMARY KEY REFERENCES poc_data.users(id), password_hash text NOT NULL);
        GRANT USAGE ON SCHEMA poc_data TO poc_registry_app;
        GRANT SELECT ON poc_data.identity,poc_data.users,poc_data.departments TO poc_registry_app;
        COMMIT;
    """)
    hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
    for user in data["users"]:
        execute("poc_registry", f"""BEGIN;
            INSERT INTO poc_data.users VALUES ({literal(user['id'])}, {literal(json.dumps(user))}::jsonb);
            INSERT INTO poc_data.credentials VALUES ({literal(user['id'])}, {literal(hasher.hash(passwords['accounts'][user['id']]))});
            COMMIT;""")
    execute("poc_experimental", f"""
        SET ROLE poc_owner;
        BEGIN;
        REVOKE ALL ON SCHEMA public FROM PUBLIC;
        CREATE SCHEMA poc_data;
        CREATE TABLE poc_data.identity(run_id text PRIMARY KEY, purpose text NOT NULL);
        INSERT INTO poc_data.identity VALUES ({identity}, 'synthetic-phase1-only');
        CREATE TABLE poc_data.fixture_catalog(
          artifact_id text PRIMARY KEY, document_id text NOT NULL, version integer NOT NULL,
          status text NOT NULL CHECK(status IN ('draft','published','archived')),
          metadata jsonb NOT NULL, UNIQUE(document_id,version));
        CREATE UNIQUE INDEX one_published_version ON poc_data.fixture_catalog(document_id) WHERE status='published';
        CREATE TABLE poc_data.version_events(
          sequence integer PRIMARY KEY, event text NOT NULL, before_state jsonb NOT NULL, after_state jsonb NOT NULL);
        GRANT USAGE ON SCHEMA poc_data TO poc_tenant_app;
        GRANT SELECT ON poc_data.identity,poc_data.fixture_catalog,poc_data.version_events TO poc_tenant_app;
        COMMIT;
    """)
    for doc in data["documents"]:
        status = data["version_transition"]["initial"].get(doc["artifact_id"], doc["status"])
        execute("poc_experimental", f"""SET ROLE poc_owner;
            INSERT INTO poc_data.fixture_catalog VALUES
            ({literal(doc['artifact_id'])},{literal(doc['document_id'])},{doc['version']},
             {literal(status)},{literal(json.dumps(doc))}::jsonb);""")
    before = json.loads(execute("poc_experimental", "SELECT json_object_agg(artifact_id,status) FROM poc_data.fixture_catalog WHERE document_id='D1'"))
    assert before == data["version_transition"]["initial"]
    initial = literal(json.dumps(before))
    final = literal(json.dumps(data["version_transition"]["final"]))
    # Actual persisted transition, not just two independently labelled files.
    execute("poc_experimental", f"""SET ROLE poc_owner;
        BEGIN;
        INSERT INTO poc_data.version_events VALUES (1,'initial_publication','{{}}'::jsonb,{initial}::jsonb);
        UPDATE poc_data.fixture_catalog SET status='archived' WHERE artifact_id='D1-v1' AND status='published';
        UPDATE poc_data.fixture_catalog SET status='published' WHERE artifact_id='D1-v2' AND status='draft';
        INSERT INTO poc_data.version_events VALUES (2,'publish_version_2',{initial}::jsonb,{final}::jsonb);
        COMMIT;""")


def verify(execute, data, run_id):
    report = {"run_id": run_id, "synthetic_only": True, "checks": {}}
    for db, role in [("poc_registry", "registry"), ("poc_experimental", "tenant")]:
        identity = execute(db, "SELECT run_id FROM poc_data.identity", role).strip()
        assert identity == run_id
        flags = json.loads(execute(db, "SELECT json_build_array(rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls) FROM pg_roles WHERE rolname=current_user", role))
        assert flags == [False] * 5
        assert execute(db, "SELECT has_database_privilege(current_user,current_database(),'CREATE') OR has_database_privilege(current_user,current_database(),'TEMP') OR has_schema_privilege(current_user,'public','CREATE')", role).strip() == "f"
        assert execute(db, "SELECT has_schema_privilege(current_user,'poc_data','CREATE') OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user))", role).strip() == "f"
        assert execute(db, "SELECT count(*) FROM pg_database WHERE datallowconn AND datname<>current_database() AND has_database_privilege(current_user,oid,'CONNECT')", role).strip() == "0"
        other = "poc_registry" if role == "tenant" else "poc_experimental"
        assert execute(db, f"SELECT has_database_privilege(current_user,'{other}','CONNECT')", role).strip() == "f"
        try:
            execute(db, "CREATE SCHEMA forbidden_phase1_probe", role)
        except PermissionError:
            pass
        else:
            raise AssertionError("Runtime DDL unexpectedly succeeded")
        try:
            execute(other, "SELECT 1", role)
        except PermissionError:
            pass
        else:
            raise AssertionError("Cross-database connection unexpectedly succeeded")
        report["checks"][db] = "identity/least-privilege/cross-database-ACL/negative-DDL passed"
    rows = json.loads(execute("poc_registry", "SELECT json_agg(profile ORDER BY id) FROM poc_data.users", "registry"))
    assert rows == sorted(data["users"], key=lambda row: row["id"])
    try:
        execute("poc_registry", "SELECT * FROM poc_data.credentials", "registry")
    except PermissionError:
        pass
    else:
        raise AssertionError("Runtime read password hashes")
    actual = json.loads(execute("poc_experimental", "SELECT json_object_agg(artifact_id,status) FROM poc_data.fixture_catalog WHERE document_id='D1'", "tenant"))
    assert actual == data["version_transition"]["final"]
    assert execute("poc_experimental", "SELECT count(*) FROM poc_data.fixture_catalog", "tenant").strip() == "7"
    events = json.loads(execute("poc_experimental", "SELECT json_agg(json_build_object('sequence',sequence,'event',event,'before',before_state,'after',after_state) ORDER BY sequence) FROM poc_data.version_events", "tenant"))
    assert events[0]["after"] == events[1]["before"] == data["version_transition"]["initial"]
    assert events[1]["after"] == actual
    for query in ["UPDATE poc_data.fixture_catalog SET status=status", "DELETE FROM poc_data.version_events"]:
        try:
            execute("poc_experimental", query, "tenant")
        except PermissionError:
            pass
        else:
            raise AssertionError("Tenant reader modified fixture history")
    report["version_events"] = events
    report["users"] = len(rows)
    report["artifacts"] = 7
    report["rag_cases_executed"] = 0
    return report
