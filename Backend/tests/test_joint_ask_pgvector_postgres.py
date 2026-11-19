"""Real joint /ask gate: Documents on a real PostgreSQL tenant (migrations, restricted runtime role,
real PDF upload, review, approval, pgvector retrieval) + Core's real lifecycle + a real C06 store database,
all inside one fresh isolated cluster. Only the LLM call is stubbed and embeddings are deterministic
vectors (no model download, no provider traffic); answer quality and embedding quality are NOT measured.
"""
from contextlib import contextmanager
from unittest.mock import Mock
from uuid import uuid4

import pymupdf
import pytest
from sqlalchemy import select, func, text, update
from sqlalchemy.orm import Session

from app.conversation_store.authorization import ConversationResultStoreFactory
from app.conversation_store.conversations import ConversationService
from app.conversation_store.postgres import PostgresResultStore
from app.documents import provider_accounting as billing, router
from app.documents.models import Document, DocumentAudience, QuestionResult
from app.documents.source_provider import DocumentSourceAuthorizerProvider
from app.models import Department, DepartmentMembership, Permission, Role, RoleAssignment, RolePermission
from app.runtime_security import restrict_runtime_engine
from app.services.authorization import has_permission
from app.usage.models import UsageEntry
from app.usage.processor import ProviderReceipt
from document_test_infrastructure import fresh_db
from joint_real_infrastructure import c06_store_database, provision_tenant_runtime
from sqlalchemy import create_engine
from test_authentication_postgres import isolated_postgres
from test_usage import usage, configure

pytestmark = pytest.mark.integration

DEPT = str(uuid4())
ANSWER = "PRIVATE_REAL_JOINT_ANSWER"
CORRECTED = "سياسة اصطناعية: الإجازة السنوية ثلاثون يوماً للموظفين المستحقين."


@pytest.fixture
def real(fresh_db, usage, c06_store_database, tmp_path, monkeypatch):
    central, client, headers = usage
    configure(central)
    with Session(central) as session, session.begin():
        for code in ("documents:upload", "documents:update", "documents:archive"):
            session.add(Permission(id=code, code=code))
            session.flush()
            session.add(RolePermission(role_id="administrator", permission_id=code))
        session.execute(update(Department).where(Department.id == "hr").values(id=DEPT))
        session.execute(update(DepartmentMembership).where(DepartmentMembership.department_id == "hr").values(department_id=DEPT))
        session.execute(update(RoleAssignment).where(RoleAssignment.department_id == "hr").values(department_id=DEPT))
    owner, runtime_url = provision_tenant_runtime(fresh_db)
    tenant_runtime = restrict_runtime_engine(create_engine(runtime_url, hide_parameters=True))
    c06_runtime, c06_name = c06_store_database

    @contextmanager
    def tenant(user_id, department_id, permission_code):
        with Session(central) as s:
            assert has_permission(s, user_id, permission_code, department_id)
        assert department_id == DEPT
        with Session(tenant_runtime) as s:
            yield s

    def c06(user):
        return PostgresResultStore(c06_runtime, expected_database=c06_name, authenticated_user_id=user)

    monkeypatch.setattr(router, "_open_department_session", tenant)
    monkeypatch.setattr(router, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(billing.Settings, "from_environment", classmethod(lambda cls: cls("fake", "v1", 1000, 1000)))
    generate = Mock(return_value=(ANSWER, ProviderReceipt("verified-real-joint-receipt", 1, 10, 5)))
    monkeypatch.setattr(billing, "generate", generate)
    provider = DocumentSourceAuthorizerProvider(central, tenant)
    factory = ConversationResultStoreFactory(c06, central, provider)
    client.app.state.document_result_store_factory = factory
    client.app.state.conversation_store_factory = factory.metadata_factory
    client.app.state.source_authorizer_provider = provider
    try:
        yield dict(central=central, client=client, headers=headers, owner=owner, runtime=tenant_runtime,
                   generate=generate, factory=factory, c06=c06)
    finally:
        tenant_runtime.dispose()
        owner.dispose()


def publish(real, audiences=None):
    """Upload a real PDF, correct its text and publish it through the real HTTP lifecycle."""
    client, admin = real["client"], real["headers"]["admin"]
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((50, 70), "SYNTHETIC POLICY: Annual leave is 30 days for eligible employees.")
    original = pdf.tobytes()
    pdf.close()
    uploaded = client.post("/api/documents/upload", headers=admin, data={"department_id": DEPT},
                           files={"file": ("synthetic.pdf", original, "application/pdf")})
    assert uploaded.status_code == 200, uploaded.text
    identity = uploaded.json()["document_id"]
    revised = client.post(f"/api/documents/{identity}/revise?department_id={DEPT}", headers=admin,
                          json={"expected_version": 1, "text": CORRECTED})
    assert revised.status_code == 200, revised.text
    approved = client.post(f"/api/documents/{identity}/approve?department_id={DEPT}", headers=admin, json={
        "expected_version": 2, "audiences": audiences or [{"department_id": None, "role_id": None}]})
    assert approved.status_code == 200, approved.text
    return identity


def conversation(real):
    service = ConversationService(real["c06"]("employee"), real["central"])
    return service.create("department", [DEPT], "conv-" + uuid4().hex)["conversation_id"]


def ask(real, conversation_id, key, question="كم مدة الإجازة السنوية؟"):
    return real["client"].post("/api/documents/ask", headers=real["headers"]["employee"], json={
        "question": question, "department_id": DEPT, "conversation_id": conversation_id, "request_key": key})


def history(real, conversation_id):
    return ConversationService(real["factory"].metadata_for("employee"), real["central"]).messages(conversation_id)["items"]


def debit(real):
    with Session(real["central"]) as s:
        return s.scalar(select(func.coalesce(func.sum(UsageEntry.question_delta), 0)))


def test_real_document_lifecycle_feeds_a_charged_once_linked_answer(real):
    identity = publish(real)
    conv, key = conversation(real), "ask-" + uuid4().hex
    first = ask(real, conv, key)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["answer"] == ANSWER and body["status"] == "answered" and body["conversation_id"] == conv
    source = body["sources"][0]
    assert source["document_id"] == identity and source["version_number"] == 2 and source["quote"] == CORRECTED
    assert source["location"]["kind"] == "reviewed_text"
    items = history(real, conv)
    assert len(items) == 1 and items[0]["content_status"] == "available" and items[0]["response"]["answer"] == ANSWER
    replay = ask(real, conv, key)
    assert replay.status_code == 200 and replay.json() == body
    assert real["generate"].call_count == 1 and debit(real) == -1
    with Session(real["owner"]) as s:
        assert s.scalar(select(func.count()).select_from(QuestionResult)) == 0  # frozen legacy table is never used


def test_archiving_the_document_denies_replay_and_masks_history(real):
    identity = publish(real)
    conv, key = conversation(real), "ask-" + uuid4().hex
    assert ask(real, conv, key).status_code == 200
    archived = real["client"].post(f"/api/documents/{identity}/archive?department_id={DEPT}",
                                   headers=real["headers"]["admin"], json={"expected_version": 2})
    assert archived.status_code == 200, archived.text
    denied = ask(real, conv, key)
    assert denied.status_code == 403 and ANSWER not in denied.text
    item = history(real, conv)[0]
    assert item["content_status"] == "access_revoked" and item["response"] is None
    assert real["generate"].call_count == 1


def test_revoked_audience_denies_replay_and_a_new_question_gets_no_answer_without_the_llm(real):
    publish(real)
    conv, first_key = conversation(real), "ask-" + uuid4().hex
    assert ask(real, conv, first_key).status_code == 200
    with Session(real["owner"]) as s, s.begin():
        for audience in s.scalars(select(DocumentAudience)):
            audience.department_id, audience.role_id = None, "denied-role"
    assert ask(real, conv, first_key).status_code == 403
    fresh = ask(real, conv, "ask-" + uuid4().hex)
    assert fresh.status_code == 200 and fresh.json()["status"] == "no_answer" and fresh.json()["sources"] == []
    assert real["generate"].call_count == 1 and debit(real) == -2  # the refusal is still one counted question
    assert [item["content_status"] for item in history(real, conv)] == ["access_revoked", "available"]


def test_document_restricted_to_another_role_yields_no_answer_without_leaking_it(real):
    # The publication API needs a UUID role id that exists in Central; the employee does not hold it.
    other_role = str(uuid4())
    with Session(real["central"]) as session, session.begin():
        session.add(Role(id=other_role, name="Synthetic other role", scope="department"))
    publish(real, audiences=[{"department_id": None, "role_id": other_role}])
    conv = conversation(real)
    response = ask(real, conv, "ask-" + uuid4().hex)
    assert response.status_code == 200 and response.json()["status"] == "no_answer"
    assert CORRECTED not in response.text and ANSWER not in response.text
    assert real["generate"].call_count == 0


def test_provider_failure_leaves_the_request_uncertain_without_redispatch(real):
    publish(real)
    real["generate"].side_effect = RuntimeError("PRIVATE_PROVIDER_ERROR host=db.internal")
    conv, key = conversation(real), "ask-" + uuid4().hex
    first = ask(real, conv, key)
    assert first.status_code == 409 and "PRIVATE_PROVIDER_ERROR" not in first.text
    assert ask(real, conv, key).status_code == 409 and real["generate"].call_count == 1
    assert history(real, conv) == []


def test_runtime_roles_cannot_touch_the_frozen_legacy_table_and_c06_holds_no_document_text(real):
    publish(real)
    conv = conversation(real)
    assert ask(real, conv, "ask-" + uuid4().hex).status_code == 200
    with Session(real["runtime"]) as s:
        assert s.scalar(text("SELECT has_table_privilege(current_user,'public.question_results','INSERT')")) is False
        assert s.scalar(text("SELECT has_table_privilege(current_user,'public.question_results','UPDATE')")) is False
    with Session(real["owner"]) as s:
        assert s.scalar(select(func.count()).select_from(Document)) == 1
    store = real["c06"]("employee")
    with store.connection() as c:
        columns = set(c.scalars(text("SELECT column_name FROM information_schema.columns WHERE table_name IN ('c06_turns','c06_question_intents')")))
        assert not {"embedding", "extracted_text", "file_path"} & columns
