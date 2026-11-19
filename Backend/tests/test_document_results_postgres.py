"""Actual PG result migration + least-privilege checks; no pgvector needed.

This tests tenant_0007 in isolation, not the complete document migration chain.
"""
import importlib.util
from pathlib import Path
import secrets

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.documents import result_store
from app.documents.models import Document, DocumentAudience
from app.runtime_security import restrict_runtime_engine
from app.usage.service import UsageError
from document_test_infrastructure import fresh_db
from test_authentication_postgres import isolated_postgres

pytestmark = pytest.mark.integration


def test_tenant_result_migration_immutable_grants_and_replay_access(fresh_db):
    owner = create_engine(fresh_db["url"], hide_parameters=True)
    migration = Path(__file__).parents[1] / "tenant_alembic/versions/tenant_0007_question_results.py"
    spec = importlib.util.spec_from_file_location("result_migration", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Explicitly scoped standalone migration test. No stamp or fake full-chain proof.
    with owner.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        Document.__table__.create(connection)
        DocumentAudience.__table__.create(connection)
    role = fresh_db["db_name"] + "_app"
    password = secrets.token_urlsafe(32)
    with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        pg = connection.connection.driver_connection
        pg.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)))
        pg.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(fresh_db["db_name"]), sql.Identifier(role)))
        pg.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        pg.execute(sql.SQL("GRANT SELECT ON documents, document_audiences TO {}").format(sql.Identifier(role)))
    runtime = restrict_runtime_engine(create_engine(fresh_db["url"].set(username=role, password=password), hide_parameters=True))
    with Session(owner) as session, session.begin():
        session.add(Document(id=1, filename="synthetic", file_path="unused", department_id="synthetic", status="approved", version=1))
        session.add(DocumentAudience(document_id=1, department_id="synthetic", role_id="reader", created_by="owner"))
    with owner.begin() as c:
        c.execute(text("INSERT INTO question_results(request_id,user_id,payload) VALUES ('legacy','employee','{}')"))
    freeze = migration.with_name("tenant_0008_freeze_legacy_results.py")
    spec = importlib.util.spec_from_file_location("legacy_freeze", freeze)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with owner.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    # Even the owner cannot mutate historical results through ordinary DML.
    for statement in ("UPDATE question_results SET user_id='changed'", "DELETE FROM question_results",
                      "TRUNCATE question_results",
                      "INSERT INTO question_results(request_id,user_id,payload) VALUES ('new','employee','{}')"):
        with owner.begin() as connection, pytest.raises(DBAPIError) as error:
            connection.execute(text(statement))
        assert error.value.orig.sqlstate == "23514"
    try:
        for operation in (result_store.save, result_store.load):
            with pytest.raises(UsageError, match="disabled"):
                operation()
        with owner.connect() as c:
            assert c.scalar(text("SELECT count(*) FROM question_results")) == 1
        for statement in ("SELECT * FROM question_results", "INSERT INTO question_results(request_id,user_id,payload) VALUES ('new','employee','{}')", "UPDATE question_results SET user_id='other'", "DELETE FROM question_results",
                          "CREATE TABLE forbidden(id int)"):
            with runtime.connect() as connection, pytest.raises(DBAPIError) as error:
                connection.execute(text(statement))
            assert error.value.orig.sqlstate == "42501"
    finally:
        runtime.dispose()
        owner.dispose()
