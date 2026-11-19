"""E2E: Documents lifecycle (upload → extract → approve → retrieve → archive).

Storage-level regressions with deterministic vectors; no extraction or LLM.
The actual authenticated PDF lifecycle is test_document_pipeline_postgres.py.

Run:
    pytest tests/test_e2e_documents.py -v --isolated-postgres
"""
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.documents.models import (
    Document, DocumentChunk, DocumentVersion, DocumentAuditLog, DocumentAudience,
)
from app.documents import rag_service


pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]
CONFIG_PATH = BACKEND / "tenant_alembic.ini"
# Only the guarded fresh-cluster runner may supply a connection. No fixed
# URL, connection termination or database deletion is performed by this suite.
from document_test_infrastructure import fresh_db
from test_authentication_postgres import isolated_postgres


def _migrate(url):
    engine = create_engine(url, hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    return engine


def test_full_document_lifecycle(fresh_db):
    """
    E2E: upload → extract (fake) → chunks → approve → retrieve → archive.
    """
    engine = _migrate(fresh_db["url"])
    try:
        with Session(engine) as db:
            # 1) Upload (simulate: document + version + chunks)
            doc = Document(
                filename="policy.pdf",
                file_path="/tmp/policy.pdf",
                file_type=".pdf",
                extracted_text="سياسة الإجازات: 30 يوم إجازة سنوية",
                department_id="hr",
                status="pending_review",
                version=1,
            )
            db.add(doc); db.commit(); db.refresh(doc)

            db.add(DocumentAudience(document_id=doc.id,department_id="hr",role_id="reader",created_by="test_user"))
            db.add(DocumentVersion(document_id=doc.id, version_number=1, status="pending"))
            db.add(DocumentAuditLog(
                document_id=doc.id, action="upload",
                performed_by="test_user", version_number=1,
            ))
            db.commit()

            rag_service.process_and_store_chunks(
                db, doc.id, ["سياسة الإجازات: 30 يوم إجازة سنوية"],
                version_number=1,
            )
            db.commit()

            # 2) Verify: pending not in RAG
            results = rag_service.retrieve_relevant_chunks(db, "إجازة", "hr", limit=5, user_department_ids=["hr"], user_role_ids=["reader"])
            assert all(r["document_id"] != doc.id for r in results), (
                "Pending doc should NOT be in RAG"
            )

            # 3) Approve
            doc.status = "approved"
            version = db.query(DocumentVersion).filter(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version_number == doc.version,
            ).first()
            if version:
                version.status = "approved"
            db.add(DocumentAuditLog(
                document_id=doc.id, action="approve",
                performed_by="test_user", version_number=doc.version,
            ))
            db.commit()

            # 4) Retrieve after approve
            results = rag_service.retrieve_relevant_chunks(db, "إجازة", "hr", limit=5, user_department_ids=["hr"], user_role_ids=["reader"])
            assert any(r["document_id"] == doc.id for r in results), (
                "Approved doc should be in RAG"
            )
            matched = [r for r in results if r["document_id"] == doc.id][0]
            assert matched["version_number"] == 1
            assert "إجازة" in matched["content"] or "الإجازات" in matched["content"]

            # 5) Archive
            doc.status = "archived"
            version = db.query(DocumentVersion).filter(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version_number == doc.version,
            ).first()
            if version:
                version.status = "archived"
            db.add(DocumentAuditLog(
                document_id=doc.id, action="archive",
                performed_by="test_user", version_number=doc.version,
            ))
            db.commit()

            # 6) Retrieve after archive — excluded
            results = rag_service.retrieve_relevant_chunks(db, "إجازة", "hr", limit=5, user_department_ids=["hr"], user_role_ids=["reader"])
            assert all(r["document_id"] != doc.id for r in results), (
                "Archived doc should NOT be in RAG"
            )

            # 7) Audit log has 3 actions
            logs = db.query(DocumentAuditLog).filter(
                DocumentAuditLog.document_id == doc.id
            ).all()
            actions = [log.action for log in logs]
            assert "upload" in actions
            assert "approve" in actions
            assert "archive" in actions
    finally:
        engine.dispose()


def test_revise_creates_new_version_old_archived(fresh_db):
    """Storage-only revision preserves prior chunks alongside a pending version."""
    engine = _migrate(fresh_db["url"])
    try:
        with Session(engine) as db:
            # V1 approved
            doc = Document(
                filename="v1.pdf", file_path="/tmp/v1.pdf", file_type=".pdf",
                extracted_text="v1 content", department_id="hr",
                status="approved", version=1,
            )
            db.add(doc); db.commit(); db.refresh(doc)
            db.add(DocumentVersion(document_id=doc.id, version_number=1, status="approved"))
            db.commit()
            rag_service.process_and_store_chunks(
                db, doc.id, ["v1 content " * 20], version_number=1,
            )
            db.commit()

            # Revise → V2
            doc.extracted_text = "v2 content"
            doc.version = 2
            doc.status = "pending_review"
            db.add(DocumentVersion(document_id=doc.id, version_number=2, status="pending"))
            db.commit()
            rag_service.process_and_store_chunks(
                db, doc.id, ["v2 content " * 20], version_number=2,
            )
            db.commit()

            # V1 chunks still exist
            v1_count = db.query(DocumentChunk).filter(
                DocumentChunk.document_id == doc.id,
                DocumentChunk.version_number == 1,
            ).count()
            assert v1_count > 0, "V1 chunks must NOT be deleted"

            # V2 chunks exist
            v2_count = db.query(DocumentChunk).filter(
                DocumentChunk.document_id == doc.id,
                DocumentChunk.version_number == 2,
            ).count()
            assert v2_count > 0
    finally:
        engine.dispose()


def test_cross_department_isolation(fresh_db):
    """RAG returns only requested department."""
    engine = _migrate(fresh_db["url"])
    try:
        with Session(engine) as db:
            for dept in ("hr", "finance"):
                doc = Document(
                    filename=f"{dept}.pdf", file_path=f"/tmp/{dept}.pdf",
                    file_type=".pdf", extracted_text=f"{dept} content",
                    department_id=dept, status="approved", version=1,
                )
                db.add(doc); db.commit(); db.refresh(doc)
                db.add(DocumentAudience(document_id=doc.id,department_id=dept,role_id="reader",created_by="test_user"))
                db.add(DocumentVersion(document_id=doc.id, version_number=1, status="approved"))
                db.commit()
                rag_service.process_and_store_chunks(
                    db, doc.id, [f"{dept} content " * 20], version_number=1,
                )
                db.commit()

            # HR query
            results = rag_service.retrieve_relevant_chunks(db, "content", "hr", limit=10, user_department_ids=["hr"], user_role_ids=["reader"])
            hr_doc = db.query(Document).filter(Document.department_id == "hr").one()
            assert results and all(r["document_id"] == hr_doc.id for r in results)
            # Verify: no finance doc leaked
            finance_doc = db.query(Document).filter(Document.department_id == "finance").first()
            assert all(r["document_id"] != finance_doc.id for r in results), (
                "Cross-dept leak!"
            )
    finally:
        engine.dispose()