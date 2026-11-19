from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import User
from app.usage import service as u
from app.usage.models import UsageEntry, UsageRequest
from app.usage.processor import ConfirmedTechnicalFailure, ProviderReceipt
from app.usage.question_adapter import QuestionCommand, execute_question
from test_usage import usage, configure


def command(**changes):
    return QuestionCommand(**{"request_key": "delivery-1", "question": "ما السياسة؟",
        "department_ids": ("hr",), "context_revision": "new-conversation-v1", **changes})


def invoke(engine, *, cmd=None, process=None, loader=None, authorize=None, user="employee", quote=100):
    return execute_question(engine, authenticated_user_id=user, command=cmd or command(),
        estimated_micro_usd=quote, authorize_content=authorize or (lambda *_: True),
        process=process or (lambda _: "no_answer"), load_response=loader or (lambda *_: {"answer": "synthetic"}))


@pytest.mark.parametrize("outcome", ["answered", "no_answer"])
def test_result_replay_loads_external_result_and_charges_once(usage, outcome):
    engine, _, _ = usage
    configure(engine)
    stored, calls = {}, []
    def process(context):
        context.perform("generation", "fake", "model", 100,
            lambda key: (calls.append(key) or ProviderReceipt(key, 7)))
        stored[("employee", context.request_id)] = {"answer": "PRIVATE_ANSWER_CANARY"}
        return outcome
    loader = lambda user, identity: stored.get((user, identity))
    first = invoke(engine, process=process, loader=loader)
    second = invoke(engine, process=process, loader=loader)
    assert first.response == second.response == {"answer": "PRIVATE_ANSWER_CANARY"}
    assert not first.receipt.replayed and second.receipt.replayed and len(calls) == 1
    with Session(engine) as session:
        assert session.scalar(select(func.sum(UsageEntry.question_delta))) == -1
        assert session.scalar(select(func.sum(UsageEntry.cost_micro_usd))) == 7
        row = session.scalar(select(UsageRequest))
        assert row.fingerprint == command().fingerprint()
        assert "PRIVATE_ANSWER_CANARY" not in str(row.__dict__)
        assert command().question not in str(row.__dict__)


@pytest.mark.parametrize("change", [{"question": "سؤال آخر"}, {"context_revision": "context-v2"}])
def test_changed_payload_conflicts_without_processing(usage, change):
    engine, _, _ = usage
    configure(engine)
    invoke(engine)
    process = Mock()
    with pytest.raises(u.UsageError, match="request_key_payload_conflict"):
        invoke(engine, cmd=command(**change), process=process)
    process.assert_not_called()


@pytest.mark.parametrize("user,department", [("employee", "finance"), ("outsider", "hr"),
                                             ("admin", "legal"), ("employee", "unknown")])
def test_role_department_and_failed_department_denied_before_admission(usage, user, department):
    engine, _, _ = usage
    configure(engine)
    process = Mock()
    with pytest.raises(u.UsageError, match="question_department_forbidden"):
        invoke(engine, cmd=command(department_ids=(department,)), user=user, process=process)
    process.assert_not_called()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(UsageRequest)) == 0


def test_content_authorizer_is_mandatory_affirmative_and_checked_on_replay(usage):
    engine, _, _ = usage
    configure(engine)
    invoke(engine)
    for value in (False, None, "yes"):
        process, loader = Mock(), Mock()
        with pytest.raises(u.UsageError, match="question_content_forbidden"):
            invoke(engine, process=process, loader=loader, authorize=lambda *_: value)
        process.assert_not_called()
        loader.assert_not_called()


def test_scope_change_and_quote_change_conflict_for_authorized_admin(usage):
    engine, _, _ = usage
    configure(engine)
    invoke(engine, user="admin")
    process = Mock()
    for changes in ({"cmd": command(department_ids=("finance",))}, {"quote": 101}):
        with pytest.raises(u.UsageError, match="request_key_payload_conflict"):
            invoke(engine, user="admin", process=process, **changes)
    process.assert_not_called()


def test_request_keys_are_scoped_to_authenticated_user(usage):
    engine, _, _ = usage
    configure(engine)
    loader = Mock(side_effect=lambda user, identity: {"owner": user, "id": identity})
    employee = invoke(engine, loader=loader)
    admin = invoke(engine, user="admin", loader=loader)
    assert employee.receipt.request_id != admin.receipt.request_id
    assert employee.response["owner"] == "employee" and admin.response["owner"] == "admin"
    assert not employee.receipt.replayed and not admin.receipt.replayed


@pytest.mark.parametrize("user", ["employee", "missing"])
def test_inactive_and_unknown_users_cannot_retrieve_previous_results(usage, user):
    engine, _, _ = usage
    configure(engine)
    if user == "employee":
        invoke(engine)
        with Session(engine) as session, session.begin():
            session.get(User, user).status = "inactive"
    loader = Mock()
    with pytest.raises(u.UsageError, match="user_not_active|user_not_found"):
        invoke(engine, user=user, loader=loader)
    loader.assert_not_called()


def test_content_access_rechecked_before_first_response(usage):
    engine, _, _ = usage
    configure(engine)
    authorization = Mock(side_effect=[True, False])
    loader = Mock()
    with pytest.raises(u.UsageError, match="question_content_forbidden"):
        invoke(engine, authorize=authorization, loader=loader)
    assert authorization.call_count == 2
    loader.assert_not_called()


def test_revoked_access_after_processing_blocks_response_not_accounting(usage):
    engine, _, _ = usage
    configure(engine)
    def process(context):
        with Session(engine) as session, session.begin():
            session.get(User, "employee").status = "inactive"
        return "answered"
    loader = Mock()
    with pytest.raises(u.UsageError, match="user_not_active"):
        invoke(engine, process=process, loader=loader)
    loader.assert_not_called()
    with Session(engine) as session:
        assert session.scalar(select(UsageRequest)).state == "completed"


@pytest.mark.parametrize("policy", [{"monthly_questions": 0}, {"monthly_budget_micro_usd": 0}])
def test_limits_block_processing(usage, policy):
    engine, _, _ = usage
    configure(engine, **policy)
    process = Mock()
    with pytest.raises(u.UsageError) as error:
        invoke(engine, process=process)
    assert error.value.status == 429
    process.assert_not_called()


def test_unconfigured_policy_does_not_process(usage):
    engine, _, _ = usage
    process = Mock()
    with pytest.raises(u.UsageError, match="usage_policy_not_configured"):
        invoke(engine, process=process)
    process.assert_not_called()


def test_missing_result_is_not_reprocessed_or_refunded(usage):
    engine, _, _ = usage
    configure(engine)
    process = Mock(return_value="answered")
    for _ in range(2):
        with pytest.raises(u.UsageError, match="question_result_unavailable"):
            invoke(engine, process=process, loader=lambda *_: None)
    assert process.call_count == 1


def test_confirmed_failure_and_uncertain_timeout_never_load_response(usage):
    engine, _, _ = usage
    configure(engine)
    def confirmed(context):
        context.perform("call", "fake", "model", 100, lambda key: ProviderReceipt(key, 3))
        raise ConfirmedTechnicalFailure()
    loader = Mock()
    assert invoke(engine, process=confirmed, loader=loader).receipt.state == "refunded"
    def timeout(context):
        context.authorize("call", "fake", "model", 100)
        raise TimeoutError("PRIVATE_EXCEPTION_CANARY")
    result = invoke(engine, cmd=command(request_key="timeout"), process=timeout, loader=loader)
    assert result.receipt.state == "uncertain" and result.response is None
    loader.assert_not_called()
    with Session(engine) as session:
        assert u.total_held(session) == 100


def test_command_is_strict_bounded_and_fingerprint_canonical():
    first = command(department_ids=("hr", "finance"))
    assert first.fingerprint() == command(department_ids=("finance", "hr")).fingerprint()
    assert first.fingerprint() != command().fingerprint()
    for changes in ({"request_key": ""}, {"question": " "}, {"question": "x" * 4001},
                    {"department_ids": ("hr", "hr")}, {"context_revision": ""},
                    {"user_id": "admin"}, {"estimated_micro_usd": 0}):
        with pytest.raises(ValidationError):
            command(**changes)
