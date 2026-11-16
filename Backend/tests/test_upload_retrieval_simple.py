"""P2-5: Simple upload + retrieval tests (no external fixture)."""
import uuid
import pytest
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.documents.models import Document, DocumentVersion, DocumentAudience
from app.documents import rag_service


pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]
CONFIG_PATH = BACKEND / "tenant_alembic.ini"
from document_test_infrastructure import fresh_db
from test_authentication_postgres import isolated_postgres


def _migrate(url):
    engine = create_engine(url, hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0007")
    return engine


def test_upload_then_retrieve(fresh_db):
    """Upload a doc (approved) + chunks → retrieve returns it."""
    engine = _migrate(fresh_db["url"])
    try:
        with Session(engine) as db:
            doc = Document(
                filename="policy.pdf", file_path="/tmp/policy.pdf",
                file_type=".pdf", extracted_text="سياسة الإجازات: 30 يوم إجازة سنوية",
                department_id="hr", status="approved", version=1,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)

            db.add(DocumentAudience(document_id=doc.id, department_id="hr", role_id="reader", created_by="owner"))
            db.add(DocumentVersion(document_id=doc.id, version_number=1, status="approved"))
            db.commit()

            rag_service.process_and_store_chunks(
                db, doc.id, ["سياسة الإجازات: 30 يوم إجازة سنوية"],
                version_number=1, pages=None,
            )
            db.commit()

            results = rag_service.retrieve_relevant_chunks(db, "إجازة", "hr", limit=3, user_department_ids=["hr"], user_role_ids=["reader"])
            assert len(results) >= 1, f"No chunks retrieved (results={results})"
            assert results[0]["document_id"] == doc.id
            assert results[0]["version_number"] == 1
    finally:
        engine.dispose()


def test_retrieval_excludes_non_approved(fresh_db):
    """Retrieval excludes pending_review / archived."""
    engine = _migrate(fresh_db["url"])
    try:
        with Session(engine) as db:
            # Approved doc FIRST (content unique)
            approved = Document(
                filename="approved.pdf", file_path="/tmp/a.pdf",
                file_type=".pdf", extracted_text="APPROVED_UNIQUE_CONTENT_XYZ",
                department_id="hr", status="approved", version=1,
            )
            db.add(approved); db.commit(); db.refresh(approved)
            db.add(DocumentAudience(document_id=approved.id, department_id="hr", role_id="reader", created_by="owner"))
            db.add(DocumentVersion(document_id=approved.id, version_number=1, status="approved"))
            db.commit()
            rag_service.process_and_store_chunks(
                db, approved.id, ["APPROVED_UNIQUE_CONTENT_XYZ_" + "x" * 30],
                version_number=1,
            )
            db.commit()

            # Pending doc SECOND (content unique)
            pending = Document(
                filename="pending.pdf", file_path="/tmp/p.pdf",
                file_type=".pdf", extracted_text="PENDING_UNIQUE_CONTENT_ABC",
                department_id="hr", status="pending_review", version=1,
            )
            db.add(pending); db.commit(); db.refresh(pending)
            db.add(DocumentVersion(document_id=pending.id, version_number=1, status="pending"))
            db.commit()
            rag_service.process_and_store_chunks(
                db, pending.id, ["PENDING_UNIQUE_CONTENT_ABC_" + "y" * 30],
                version_number=1,
            )
            db.commit()

            # Retrieve — approved must be found, pending excluded
            results = rag_service.retrieve_relevant_chunks(db, "APPROVED_UNIQUE_CONTENT", "hr", limit=10, user_department_ids=["hr"], user_role_ids=["reader"])
            doc_ids = [r["document_id"] for r in results]
            assert approved.id in doc_ids, f"Approved missing: doc_ids={doc_ids}, approved.id={approved.id}"
            assert pending.id not in doc_ids, f"Pending leaked: doc_ids={doc_ids}, pending.id={pending.id}"
    finally:
        engine.dispose()