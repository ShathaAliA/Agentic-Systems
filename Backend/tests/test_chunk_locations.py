"""PR 5 — Chunk locations: each chunk attributed to its real page."""
import uuid
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


def test_chunks_preserve_page_numbers(fresh_db):
    """With pages input, chunks are attributed to their real page."""
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

        # Simulate 3 pages with distinct content
        pages = [
            {"page_number": 1, "text": "صفحة الأولى " * 20},
            {"page_number": 2, "text": "صفحة الثانية " * 20},
            {"page_number": 3, "text": "صفحة الثالثة " * 20},
        ]

        rag_service.process_and_store_chunks(
            db, doc.id, [], version_number=1, pages=pages
        )

        chunks = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id
        ).all()

        assert len(chunks) > 0
        page_numbers = {c.page_number for c in chunks}
        assert page_numbers == {1, 2, 3}, f"Expected pages 1,2,3 got {page_numbers}"

    engine.dispose()


def test_legacy_fallback_to_page_1(fresh_db):
    """Without pages, all chunks attributed to page 1 (legacy)."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0004")

    with Session(engine) as db:
        doc = Document(
            filename="legacy.pdf",
            file_path="/tmp/legacy.pdf",
            file_type=".pdf",
            extracted_text="",
            department_id="hr",
            status="approved",
            version=1,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        chunks = ["chunk a " * 40, "chunk b " * 40]
        rag_service.process_and_store_chunks(
            db, doc.id, chunks, version_number=1, pages=None
        )

        stored = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == doc.id
        ).all()
        assert len(stored) >= 2
        for c in stored:
            assert c.page_number == 1

    engine.dispose()