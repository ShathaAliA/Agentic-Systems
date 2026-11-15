from dataclasses import replace
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.usage import service
from app.usage.external_results import PreparedQuestionResult, execute_with_result_store
from app.usage.models import UsageEntry, UsageRequest
from test_question_adapter import command
from test_usage import usage, configure


class FakeStore:
    """Test-only memory store; not a deployment implementation."""
    def __init__(self):
        self.rows = {}
        self.writes = 0
        self.reads = 0
        self.allowed = True

    def save_once(self, result):
        key = result.user_id, result.request_id
        if key in self.rows:
            raise RuntimeError("Immutable result conflict")
        self.rows[key] = deepcopy(result)
        self.writes += 1

    def load_authorized(self, user_id, request_id):
        self.reads += 1
        if not self.allowed:
            raise service.UsageError(403, "question_source_access_revoked")
        return self.rows.get((user_id, request_id))


def invoke(engine, store, process, **changes):
    args = dict(authenticated_user_id="employee", command=command(), estimated_micro_usd=10,
                authorize_content=lambda *_: True, process=process, store=store)
    args.update(changes)
    return execute_with_result_store(engine, **args)


@pytest.mark.parametrize("outcome", ["answered", "no_answer"])
def test_save_then_finish_replay_and_no_central_content(usage, outcome):
    engine, _, _ = usage
    configure(engine)
    store = FakeStore()
    process = Mock(return_value=PreparedQuestionResult(outcome, {"answer": "external-content-canary"}))
    first = invoke(engine, store, process)
    replay = invoke(engine, store, process)
    assert first.response == replay.response == {"answer": "external-content-canary"}
    assert replay.receipt.replayed and process.call_count == store.writes == 1
    assert store.reads == 2
    with Session(engine) as s:
        assert s.scalar(select(func.sum(UsageEntry.question_delta))) == -1
        assert "external-content-canary" not in str(s.scalar(select(UsageRequest)).__dict__)
    assert "external-content-canary" not in repr(next(iter(store.rows.values())))


@pytest.mark.parametrize("after_save", [False, True])
def test_save_failure_or_lost_ack_stays_uncertain_without_reprocessing(usage, after_save):
    engine, _, _ = usage
    configure(engine)
    store = FakeStore()
    original = store.save_once
    def fail(record):
        if after_save:
            original(record)
        raise RuntimeError("synthetic save failure")
    store.save_once = fail
    process = Mock(return_value=PreparedQuestionResult("answered", {"answer": "private"}))
    for _ in range(2):
        result = invoke(engine, store, process)
        assert result.receipt.state == "uncertain" and result.response is None
    assert process.call_count == 1 and store.reads == 0
    with Session(engine) as s:
        row = s.scalar(select(UsageRequest))
        assert row.held_micro_usd == 10
        assert s.scalar(select(func.sum(UsageEntry.question_delta))) == -1


def test_saved_result_with_failed_accounting_closure_is_not_delivered(usage):
    engine, _, _ = usage
    configure(engine)
    store = FakeStore()
    process = Mock(return_value=PreparedQuestionResult("answered", "private"))
    with patch.object(service, "finish", side_effect=service.UsageError(503, "synthetic_closure_failure")):
        first = invoke(engine, store, process)
    replay = invoke(engine, store, process)
    assert first.receipt.state == replay.receipt.state == "uncertain"
    assert first.response is replay.response is None
    assert store.writes == process.call_count == 1 and store.reads == 0


def test_missing_or_revoked_result_never_redispatches_or_refunds(usage):
    engine, _, _ = usage
    configure(engine)
    store = FakeStore()
    process = Mock(return_value=PreparedQuestionResult("answered", "private"))
    first = invoke(engine, store, process)
    store.allowed = False
    with pytest.raises(service.UsageError, match="question_source_access_revoked"):
        invoke(engine, store, process)
    store.allowed = True
    store.rows.clear()
    with pytest.raises(service.UsageError, match="question_result_unavailable"):
        invoke(engine, store, process)
    assert process.call_count == 1
    with Session(engine) as s:
        assert s.get(UsageRequest, first.receipt.request_id).state == "completed"
        assert s.scalar(select(func.sum(UsageEntry.question_delta))) == -1


@pytest.mark.parametrize("change", [{"user_id": "outsider"}, {"request_id": "other-request"},
    {"fingerprint": "0" * 64}, {"outcome": "no_answer"}])
def test_wrong_owner_request_fingerprint_or_outcome_is_refused(usage, change):
    engine, _, _ = usage
    configure(engine)
    store = FakeStore()
    process = Mock(return_value=PreparedQuestionResult("answered", "private"))
    invoke(engine, store, process)
    key = next(iter(store.rows))
    store.rows[key] = replace(store.rows[key], **change)
    with pytest.raises(service.UsageError, match="question_result_integrity_error"):
        invoke(engine, store, process)
    assert process.call_count == 1


def test_unauthorized_department_never_calls_external_store(usage):
    engine, _, _ = usage
    configure(engine)
    store = Mock()
    process = Mock()
    with pytest.raises(service.UsageError, match="question_department_forbidden"):
        invoke(engine, store, process, command=command(department_ids=("finance",)))
    process.assert_not_called()
    store.save_once.assert_not_called()
    store.load_authorized.assert_not_called()


def test_bad_store_configuration_fails_before_reservation(usage):
    engine, _, _ = usage
    configure(engine)
    with pytest.raises(service.UsageError, match="external_result_store_required"):
        invoke(engine, object(), Mock())
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(UsageRequest)) == 0


@pytest.mark.parametrize("prepared", [None, {"outcome": "answered"}, PreparedQuestionResult("partial", {}),
                                      PreparedQuestionResult("answered", None)])
def test_invalid_prepared_result_is_not_saved_or_delivered(usage, prepared):
    engine, _, _ = usage
    configure(engine)
    store = FakeStore()
    result = invoke(engine, store, lambda _: prepared)
    assert result.receipt.state == "uncertain" and result.response is None
    assert store.writes == store.reads == 0


def test_external_result_is_written_while_request_is_running_and_cannot_be_overwritten(usage):
    engine, _, _ = usage
    configure(engine)
    store = FakeStore()
    save = store.save_once
    def check_order(record):
        with Session(engine) as session:
            assert session.get(UsageRequest, record.request_id).state == "running"
        save(record)
    store.save_once = check_order
    result = invoke(engine, store, lambda _: PreparedQuestionResult("answered", "private"))
    record = next(iter(store.rows.values()))
    store.save_once = save
    with pytest.raises(RuntimeError, match="Immutable result conflict"):
        store.save_once(replace(record, payload="replacement"))
    assert store.rows[record.user_id, record.request_id].payload == result.response == "private"


def test_abrupt_closure_failure_leaves_running_hold_without_automatic_recovery(usage):
    engine, _, _ = usage
    configure(engine)
    store = FakeStore()
    process = Mock(return_value=PreparedQuestionResult("answered", "private"))
    with patch.object(service, "finish", side_effect=RuntimeError("synthetic abrupt closure")):
        with pytest.raises(RuntimeError, match="synthetic abrupt closure"):
            invoke(engine, store, process)
    replay = invoke(engine, store, process)
    assert replay.receipt.state == "running" and replay.response is None
    assert store.writes == process.call_count == 1 and store.reads == 0
    with Session(engine) as session:
        assert session.scalar(select(UsageRequest)).held_micro_usd == 10
