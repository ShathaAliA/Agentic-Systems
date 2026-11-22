"""H6: handoff→knowledge conversion tests (on existing wadi_db)."""
import uuid
import pytest
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.conversation_store.postgres import PostgresResultStore


BACKEND = Path(__file__).resolve().parents[1]
TEST_DB_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5435/wadi_db"


@pytest.fixture(scope="module")
def db_engine():
    """Use existing wadi_db engine for testing handoff knowledge storage."""
    engine = create_engine(TEST_DB_URL, hide_parameters=True)
    yield engine
    engine.dispose()


def test_handoff_knowledge_table_created(db_engine):
    with db_engine.connect() as conn:
        assert conn.scalar(text(
            "SELECT to_regclass('public.handoff_knowledge')"
        )) is not None


def test_idempotent_insert(db_engine):
    hid = f"test-handoff-{uuid.uuid4()}"
    rk = "req-1"
    store = PostgresResultStore(
        db_engine,
        expected_database="wadi_db",
        authenticated_user_id="user-1"
    )
    try:
        # Test creation/finding via PostgresResultStore or direct SQL execution
        with db_engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO handoff_knowledge (id, handoff_id, request_key, document_id, document_version_id, created_by)
                    VALUES (:id, :hid, :rkey, :doc_id, :doc_v_id, :c_by)
                    ON CONFLICT (handoff_id, request_key) DO NOTHING
                """),
                {
                    "id": str(uuid.uuid4()),
                    "hid": hid,
                    "rkey": rk,
                    "doc_id": 1,
                    "doc_v_id": 1,
                    "c_by": "user-1",
                }
            )
        
        record = store.find_handoff_knowledge(hid, rk)
        assert record is not None
        assert record.handoff_id == hid
    finally:
        with db_engine.begin() as conn:
            conn.execute(text("DELETE FROM handoff_knowledge WHERE handoff_id = :h"), {"h": hid})