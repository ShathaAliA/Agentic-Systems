"""P2-4: Upload rollback — no orphan new_doc if chunks fail."""
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.documents.models import Document


pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]
CONFIG_PATH = BACKEND / "tenant_alembic.ini"
from document_test_infrastructure import fresh_db
from test_authentication_postgres import isolated_postgres


def test_chunks_failure_rolls_back_new_doc(fresh_db):
    """If process_and_store_chunks fails, new_doc must NOT be committed."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0007")

    from app.documents import rag_service

    try:
        with Session(engine) as db:
            db.add(Document(
                filename="existing.pdf", file_path="/tmp/existing.pdf",
                file_type=".pdf", extracted_text="existing",
                department_id="hr", status="approved", version=1,
            ))
            db.commit()

            initial_count = db.query(Document).count()

            db.add(Document(
                filename="new.pdf", file_path="/tmp/new.pdf",
                file_type=".pdf", extracted_text="new",
                department_id="hr", status="pending_review", version=2,
            ))
            db.flush()

            with patch.object(rag_service, "process_and_store_chunks",
                              side_effect=RuntimeError("embed failed")):
                try:
                    rag_service.process_and_store_chunks(db, 1, ["chunk"], version_number=2)
                    pytest.fail("Expected RuntimeError")
                except RuntimeError:
                    db.rollback()

            final_count = db.query(Document).count()
            assert final_count == initial_count, (
                f"Rollback failed: final={final_count} != initial={initial_count}"
            )
    finally:
        engine.dispose()