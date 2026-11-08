"""PR 2 — Document audiences tests.

Verifies:
1. `tenant_0003` creates `document_audiences`.
2. Audience filtering respects (dept, role) matching.
3. Wildcards: NULL dept = all depts, NULL role = all roles.
"""
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.documents.models import Document, DocumentAudience
from app.documents.audience_service import (
    user_can_access_document,
    filter_visible_documents,
)


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


def test_tenant_0003_creates_audiences_table(fresh_db):
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0003")
    with engine.connect() as conn:
        assert conn.scalar(text(
            "SELECT to_regclass('public.document_audiences')"
        )) is not None
        assert conn.scalar(text(
            "SELECT version_num FROM alembic_version"
        )) == "tenant_0003"
    engine.dispose()


def _make_doc(db, dept_id="hr"):
    doc = Document(
        filename="test.pdf",
        file_path="/tmp/test.pdf",
        file_type=".pdf",
        extracted_text="",
        department_id=dept_id,
        status="approved",
        version=1,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_no_audience_allows_dept_scope(fresh_db):
    """A document with NO audience rows is visible within its department (legacy)."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0003")

    with Session(engine) as db:
        doc = _make_doc(db)
        assert user_can_access_document(db, doc.id, ["hr"], ["employee"]) is True
        assert user_can_access_document(db, doc.id, [], []) is False
    engine.dispose()


def test_specific_dept_and_role_required(fresh_db):
    """(dept='hr', role='manager') → only hr+manager."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0003")

    with Session(engine) as db:
        doc = _make_doc(db)
        db.add(DocumentAudience(
            document_id=doc.id,
            department_id="hr",
            role_id="manager",
            created_by="test",
        ))
        db.commit()

        assert user_can_access_document(db, doc.id, ["hr"], ["manager"]) is True
        assert user_can_access_document(db, doc.id, ["hr"], ["employee"]) is False
        assert user_can_access_document(db, doc.id, ["finance"], ["manager"]) is False
    engine.dispose()


def test_all_departments_wildcard(fresh_db):
    """(dept=NULL, role='manager') → managers in ANY department."""
    engine = create_engine(fresh_db["url"], hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0003")

    with Session(engine) as db:
        doc = _make_doc(db)
        db.add(DocumentAudience(
            document_id=doc.id,
            department_id=None,
            role_id="manager",
            created_by="test",
        ))
        db.commit()

        assert user_can_access_document(db, doc.id, ["hr"], ["manager"]) is True
        assert user_can_access_document(db, doc.id, ["finance"], ["manager"]) is True
        assert user_can_access_document(db, doc.id, ["hr"], ["employee"]) is False
    engine.dispose()