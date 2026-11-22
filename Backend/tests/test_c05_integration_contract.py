"""C05 Core-side integration contract gates.

All database interactions below use test_usage's fresh in-memory SQLite fixture.
No document modules, live tenant databases, migrations, or provider calls are used.
Partner-branch findings are reviewed in docs/c05-integration-readiness.md, not
imported into this branch's test environment.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings
from app.main import create_app
from app.models import Base
from app.provisioning.tenant_migrations import TenantMigrator
from app.services.authorization import has_permission
from app.usage.external_results import (
    PreparedQuestionResult,
    execute_with_result_store,
)
from app.usage.models import UsageEntry, UsageRequest
from app.usage.service import UsageError
from app.usage.question_adapter import QuestionCommand
from contracts.document_api_draft import AnswerResponse, QuestionRequest
from test_external_results import FakeStore
from test_question_adapter import command
from test_usage import configure, usage

BACKEND = Path(__file__).resolve().parents[1]


def test_c05_core_does_not_expose_document_endpoints_or_central_document_tables():
    app = create_app(
        auth_settings=AuthSettings(
            mode="development_password",
            app_env="test",
        )
    )

    from test_core_access import effective_routes, dependency_calls
    from app.auth.dependencies import get_auth_context
    routes = [r for r in effective_routes(app) if getattr(r, "path", "").startswith("/api/documents")]
    assert routes  # partner branch mounts documents, unlike the Core-only branch
    assert all(get_auth_context in set(dependency_calls(r.dependant)) for r in routes)
    assert app.state.document_result_store_factory is None

    assert not any(
        name in Base.metadata.tables
        for name in (
            "documents",
            "document_chunks",
            "document_audiences",
            "question_results",
        )
    )

    assert "result_payload" not in UsageRequest.__table__.columns


def test_c05_tenant_example_stays_unready_and_has_no_activation_policy():
    manifest = json.loads(
        (BACKEND / "tenant-contract.example.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["contract_version"] == 1
    assert manifest["ready"] is False
    assert manifest["required_revision"] is None
    assert manifest["required_tables"] == []
    assert manifest["table_privileges"] == {}
    assert manifest["sequence_privileges"] == {}

    migrator = TenantMigrator.from_contract(
        BACKEND / "tenant-contract.example.json"
    )

    assert not migrator.available
    assert migrator.error_code == "tenant_contract_unavailable"


@pytest.mark.parametrize(
    "change",
    [
        {
            "ready": True,
            "required_revision": "tenant_0001",
            "required_tables": ["_provisioning_identity"],
        },
        {
            "ready": True,
            "required_revision": "tenant_0007",
            "required_tables": [
                "_provisioning_identity",
                "question_results",
            ],
            "table_privileges": {},
        },
    ],
)
def test_c05_no_ready_tenant_without_business_revision_and_explicit_grants(
    tmp_path,
    change,
):
    data = {
        "contract_version": 1,
        "ready": False,
        "required_revision": None,
        "required_tables": [],
        "table_privileges": {},
        "sequence_privileges": {},
    }

    data.update(change)

    path = tmp_path / "synthetic-unapproved-contract.json"
    path.write_text(
        json.dumps(data),
        encoding="utf-8",
    )

    assert not TenantMigrator.from_contract(path).available


def test_c05_core_migration_heads_are_not_partner_branch_heads():
    # Metadata-only Alembic inspection.
    # Never load env.py or open a database.

    central = ScriptDirectory(str(BACKEND / "alembic"))
    tenant = ScriptDirectory(str(BACKEND / "tenant_alembic"))

    assert central.get_heads() == ["handoff001_policy"]
    assert tenant.get_heads() == ["tenant_0008"]
    assert {"usage002_result_payload", "usage003_merge", "usage004_fence"} <= {
        revision.revision for revision in central.walk_revisions()}



@pytest.mark.parametrize(
    "injected",
    [
        {"user_id": "admin"},
        {"role": "Admin"},
        {"estimated_micro_usd": 0},
        {"claim_id": "synthetic"},
        {"context_revision": "client-forged"},
    ],
)
def test_c05_draft_question_request_rejects_untrusted_integration_fields(
    injected,
):
    payload = {
        "department_id": "hr",
        "question": "ما السياسة؟",
        "request_key": "c05-1",
    }

    assert (
        QuestionRequest.model_validate(payload).department_id
        == "hr"
    )

    with pytest.raises(ValidationError):
        QuestionRequest.model_validate(
            {**payload, **injected}
        )


def test_c05_answer_requires_evidence_and_no_private_context_fields():
    valid = {
        "status": "no_answer",
        "answer": "لا توجد إجابة في المصادر.",
        "sources": [],
        "handoff_available": False,
    }

    assert (
        AnswerResponse.model_validate(valid).status
        == "no_answer"
    )

    for invalid in (
        {**valid, "status": "answered"},
        {**valid, "context_used": ["PRIVATE_CANARY"]},
        {**valid, "user_id": "admin"},
    ):
        with pytest.raises(ValidationError):
            AnswerResponse.model_validate(invalid)


def test_c05_u1_command_is_strict_and_fingerprint_binds_source_scope():
    original = command()

    assert original.fingerprint() == command().fingerprint()

    assert (
        original.fingerprint()
        != command(department_ids=("finance",)).fingerprint()
    )

    assert (
        original.fingerprint()
        != command(context_revision="other-version").fingerprint()
    )

    with pytest.raises(ValidationError):
        QuestionCommand.model_validate(
            {
                **original.model_dump(),
                "provider_cost": 0,
            }
        )


def test_c05_synthetic_rbac_rejects_finance_and_failed_legal(usage):
    engine, _, _ = usage

    with Session(engine) as session:
        assert has_permission(
            session,
            "employee",
            "documents:read",
            "hr",
        )

        assert not has_permission(
            session,
            "employee",
            "documents:read",
            "finance",
        )

        assert not has_permission(
            session,
            "admin",
            "documents:read",
            "legal",
        )

        assert not has_permission(
            session,
            "missing",
            "documents:read",
            "hr",
        )


def test_c05_result_replay_and_revocation_do_not_duplicate_charge(usage):
    engine, _, _ = usage
    configure(engine)

    store = FakeStore()

    process = Mock(
        return_value=PreparedQuestionResult(
            "answered",
            {
                "answer": "PRIVATE_C05_ANSWER",
                "sources": ["synthetic"],
            },
        )
    )

    args = dict(
        authenticated_user_id="employee",
        command=command(),
        estimated_micro_usd=10,
        authorize_content=lambda *_: True,
        process=process,
        store=store,
    )

    first = execute_with_result_store(engine, **args)
    replay = execute_with_result_store(engine, **args)

    assert first.response == replay.response
    assert replay.receipt.replayed
    assert process.call_count == store.writes == 1

    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.sum(UsageEntry.question_delta))
            )
            == -1
        )

        assert "PRIVATE_C05_ANSWER" not in repr(
            session.scalar(
                select(UsageRequest)
            ).__dict__
        )

    store.allowed = False

    with pytest.raises(
        UsageError,
        match="question_source_access_revoked",
    ):
        execute_with_result_store(engine, **args)

    assert process.call_count == store.writes == 1


def test_c05_unauthorized_department_never_reaches_result_store(usage):
    engine, _, _ = usage
    configure(engine)

    store = Mock()
    process = Mock()

    with pytest.raises(
        UsageError,
        match="question_department_forbidden",
    ):
        execute_with_result_store(
            engine,
            authenticated_user_id="employee",
            command=command(
                department_ids=("finance",)
            ),
            estimated_micro_usd=10,
            authorize_content=lambda *_: True,
            process=process,
            store=store,
        )

    process.assert_not_called()
    store.save_once.assert_not_called()
    store.load_authorized.assert_not_called()

    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(UsageRequest)
            )
            == 0
        )
