"""B1 — Tenant migration contract test.

Verifies that `tenant_0002..tenant_0006` produce the required schema
on a fresh isolated database, without touching HR/Finance/Legal.

Run:
    pytest tests/test_tenant_migration.py -v --isolated-postgres

This test is marked `integration` — requires `--isolated-postgres`
to run, per conftest.py.
"""
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]
CONFIG_PATH = BACKEND / "tenant_alembic.ini"

from document_test_infrastructure import fresh_db
from test_authentication_postgres import isolated_postgres


def test_tenant_0002_creates_required_tables(fresh_db):
    """tenant_0002 should create all required tables + pgvector."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))

    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0002")

    required = {
        "_provisioning_identity",
        "alembic_version",
        "documents",
        "document_versions",
        "document_revision_logs",
        "document_audit_logs",
        "document_chunks",
    }

    with engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            ))
        }
        assert required.issubset(tables), (
            f"Missing tables: {required - tables}"
        )

        # pgvector extension
        assert conn.scalar(text(
            "SELECT extname FROM pg_extension WHERE extname='vector'"
        )) == "vector", "pgvector extension missing"

        # HNSW index on embedding
        assert conn.scalar(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE indexname='ix_document_chunks_embedding_hnsw'"
        )) == "ix_document_chunks_embedding_hnsw", "HNSW index missing"

        # Revision is exactly tenant_0002
        assert conn.scalar(text(
            "SELECT version_num FROM alembic_version"
        )) == "tenant_0002", "Revision not pinned to tenant_0002"

    engine.dispose()


def test_documents_have_expected_columns(fresh_db):
    """documents table should have the B1 schema."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))

    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0002")

    with engine.connect() as conn:
        cols = {
            r[0]
            for r in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='documents'"
            ))
        }

    expected = {
        "id", "filename", "file_path", "file_type", "extracted_text",
        "department_id", "status", "version", "upload_date",
    }
    assert expected.issubset(cols), f"Missing columns: {expected - cols}"
    engine.dispose()


def test_version_uniqueness_constraint(fresh_db):
    """(document_id, version_number) should be UNIQUE."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))

    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0002")

    with engine.connect() as conn:
        constraints = {
            r[0]
            for r in conn.execute(text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name='document_versions' AND constraint_type='UNIQUE'"
            ))
        }

    assert "uq_document_version" in constraints, (
        f"UNIQUE constraint missing. Found: {constraints}"
    )
    engine.dispose()


def test_contract_matches_schema(fresh_db):
    """tenant-contract.json should declare the same tables as the schema."""
    import json
    contract_path = BACKEND / "tenant-contract.json"
    assert contract_path.exists(), "tenant-contract.json missing"

    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    assert contract["contract_version"] == 1, "contract_version must be int 1"
    assert contract["required_revision"] == "tenant_0007"
    assert contract["ready"] is False
    assert "_provisioning_identity" in contract["required_tables"]
    assert set(contract["required_tables"]).issubset({
        "_provisioning_identity",
        "documents",
        "document_versions",
        "document_revision_logs",
        "document_audit_logs",
        "document_chunks",
        "document_audiences",
        "question_results",
    })
    # table_privileges / sequence_privileges present
    assert "table_privileges" in contract
    assert "sequence_privileges" in contract
    assert "documents" in contract["table_privileges"]
    assert "document_audiences" in contract["table_privileges"]
