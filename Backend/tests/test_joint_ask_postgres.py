"""Joint gate: Documents' /ask + Documents' source authorizer + Core's real lifecycle and C06 store.

New cluster only. The tenant side is a synthetic SQLite document store (retrieval and the LLM are
stubbed, no real provider); the C06 store is a real PostgreSQL database. Tenant PostgreSQL/pgvector
behaviour is covered by the existing Documents suites. Counts are per conversation because the C06
store is module-scoped and immutable.
"""
from contextlib import contextmanager
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.conversation_store.authorization import ConversationResultStoreFactory
from app.conversation_store.conversations import ConversationService
from app.documents import provider_accounting as billing, router
from app.documents.models import (Base as TenantBase, Document, DocumentAudience, DocumentChunk,
                                  DocumentVersion, QuestionResult)
from app.documents.source_provider import DocumentSourceAuthorizerProvider
from app.usage.processor import ProviderReceipt
from c06_store_fixtures import DEPARTMENT
from test_c06_conversations_postgres import context
from test_c06_lifecycle_postgres import count, debit
from test_c06_result_store_postgres import engines, store, pytestmark
from test_usage import usage

EVIDENCE = "synthetic authorized evidence"
ANSWER = "PRIVATE_JOINT_ANSWER"


@pytest.fixture
def joint(context, monkeypatch):
    service, central, client, headers, runtime = context
    tenant = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TenantBase.metadata.create_all(tenant)
    with Session(tenant) as session, session.begin():
        session.add(Document(id=1, filename="synthetic.pdf", file_path="unused", department_id=DEPARTMENT,
                             version=1, status="approved"))
        session.add(DocumentVersion(id=1, document_id=1, version_number=1, status="approved"))
        session.add(DocumentChunk(id=1, document_id=1, version_number=1, content=EVIDENCE, page_number=1))
        session.add(DocumentAudience(document_id=1, department_id=DEPARTMENT, role_id="reader", created_by="admin"))

    @contextmanager
    def open_tenant(user_id, department_id, permission=None, permission_code=None):
        assert department_id == DEPARTMENT
        with Session(tenant) as session:
            yield session
    monkeypatch.setattr(router, "_open_department_session", open_tenant)
    monkeypatch.setattr(router.rag_service, "retrieve_relevant_chunks", lambda *_, **kwargs: [{
        "document_id": 1, "document_name": "synthetic.pdf", "chunk_id": 1,
        "version_number": 1, "page_number": 1, "content": EVIDENCE}])
    monkeypatch.setattr(billing.Settings, "from_environment", classmethod(lambda cls: cls("fake", "v1", 1000, 1000)))
    generate = Mock(return_value=(ANSWER, ProviderReceipt("verified-joint-receipt", 1, 10, 5)))
    monkeypatch.setattr(billing, "generate", generate)
    provider = DocumentSourceAuthorizerProvider(central, open_tenant)
    factory = ConversationResultStoreFactory(lambda user: store(runtime, user=user), central, provider)
    # The same composition main.py performs: one factory, three-argument shim plus raw metadata factory.
    client.app.state.document_result_store_factory = factory
    client.app.state.conversation_store_factory = factory.metadata_factory
    client.app.state.source_authorizer_provider = provider
    try:
        yield service, central, client, headers, runtime, tenant, generate, factory
    finally:
        tenant.dispose()


def new_conversation(service):
    return service.create("department", [DEPARTMENT], "conv-" + uuid4().hex)["conversation_id"]


def ask(client, headers, conversation, key, question="ما مدة حفظ السجلات؟", department=DEPARTMENT):
    return client.post("/api/documents/ask", headers=headers["employee"], json={
        "question": question, "department_id": department, "conversation_id": conversation, "request_key": key})


def history(factory, central, conversation):
    return ConversationService(factory.metadata_for("employee"), central).messages(conversation)["items"]


def test_answer_is_charged_once_linked_to_the_conversation_and_visible_in_history(joint):
    service, central, client, headers, _, tenant, generate, factory = joint
    conversation, key = new_conversation(service), "ask-" + uuid4().hex
    first = ask(client, headers, conversation, key)
    assert first.status_code == 200, first.text
    assert first.json()["answer"] == ANSWER and first.json()["conversation_id"] == conversation
    assert first.headers["cache-control"] == "no-store"
    items = history(factory, central, conversation)
    assert len(items) == 1 and items[0]["content_status"] == "available"
    assert items[0]["response"]["answer"] == ANSWER and items[0]["question"] == "ما مدة حفظ السجلات؟"
    replay = ask(client, headers, conversation, key)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert generate.call_count == 1 and debit(central) == -1
    assert count(service, "c06_turns", conversation) == 1 and count(service, "c06_question_intents", conversation) == 1
    with Session(tenant) as session:
        assert session.scalar(select(QuestionResult)) is None  # the frozen legacy table is never used


def test_revoked_audience_denies_replay_and_masks_history_without_redispatch(joint):
    service, central, client, headers, _, tenant, generate, factory = joint
    conversation, key = new_conversation(service), "ask-" + uuid4().hex
    assert ask(client, headers, conversation, key).status_code == 200
    with Session(tenant) as session, session.begin():
        session.scalar(select(DocumentAudience)).role_id = "denied-role"
    denied = ask(client, headers, conversation, key)
    assert denied.status_code == 403 and ANSWER not in denied.text
    item = history(factory, central, conversation)[0]
    assert item["content_status"] == "access_revoked" and item["response"] is None and item["question"] is None
    assert generate.call_count == 1


def test_archived_document_denies_delivery_of_an_earlier_answer(joint):
    service, central, client, headers, _, tenant, generate, factory = joint
    conversation, key = new_conversation(service), "ask-" + uuid4().hex
    assert ask(client, headers, conversation, key).status_code == 200
    with Session(tenant) as session, session.begin():
        session.get(Document, 1).status = "archived"
    assert ask(client, headers, conversation, key).status_code == 403
    assert history(factory, central, conversation)[0]["content_status"] == "access_revoked"
    assert generate.call_count == 1


def test_changed_question_under_a_used_key_conflicts_without_effects(joint):
    service, central, client, headers, _, _, generate, _ = joint
    conversation, key = new_conversation(service), "ask-" + uuid4().hex
    assert ask(client, headers, conversation, key).status_code == 200
    assert ask(client, headers, conversation, key, question="سؤال مختلف").status_code == 409
    assert generate.call_count == 1 and debit(central) == -1
    assert count(service, "c06_turns", conversation) == 1


def test_orchestrator_and_foreign_conversations_are_refused_before_any_effect(joint):
    service, central, client, headers, runtime, _, generate, _ = joint
    orchestrator = service.create("orchestrator", [DEPARTMENT], "orc-" + uuid4().hex)["conversation_id"]
    refused = ask(client, headers, orchestrator, "ask-" + uuid4().hex)
    assert refused.status_code == 403
    foreign = ConversationService(store(runtime, user="admin"), central).create(
        "department", [DEPARTMENT], "adm-" + uuid4().hex)["conversation_id"]
    assert ask(client, headers, foreign, "ask-" + uuid4().hex).status_code == 404
    assert ask(client, headers, str(uuid4()), "ask-" + uuid4().hex).status_code == 404
    assert generate.call_count == 0 and debit(central) == 0
    assert count(service, "c06_question_intents", orchestrator) == 0


def test_provider_failure_leaves_the_request_uncertain_without_redispatch_or_leak(joint):
    service, central, client, headers, _, _, generate, factory = joint
    generate.side_effect = RuntimeError("PRIVATE_PROVIDER_ERROR host=db.internal")
    conversation, key = new_conversation(service), "ask-" + uuid4().hex
    first = ask(client, headers, conversation, key)
    assert first.status_code == 409 and "PRIVATE_PROVIDER_ERROR" not in first.text
    second = ask(client, headers, conversation, key)
    assert second.status_code == 409 and generate.call_count == 1
    assert count(service, "c06_turns", conversation) == 0 and history(factory, central, conversation) == []


def test_missing_source_provider_disables_content_delivery(joint):
    service, central, client, headers, runtime, _, generate, _ = joint
    bare = ConversationResultStoreFactory(lambda user: store(runtime, user=user), central, None)
    client.app.state.document_result_store_factory = bare
    conversation = new_conversation(service)
    response = ask(client, headers, conversation, "ask-" + uuid4().hex)
    assert response.status_code == 503 and ANSWER not in response.text
    assert "source_authorizer_unavailable" in response.text


def test_unconfigured_integration_fails_closed_before_reservation(joint):
    service, central, client, headers, _, _, generate, _ = joint
    client.app.state.document_result_store_factory = None
    response = ask(client, headers, new_conversation(service), "ask-" + uuid4().hex)
    assert response.status_code == 503 and generate.call_count == 0 and debit(central) == 0
