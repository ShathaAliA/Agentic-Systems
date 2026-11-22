"""Provisioning helpers for the real joint /ask gate: a real C06 store database and a restricted
tenant runtime role, both created inside the fresh isolated cluster only. No fixed URLs, no drops."""
from pathlib import Path
import secrets
import uuid

from alembic import command as migrate
from alembic.config import Config
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import create_engine

BACKEND = Path(__file__).resolve().parents[1]


def provision_c06_store(admin):
    """Create + migrate a NEW C06 store database; return (runtime_engine, database_name, owner_role)."""
    name = "c06_joint_" + uuid.uuid4().hex[:12]
    owner, login, password = "c06_own_" + uuid.uuid4().hex[:8], "c06_run_" + uuid.uuid4().hex[:8], secrets.token_urlsafe(32)
    url = admin.url
    params = dict(host=url.host, port=url.port or 5432, dbname=url.database, user=url.username, password=url.password)
    with psycopg.connect(**params, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        c.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(owner)))
        c.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}")
                  .format(sql.Identifier(login), sql.Literal(password)))
    store_admin = create_engine(url.set(database=name), hide_parameters=True)
    try:
        with store_admin.begin() as c:
            c.exec_driver_sql(f'GRANT USAGE,CREATE ON SCHEMA public TO "{owner}"')
        cfg = Config(str(BACKEND / "result_store_alembic.ini"))
        with store_admin.begin() as c:
            c.exec_driver_sql(f'SET LOCAL ROLE "{owner}"')
            cfg.attributes.update(connection=c, expected_database=name, store_migration_approved=True)
            migrate.upgrade(cfg, "head")
    finally:
        store_admin.dispose()
    with psycopg.connect(**{**params, "dbname": name}, autocommit=True) as c:
        c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(login)))
        c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(login)))
        c.execute(sql.SQL("GRANT SELECT ON c06_store_identity,c06_store_version,c06_result_sources TO {}").format(sql.Identifier(login)))
        c.execute(sql.SQL("GRANT SELECT,INSERT ON c06_results,c06_conversations,c06_turns,c06_question_intents,c06_handoffs,c06_handoff_events,c06_handoff_messages,c06_handoff_reads,c06_handoff_access_log TO {}").format(sql.Identifier(login)))
    runtime = create_engine(url.set(database=name, username=login, password=password), hide_parameters=True)
    return runtime, name, owner


@pytest.fixture(scope="module")
def c06_store_database(isolated_postgres):
    admin, _, _ = isolated_postgres
    runtime, name, _ = provision_c06_store(admin)
    try:
        yield runtime, name
    finally:
        runtime.dispose()


def provision_tenant_runtime(fresh_db):
    """Run the real tenant migrations and create the restricted runtime role Documents uses."""
    owner = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(BACKEND / "tenant_alembic.ini"))
    with owner.begin() as c:
        cfg.attributes["connection"] = c
        migrate.upgrade(cfg, "head")
    role, password, database = "joint_" + secrets.token_hex(6), secrets.token_urlsafe(32), fresh_db["db_name"]
    with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        pg = c.connection.driver_connection
        pg.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}")
                   .format(sql.Identifier(role), sql.Literal(password)))
        pg.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        pg.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        pg.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role)))
        pg.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        pg.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE ON documents,document_versions TO {}").format(sql.Identifier(role)))
        pg.execute(sql.SQL("GRANT SELECT,INSERT ON document_chunks,document_audit_logs,document_revision_logs TO {}").format(sql.Identifier(role)))
        pg.execute(sql.SQL("GRANT SELECT,INSERT,DELETE ON document_audiences TO {}").format(sql.Identifier(role)))
        pg.execute(sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(role)))
    return owner, fresh_db["url"].set(username=role, password=password)
