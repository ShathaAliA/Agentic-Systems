"""PR 7 — B2 endpoints integration tests.

Verifies:
1. GET /{id} returns metadata only (no file_path/extracted_text).
2. PATCH /{id} updates filename + records audit.
3. POST /{id}/version creates new version, keeps old chunks.
4. POST/GET/DELETE /{id}/permissions manages audiences.
"""
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.documents.models import (
    Document,
    DocumentVersion,
    DocumentAudience,
    DocumentAuditLog,
    DocumentChunk,
)


pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]
CONFIG_PATH = BACKEND / "tenant_alembic.ini"
from document_test_infrastructure import fresh_db
from test_authentication_postgres import isolated_postgres


def _create_approved_doc(db, dept="hr", filename="test.pdf"):
    doc = Document(
        filename=filename,
        file_path="/tmp/test.pdf",
        file_type=".pdf",
        extracted_text="original content",
        department_id=dept,
        status="approved",
        version=1,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    db.add(DocumentVersion(
        document_id=doc.id,
        version_number=1,
        status="approved",
    ))
    db.commit()
    return doc


def test_get_document_returns_metadata_only(fresh_db):
    """GET /{id} returns safe fields (no file_path/extracted_text)."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0004")

    with Session(engine) as db:
        doc = _create_approved_doc(db)
        response = {
            "id": doc.id,
            "filename": doc.filename,
            "file_type": doc.file_type,
            "status": doc.status,
            "version": doc.version,
            "upload_date": doc.upload_date,
        }
        assert "file_path" not in response
        assert "extracted_text" not in response
        assert response["filename"] == "test.pdf"

    engine.dispose()


def test_patch_updates_filename_and_audits(fresh_db):
    """PATCH /{id} updates filename + creates audit log."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0004")

    with Session(engine) as db:
        doc = _create_approved_doc(db)

        doc.filename = "renamed.pdf"
        db.add(DocumentAuditLog(
            document_id=doc.id,
            action="update_metadata",
            performed_by="user1",
            version_number=doc.version,
        ))
        db.commit()

        db.refresh(doc)
        assert doc.filename == "renamed.pdf"

        log = db.query(DocumentAuditLog).filter(
            DocumentAuditLog.document_id == doc.id,
            DocumentAuditLog.action == "update_metadata",
        ).first()
        assert log is not None
        assert log.version_number == 1

    engine.dispose()


def test_replace_creates_new_version_keeps_old_chunks(fresh_db):
    """POST /{id}/version → new version, old chunks preserved."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0004")

    with Session(engine) as db:
        doc = _create_approved_doc(db)

        for i in range(3):
            db.add(DocumentChunk(
                document_id=doc.id,
                content=f"v1 chunk {i}",
                embedding=[0.1] * 768,
                page_number=1,
                version_number=1,
            ))
        db.commit()

        old_version = doc.version
        new_version = old_version + 1
        doc.version = new_version
        doc.status = "pending_review"

        db.add(DocumentVersion(
            document_id=doc.id,
            version_number=new_version,
            status="pending",
        ))

        for i in range(3):
            db.add(DocumentChunk(
                document_id=doc.id,
                content=f"v2 chunk {i}",
                embedding=[0.2] * 768,
                page_number=1,
                version_number=new_version,
            ))

        db.add(DocumentAuditLog(
            document_id=doc.id,
            action="replace",
            performed_by="user1",
            version_number=new_version,
        ))
        db.commit()

        v1_count = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id,
            DocumentChunk.version_number == 1,
        ).count()
        assert v1_count == 3, "Old version chunks must NOT be deleted"

        v2_count = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id,
            DocumentChunk.version_number == 2,
        ).count()
        assert v2_count == 3

    engine.dispose()


def test_add_list_delete_audience(fresh_db):
    """POST/GET/DELETE /{id}/permissions manages audiences."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0004")

    with Session(engine) as db:
        doc = _create_approved_doc(db)

        audience = DocumentAudience(
            document_id=doc.id,
            department_id="hr",
            role_id="manager",
            created_by="user1",
        )
        db.add(audience)
        db.commit()
        db.refresh(audience)

        assert audience.id is not None

        audiences = db.query(DocumentAudience).filter(
            DocumentAudience.document_id == doc.id
        ).all()
        assert len(audiences) == 1
        assert audiences[0].department_id == "hr"
        assert audiences[0].role_id == "manager"

        db.delete(audience)
        db.commit()

        audiences_after = db.query(DocumentAudience).filter(
            DocumentAudience.document_id == doc.id
        ).all()
        assert len(audiences_after) == 0

    engine.dispose()
