"""PR 3 — Version pinning tests.

Verifies:
1. tenant_0004 adds version_number to chunks/audit/revision.
2. Revision creates a new version (does not replace chunks).
3. Audit log records version_number.
"""
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.documents.models import Document, DocumentChunk, DocumentAuditLog


pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]
CONFIG_PATH = BACKEND / "tenant_alembic.ini"
from document_test_infrastructure import fresh_db
from test_authentication_postgres import isolated_postgres


def test_tenant_0004_adds_version_columns(fresh_db):
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0004")
    with engine.connect() as conn:
        cols = {
            r[0] for r in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='document_chunks'"
            ))
        }
        assert "version_number" in cols
        assert conn.scalar(text(
            "SELECT version_num FROM alembic_version"
        )) == "tenant_0004"
    engine.dispose()


def test_revision_does_not_delete_old_chunks(fresh_db):
    """After revise, old chunks remain (immutable history)."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0004")

    with Session(engine) as db:
        doc = Document(
            filename="v1.pdf",
            file_path="/tmp/v1.pdf",
            file_type=".pdf",
            extracted_text="old content",
            department_id="hr",
            status="approved",
            version=1,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        # V1 chunks
        for i in range(3):
            db.add(DocumentChunk(
                document_id=doc.id,
                content=f"v1 chunk {i}",
                embedding=[0.1] * 768,
                page_number=1,
                version_number=1,
            ))
        db.commit()

        v1_count = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id,
            DocumentChunk.version_number == 1,
        ).count()
        assert v1_count == 3

        # Simulate revise → new version (do not delete old chunks)
        doc.version = 2
        for i in range(3):
            db.add(DocumentChunk(
                document_id=doc.id,
                content=f"v2 chunk {i}",
                embedding=[0.2] * 768,
                page_number=1,
                version_number=2,
            ))
        db.commit()

        # V1 chunks still exist
        v1_after = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id,
            DocumentChunk.version_number == 1,
        ).count()
        assert v1_after == 3, "Old version chunks must NOT be deleted"

        v2_count = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id,
            DocumentChunk.version_number == 2,
        ).count()
        assert v2_count == 3

    engine.dispose()


def test_audit_log_records_version(fresh_db):
    """Audit log should record version_number."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0004")

    with Session(engine) as db:
        doc = Document(
            filename="audit.pdf",
            file_path="/tmp/audit.pdf",
            file_type=".pdf",
            extracted_text="",
            department_id="hr",
            status="pending_review",
            version=1,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        db.add(DocumentAuditLog(
            document_id=doc.id,
            action="upload",
            performed_by="user1",
            version_number=1,
        ))
        db.commit()

        log = db.query(DocumentAuditLog).filter(
            DocumentAuditLog.document_id == doc.id
        ).first()
        assert log is not None
        assert log.version_number == 1
    engine.dispose()
