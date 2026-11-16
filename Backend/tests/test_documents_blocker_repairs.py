"""Offline security regressions, no model/network/real database access."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects import postgresql
from app.auth.config import AuthSettings
from app.auth.dependencies import get_current_user, get_central_session
from app.documents.models import Base, Document, DocumentVersion, DocumentChunk, DocumentAudience
from app.documents.audience_service import user_can_access_document
from app.documents import router, source_authorization as verification, result_store
from app.main import create_app
from app.usage.service import UsageError

DEPT = str(uuid4())
CONV = str(uuid4())

@pytest.fixture
def tenant():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread":False})
    Base.metadata.create_all(engine)
    with Session(engine) as s, s.begin():
        s.add(Document(id=1, department_id=DEPT, filename="synthetic.pdf", file_path="unused",
            file_type=".pdf", extracted_text="approved synthetic content", version=1, status="approved"))
        s.add(DocumentVersion(id=7, document_id=1, version_number=1, status="approved"))
        s.add(DocumentChunk(id=9, document_id=1, version_number=1, content="approved synthetic content", page_number=2))
        s.add(DocumentAudience(document_id=1, department_id=DEPT, role_id="reader", created_by="owner"))
    yield engine
    engine.dispose()

def payload(tenant):
    with Session(tenant) as s:
        source=verification.source_for_chunk(s, DEPT, dict(document_id=1,version_number=1,
            chunk_id=9,content="approved synthetic content"))
    return dict(question_request_id=str(uuid4()), conversation_id=CONV,status="answered",
        answer="approved synthetic content", answer_kind="verbatim_excerpt", llm_called=False,
        sources=[source], unanswered_departments=[], handoff_available=True)

@pytest.fixture
def authorizer(tenant, monkeypatch):
    @contextmanager
    def open_tenant(**kw):
        assert kw == dict(user_id="owner",department_id=DEPT,permission_code="documents:read")
        with Session(tenant) as s: yield s
    monkeypatch.setattr(verification,"get_user_context", lambda *args: ([DEPT],["reader"]))
    return verification.DocumentSourceAuthorizer(tenant,open_tenant,user_id="owner",
        department_id=DEPT,conversation_id=CONV)

def test_current_source_passes_and_foreign_owner_is_denied(tenant,authorizer):
    p=payload(tenant)
    assert authorizer("owner",p) is True
    assert authorizer("other",p) is False

@pytest.mark.parametrize("field,value",[("name","forged"),("quote","secret"),("version_id","8"),
    ("department_id",str(uuid4())),("location",{"kind":"pdf_page","page_number":3})])
def test_forged_source_fields_are_denied(tenant,authorizer,field,value):
    p=payload(tenant);p["sources"][0][field]=value
    assert authorizer("owner",p) is False

@pytest.mark.parametrize("change",["archive","version","audience","delete_audience"])
def test_revocation_rechecks_every_delivery(tenant,authorizer,change):
    p=payload(tenant); assert authorizer("owner",p)
    with Session(tenant) as s,s.begin():
        if change=="archive": s.get(Document,1).status="archived"
        elif change=="version": s.get(Document,1).version=2
        elif change=="audience": s.scalar(select(DocumentAudience)).role_id="denied"
        else: s.delete(s.scalar(select(DocumentAudience)))
    if change in ("archive","version"):
        with pytest.raises(UsageError,match="revoked"): authorizer("owner",p)
    else: assert authorizer("owner",p) is False

def test_no_answer_still_checks_identity_scope(tenant,authorizer,monkeypatch):
    p=payload(tenant);p.update(status="no_answer",sources=[],answer_kind="system_notice")
    monkeypatch.setattr(verification,"get_user_context", lambda *args: ([],[]))
    assert authorizer("owner",p) is False

def test_no_audience_denies_and_explicit_all_all_allows(tenant):
    with Session(tenant) as s,s.begin():
        s.query(DocumentAudience).delete()
        assert not user_can_access_document(s,1,[DEPT],["reader"])
        s.add(DocumentAudience(document_id=1,department_id=None,role_id=None,created_by="owner"))
        s.flush()
        assert user_can_access_document(s,1,[DEPT],["reader"])
        assert not user_can_access_document(s,1,[],["reader"])

def test_audience_is_in_sql_before_limit(monkeypatch):
    from app.documents import rag_service
    captured=[]
    class Query:
        def scalars(self): return self
        def all(self): return []
    class DB:
        def execute(self,stmt): captured.append(stmt);return Query()
    monkeypatch.setattr(rag_service,"get_embedding_model",lambda: SimpleNamespace(
        encode=lambda _:SimpleNamespace(tolist=lambda:[0.0]*768)))
    rag_service.retrieve_relevant_chunks(DB(),"q",DEPT,user_department_ids=[DEPT],user_role_ids=["reader"])
    sql=str(captured[0].compile(dialect=postgresql.dialect()))
    assert sql.index("EXISTS") < sql.index("ORDER BY") < sql.index("LIMIT")
    assert "document_audiences.role_id" in sql and "document_audiences.department_id" in sql

def test_legacy_result_helpers_cannot_write_or_deliver():
    for operation in (result_store.save,result_store.load):
        with pytest.raises(UsageError,match="disabled"): operation()

@pytest.fixture
def http(tenant,monkeypatch):
    app=create_app(central_engine=tenant,auth_settings=AuthSettings(mode="disabled",app_env="test"))
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id="owner")
    def session():
        with Session(tenant) as s: yield s
    app.dependency_overrides[get_central_session]=session
    monkeypatch.setattr(router,"has_permission",lambda *args:True)
    @contextmanager
    def open_tenant(**kw):
        with Session(tenant) as s: yield s
    monkeypatch.setattr(router,"_open_department_session",open_tenant)
    with TestClient(app) as c: yield c

def test_missing_store_blocks_before_provider_and_accounting(http,monkeypatch):
    def forbidden(*a,**kw): raise AssertionError("Provider/accounting reached")
    monkeypatch.setattr(router.provider_accounting.Settings,"from_environment",forbidden)
    r=http.post("/api/documents/ask",json=dict(question="q",department_id=DEPT,
        conversation_id=CONV,request_key="one"))
    assert r.status_code==503 and r.headers["cache-control"]=="no-store"

def test_publish_requires_explicit_audience_and_expected_version(http,tenant):
    url=f"/api/documents/1/approve?department_id={DEPT}"
    assert http.post(url,json={"expected_version":1,"audiences":[]}).status_code==422
    assert http.post(url,json={"expected_version":2,"audiences":[{"department_id":None,"role_id":None}]}).status_code==409
    with Session(tenant) as s: assert s.get(Document,1).status=="approved"

def test_publish_then_archive_preserves_rows_and_denies_stale_version(http,tenant):
    with Session(tenant) as s,s.begin(): s.get(Document,1).status="pending_review"
    url=f"/api/documents/1/approve?department_id={DEPT}"
    r=http.post(url,json={"expected_version":1,"audiences":[{"department_id":None,"role_id":None}]})
    assert r.status_code==200,r.text
    url=f"/api/documents/1/archive?department_id={DEPT}"
    assert http.post(url,json={"expected_version":2}).status_code==409
    assert http.post(url,json={"expected_version":1}).status_code==200
    with Session(tenant) as s:
        assert s.get(Document,1).status=="archived"
        assert s.get(DocumentVersion,7).status=="archived"
        assert s.get(DocumentChunk,9) is not None


def test_metadata_and_audience_commands_require_json_and_version(http,tenant):
    assert http.patch(f"/api/documents/1?department_id={DEPT}&filename=ignored").status_code==422
    r=http.patch(f"/api/documents/1?department_id={DEPT}",json={"expected_version":2,"filename":"changed"})
    assert r.status_code==409
    r=http.post(f"/api/documents/1/permissions?department_id={DEPT}",json={
        "expected_version":2,"department_id":None,"role_id":None})
    assert r.status_code==409
    with Session(tenant) as s:
        assert s.get(Document,1).filename=="synthetic.pdf"
        assert s.query(DocumentAudience).count()==1


def test_reviewer_permission_denied_before_tenant_session(http,monkeypatch):
    monkeypatch.setattr(router,"has_permission",lambda *args:False)
    def forbidden(**kw): raise AssertionError("Unauthorized tenant opened")
    monkeypatch.setattr(router,"_open_department_session",forbidden)
    r=http.post(f"/api/documents/1/approve?department_id={DEPT}",json={
        "expected_version":1,"audiences":[{"department_id":None,"role_id":None}]})
    assert r.status_code==403
