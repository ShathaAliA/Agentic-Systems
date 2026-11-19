"""Fresh PostgreSQL-only compatibility and locking evidence; no pgvector required."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from alembic import command
from alembic.config import Config
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.main import create_app
from app.auth.config import AuthSettings
from app.auth.dependencies import get_current_user, get_central_session
from app.documents import router
from app.documents.models import Document, DocumentVersion, DocumentAudience, DocumentAuditLog
from document_test_infrastructure import fresh_db
from test_authentication_postgres import isolated_postgres
pytestmark=pytest.mark.integration
BACKEND=Path(__file__).parents[1]


def test_existing_legacy_history_upgrade_preserves_payload_and_fences_new_writes(fresh_db,monkeypatch):
    import os
    report=Path(os.environ["AUTH_TEST_REPORT_DIR"])
    url_file=report/(fresh_db["db_name"]+".json")
    url_file.write_text(json.dumps({"url":fresh_db["url"].render_as_string(hide_password=False)}))
    monkeypatch.setenv("CENTRAL_MIGRATION_DATABASE_URL_FILE",str(url_file))
    cfg=Config(str(BACKEND/"alembic.ini"))
    command.upgrade(cfg,"usage003_merge")
    engine=create_engine(fresh_db["url"],hide_parameters=True)
    with engine.begin() as c:
        c.execute(text("INSERT INTO users(id,name,email,status,created_at) VALUES ('synthetic','Synthetic','synthetic@example.invalid','active',CURRENT_TIMESTAMP)"))
        c.execute(text("""INSERT INTO usage_requests(id,user_id,request_key,fingerprint,policy_version,timezone,
            question_month,question_day,state,estimated_micro_usd,held_micro_usd,created_at,result_payload)
            VALUES ('legacy','synthetic','legacy','fingerprint',1,'UTC','2026-01','2026-01-01','reserved',0,0,CURRENT_TIMESTAMP,'{"audit":"retained"}')"""))
    command.upgrade(cfg,"head")
    command.upgrade(cfg,"head")
    with engine.begin() as c:
        assert c.scalar(text("SELECT result_payload->>'audit' FROM usage_requests WHERE id='legacy'"))=='retained'
        # Legitimate accounting updates remain possible on old rows.
        c.execute(text("UPDATE usage_requests SET state='running' WHERE id='legacy'"))
        assert c.scalar(text("SELECT count(*) FROM permissions WHERE code='documents:archive'"))==1
        assert c.scalar(text("SELECT count(*) FROM role_permissions"))==0
    for sql in ["UPDATE usage_requests SET result_payload='{}' WHERE id='legacy'",
                """INSERT INTO usage_requests(id,user_id,request_key,fingerprint,policy_version,timezone,
                question_month,question_day,state,estimated_micro_usd,held_micro_usd,created_at,result_payload)
                VALUES ('new','synthetic','new','fingerprint',1,'UTC','2026-01','2026-01-01','reserved',0,0,CURRENT_TIMESTAMP,'{}')"""]:
        with engine.begin() as c,pytest.raises(DBAPIError) as err: c.execute(text(sql))
        assert err.value.orig.sqlstate=='23514'
    engine.dispose()


def test_concurrent_publish_locks_version_and_archive_preserves_rows(fresh_db,monkeypatch):
    engine=create_engine(fresh_db["url"],hide_parameters=True)
    with engine.begin() as c:
        for table in (Document.__table__,DocumentVersion.__table__,DocumentAudience.__table__,DocumentAuditLog.__table__):
            table.create(c)
    dept=str(uuid4())
    with Session(engine) as s,s.begin():
        s.add(Document(id=1,filename='synthetic.pdf',file_path='unused',department_id=dept,status='pending_review',version=1))
        s.add(DocumentVersion(id=1,document_id=1,version_number=1,status='pending'))
    app=create_app(central_engine=engine,auth_settings=AuthSettings(mode='disabled',app_env='test'))
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id='synthetic')
    def central():
        with Session(engine) as s: yield s
    app.dependency_overrides[get_central_session]=central
    @contextmanager
    def tenant(**kw):
        assert kw['department_id']==dept
        with Session(engine) as s: yield s
    monkeypatch.setattr(router,'_open_department_session',tenant)
    monkeypatch.setattr(router,'has_permission',lambda *args:True)
    body=dict(expected_version=1,audiences=[dict(department_id=None,role_id=None)])
    def publish(_):
        with TestClient(app) as client:
            return client.post(f'/api/documents/1/approve?department_id={dept}',json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(publish,range(2)))
    assert sorted(results)==[200,409]
    with TestClient(app) as client:
        assert client.post(f'/api/documents/1/archive?department_id={dept}',json={'expected_version':2}).status_code==409
        assert client.post(f'/api/documents/1/archive?department_id={dept}',json={'expected_version':1}).status_code==200
    with Session(engine) as s:
        assert s.get(Document,1).status=='archived'
        assert s.get(DocumentVersion,1).status=='archived'
        assert s.query(DocumentAudience).count()==1
        assert s.query(DocumentAuditLog).count()==2
    # Different pending documents with the same logical name also serialize.
    with Session(engine) as s,s.begin():
        for i in (2,3):
            s.add(Document(id=i,filename='next.pdf',file_path='unused',department_id=dept,status='pending_review',version=i))
            s.add(DocumentVersion(id=i,document_id=i,version_number=i,status='pending'))
    def publish_other(i):
        with TestClient(app) as client:
            return client.post(f'/api/documents/{i}/approve?department_id={dept}',
                json=dict(expected_version=i,audiences=[dict(department_id=None,role_id=None)])).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(publish_other,(2,3)))==[200,200]
    with Session(engine) as s:
        assert s.query(Document).filter_by(filename='next.pdf',status='approved').count()==1
        assert s.query(Document).filter_by(filename='next.pdf',status='archived').count()==1
    engine.dispose()
