"""PR 10 — Extraction quality: locations + version preservation.

Verifies that extraction preserves real locations (pages/slides/sheets)
and that version replacement does not corrupt prior chunks.
"""
import uuid
import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.documents.models import Document, DocumentChunk
from app.documents import rag_service


pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]
CONFIG_PATH = BACKEND / "tenant_alembic.ini"
ADMIN_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5435/postgres"


@pytest.fixture(scope="module")
def fresh_db():
    hex_ = uuid.uuid4().hex
    db_name = f"dept_{hex_}"

    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        c.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin.dispose()

    url = f"postgresql+psycopg://postgres:postgres@127.0.0.1:5435/{db_name}"
    yield {"db_name": db_name, "url": url}

    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    admin.dispose()


def test_chunk_text_preserves_arabic(fresh_db):
    """Arabic characters must survive chunking without corruption."""
    text = "مرحباً بكم في شركة وادي مكة. نقدم خدمات الحج والعمرة. " * 5
    chunks = rag_service.chunk_text(text, chunk_size=100, overlap=10)
    assert len(chunks) > 0
    joined = "".join(chunks)
    assert "وادي مكة" in joined
    assert "الحج والعمرة" in joined


def test_chunk_text_preserves_numbers(fresh_db):
    """Numbers (e.g., financial data) must not be lost or normalized."""
    text = "الراتب الأساسي: 15000 ريال. البدل: 2500 ريال. الإجمالي: 17500 ريال."
    chunks = rag_service.chunk_text(text, chunk_size=200, overlap=0)
    joined = "".join(chunks)
    assert "15000" in joined
    assert "2500" in joined
    assert "17500" in joined


def test_real_locations_from_pages(fresh_db):
    """Chunks preserve real pages/slides/sheets from extraction."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0004")

    with Session(engine) as db:
        doc = Document(
            filename="multi.pdf",
            file_path="/tmp/multi.pdf",
            file_type=".pdf",
            extracted_text="",
            department_id="hr",
            status="approved",
            version=1,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        pages = [
            {"page_number": 1, "text": "الصفحة الأولى " * 30},
            {"page_number": 2, "text": "الصفحة الثانية " * 30},
            {"page_number": 3, "text": "الصفحة الثالثة " * 30},
        ]
        rag_service.process_and_store_chunks(db, doc.id, [], version_number=1, pages=pages)

        chunks = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id
        ).all()
        page_nums = {c.page_number for c in chunks}
        assert page_nums == {1, 2, 3}

    engine.dispose()


def test_old_version_chunks_survive_replacement(fresh_db):
    """Replacing a document must not delete old chunks (immutable history)."""
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
            extracted_text="old content " * 50,
            department_id="hr",
            status="approved",
            version=1,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        # V1 chunks
        rag_service.process_and_store_chunks(
            db, doc.id,
            ["v1 chunk a " * 40, "v1 chunk b " * 40],
            version_number=1,
            pages=None,
        )

        v1_before = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id,
            DocumentChunk.version_number == 1,
        ).count()
        assert v1_before >= 2

        # Simulate replace → new version
        doc.version = 2
        db.commit()
        rag_service.process_and_store_chunks(
            db, doc.id,
            ["v2 chunk a " * 40, "v2 chunk b " * 40],
            version_number=2,
            pages=None,
        )

        v1_after = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id,
            DocumentChunk.version_number == 1,
        ).count()
        v2_count = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id,
            DocumentChunk.version_number == 2,
        ).count()

        assert v1_after == v1_before, "Old version chunks must NOT be deleted"
        assert v2_count >= 2

    engine.dispose()