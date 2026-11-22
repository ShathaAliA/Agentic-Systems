from contextlib import contextmanager
from types import SimpleNamespace as Obj
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.config import AuthSettings
from app.main import create_app
from app.models import Base as CentralBase
from app.documents.models import Base as TenantBase, Document, DocumentAudience, QuestionResult, DocumentChunk, DocumentVersion
from app.conversation_store.postgres import PostgresResultStore, validated
from app.documents.source_authorization import DocumentSourceAuthorizer
from uuid import uuid4
DEPT = str(uuid4())
CONV = str(uuid4())
from app.documents import router, provider_accounting as billing
from app.usage.models import UsageRequest
from app.usage.processor import ProviderReceipt, ConfirmedTechnicalFailure
from app.usage.service import UsageError
from app.usage.question_adapter import QuestionCommand
from app.usage.external_results import execute_with_result_store
from test_usage import usage, configure


def test_migration_heads_and_manifest_fail_closed():
    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.provisioning.contract import read_contract
    root = Path(__file__).parents[1]
    central = Config()
    central.set_main_option("script_location", str(root / "alembic"))
    tenant = Config()
    tenant.set_main_option("script_location", str(root / "tenant_alembic"))
    assert ScriptDirectory.from_config(central).get_heads() == ["handoff001_policy"]
    assert ScriptDirectory.from_config(tenant).get_heads() == ["tenant_0008"]
    contract = read_contract(root / "tenant-contract.json")
    assert contract.ready is False
    assert contract.sequence_privileges["documents_id_seq"] == ["USAGE", "SELECT"]
    assert "question_results" not in contract.table_privileges
    assert "question_results" not in contract.required_tables


def test_provider_quote_uses_caps_and_settles_actual_usage_without_word_counts():
    settings = billing.Settings("reviewed-model", "price-v1", 150000, 600000)
    call = Mock(return_value=Obj(id="upstream-receipt", usage=Obj(prompt_tokens=10, completion_tokens=5),
        choices=[Obj(message=Obj(content="إجابة"))]))
    client = Obj(chat=Obj(completions=Obj(create=call)))
    answer, receipt = billing.generate(settings, "سؤال", "مصدر", "stable-attempt", client=client)
    assert answer == "إجابة" and receipt.cost_micro_usd == 5
    assert receipt.input_tokens == 10 and receipt.output_tokens == 5
    assert settings.envelope > receipt.cost_micro_usd
    assert call.call_args.kwargs["max_completion_tokens"] == settings.output_limit
    assert call.call_args.kwargs["extra_headers"]["Idempotency-Key"] == "stable-attempt"
    assert receipt.key.startswith("openai:") and "stable-attempt" not in receipt.key
    with pytest.raises(ConfirmedTechnicalFailure):
        billing.generate(settings, "x" * settings.input_limit, "", "too-large", client=client)
    assert call.call_count == 1


def test_missing_usage_or_prices_never_manufactures_a_receipt(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_PRICING_REVISION", raising=False)
    with pytest.raises(UsageError, match="not_configured"):
        billing.Settings.from_environment()
    call = Mock(return_value=Obj(id="receipt", usage=None))
    with pytest.raises(UsageError, match="unverified"):
        billing.generate(billing.Settings("model", "v1", 1000, 1000), "q", "c", "a",
            client=Obj(chat=Obj(completions=Obj(create=call))))
    with pytest.raises(UsageError, match="cached_usage_price"):
        billing.Settings("model", "v1", 1000, 1000).cost(100, 10, 50)
    assert billing.Settings("model", "v1", 1000, 1000, cached_input_rate=100).cost(100, 10, 50) == 1


@pytest.fixture
def question_http(usage, monkeypatch):
    central, _, headers = usage
    configure(central)
    from app.models import Department, DepartmentMembership, RoleAssignment
    with Session(central) as session, session.begin():
        session.execute(update(Department).where(Department.id=="hr").values(id=DEPT))
        session.execute(update(DepartmentMembership).where(DepartmentMembership.department_id=="hr").values(department_id=DEPT))
        session.execute(update(RoleAssignment).where(RoleAssignment.department_id=="hr").values(department_id=DEPT))
    tenant = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TenantBase.metadata.create_all(tenant)
    with Session(tenant) as session, session.begin():
        session.add(Document(id=1, filename="synthetic.pdf", file_path="unused", department_id=DEPT,
            version=1, status="approved"))
        session.add(DocumentVersion(id=1, document_id=1, version_number=1, status="approved"))
        session.add(DocumentChunk(id=1, document_id=1, version_number=1, content="synthetic authorized evidence", page_number=1))
        session.add(DocumentAudience(document_id=1, department_id=DEPT, role_id="reader", created_by="admin"))
    @contextmanager
    def open_tenant(user_id, department_id, permission=None, permission_code=None):
        assert department_id == DEPT
        with Session(tenant) as session:
            yield session
    monkeypatch.setattr(router, "_open_department_session", open_tenant)
    monkeypatch.setattr(router.rag_service, "retrieve_relevant_chunks", lambda *_, **kwargs: [{
        "document_id": 1, "document_name": "synthetic.pdf", "chunk_id": 1,
        "version_number": 1, "page_number": 1, "content": "synthetic authorized evidence"}])
    monkeypatch.setattr(billing.Settings, "from_environment", classmethod(lambda cls: cls("fake", "v1", 1000, 1000)))
    provider = Mock(return_value=("PRIVATE_TENANT_ANSWER", ProviderReceipt("verified-test-receipt", 1, 10, 5)))
    monkeypatch.setattr(billing, "generate", provider)
    records = {}
    class SyntheticStore(PostgresResultStore):
        def __init__(self, user_id, conversation_id, department_id):
            self.user_id = user_id
            self.authorize = DocumentSourceAuthorizer(central, open_tenant,
                user_id=user_id, conversation_id=conversation_id, department_id=department_id)
        def save_once(self, record):
            validated(record)
            assert record.request_id not in records
            records[record.request_id] = record
        def load_authorized(self, user_id, request_id):
            assert user_id == self.user_id
            record = records.get(request_id)
            if record and self.authorize(user_id, record.payload) is not True:
                raise UsageError(403, "question_source_access_revoked")
            return record
    class RouteLevelLifecycle:
        """Offline stand-in for QuestionLifecycleService: Core's real lifecycle needs a PostgreSQL C06
        store and is exercised in test_joint_ask_postgres.py. Here only the route behaviour is checked."""
        def __init__(self, store, central_engine, settings):
            self.store, self.central, self.settings = store, central_engine, settings

        def ask(self, conversation_id, request_key, question, *, authorize_content, process):
            command = QuestionCommand(request_key=request_key, question=question,
                department_ids=(DEPT,), context_revision=conversation_id)
            result = execute_with_result_store(self.central, authenticated_user_id=self.store.user_id,
                command=command, estimated_micro_usd=self.settings.envelope,
                authorize_content=authorize_content, process=lambda ctx: process(ctx, command), store=self.store)
            status = {"running": "processing", "reserved": "processing"}.get(result.receipt.state, result.receipt.state)
            if result.response is not None:
                return dict(status=result.receipt.outcome, request_id=result.receipt.request_id,
                            replayed=result.receipt.replayed, turn=None, response=result.response)
            return dict(status=status, request_id=result.receipt.request_id, replayed=result.receipt.replayed,
                        turn=None, response=None)
    monkeypatch.setattr(router, "_question_lifecycle",
        lambda store, central_engine, settings: RouteLevelLifecycle(store, central_engine, settings))
    app = create_app(central_engine=central, document_result_store_factory=SyntheticStore, auth_settings=AuthSettings(mode="development_password", app_env="test"))
    with TestClient(app) as client:
        yield client, central, tenant, headers, provider, records
    tenant.dispose()


def ask(client, headers, **changes):
    if "query" in changes: changes["question"] = changes.pop("query")
    return client.post("/api/documents/ask", headers=headers, json={
        "question": "سؤال اصطناعي", "department_id": DEPT, "conversation_id": CONV,
        "request_key": "delivery", **changes})


def test_paid_replay_is_owned_tenant_only_and_revoked_audience_is_denied(question_http):
    client, central, tenant, headers, provider, records = question_http
    first = ask(client, headers["employee"])
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    replay = ask(client, headers["employee"])
    assert replay.status_code == 200 and replay.json() == first.json()
    assert provider.call_count == 1
    with Session(central) as session:
        row = session.scalar(select(UsageRequest))
        assert "PRIVATE_TENANT_ANSWER" not in str(row.__dict__)
        assert "documents" not in CentralBase.metadata.tables
    with Session(tenant) as session, session.begin():
        assert session.scalar(select(QuestionResult)) is None
        assert next(iter(records.values())).payload["answer"] == "PRIVATE_TENANT_ANSWER"
        session.scalar(select(DocumentAudience)).role_id = "denied-role"
    denied = ask(client, headers["employee"])
    assert denied.status_code == 403 and "PRIVATE_TENANT_ANSWER" not in denied.text
    assert provider.call_count == 1


def test_question_role_scope_and_changed_payload_are_rejected(question_http):
    client, _, _, headers, provider, _ = question_http
    assert ask(client, headers["outsider"]).status_code == 403
    assert ask(client, headers["employee"]).status_code == 200
    assert ask(client, headers["employee"], query="different").status_code == 409
    assert ask(client, headers["employee"], department_id=str(uuid4())).status_code == 403
    assert provider.call_count == 1


def test_missing_result_never_redispatches_or_refunds(question_http):
    client, _, tenant, headers, provider, records = question_http
    assert ask(client, headers["employee"]).status_code == 200
    # Simulate loss only in the synthetic SQLite store, not a production deletion API.
    records.clear()
    assert ask(client, headers["employee"]).status_code == 503
    assert provider.call_count == 1


def test_scoped_role_from_other_department_is_not_an_audience_role(usage):
    from app.models import Role, RoleAssignment
    from app.documents.audience_service import get_user_context
    central, _, _ = usage
    with Session(central) as session, session.begin():
        session.add(Role(id="finance-only", name="Synthetic finance", scope="department"))
        session.flush()
        session.add(RoleAssignment(user_id="employee", role_id="finance-only", department_id="finance"))
    with Session(central) as session:
        departments, roles = get_user_context(session, "employee", "hr")
        assert departments == ["hr"] and "reader" in roles and "finance-only" not in roles
