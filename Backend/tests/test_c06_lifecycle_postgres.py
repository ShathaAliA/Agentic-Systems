"""Gate 4.3: internal question lifecycle. New cluster only; synthetic Central, never project databases."""
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text, select, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.conversation_store.conversations import ConversationService
from app.conversation_store.lifecycle import QuestionLifecycleService
from app.usage.external_results import PreparedQuestionResult
from app.usage.models import UsageEntry, UsageRequest
from app.usage.service import UsageError
from c06_store_fixtures import DEPARTMENT, answer
from test_c06_conversations_postgres import context, create
from test_c06_result_store_postgres import engines, store, pytestmark
from test_usage import usage

QUESTION = "ما مدة حفظ السجلات؟"


@pytest.fixture
def lifecycle(context):
    service, central, _, _, runtime = context
    return QuestionLifecycleService(service, central, quote_provider=lambda _: 10), service, central, runtime


def make_process(identity, calls, kind="answered", bound=True):
    def process(c, command):
        calls.append(c.request_id)
        payload = answer(c.request_id)
        payload["conversation_id"] = identity if bound else "99999999-9999-4999-8999-999999999999"
        if kind == "no_answer":
            payload.update(status="no_answer", sources=[], answer_kind="system_notice", answer="لا توجد إجابة.")
        return PreparedQuestionResult(kind, payload)
    return process


def ask(svc, identity, key, process, question=QUESTION, **kw):
    return svc.ask(identity, key, question, authorize_content=lambda *_: True, process=process, **kw)


def debit(central):
    with Session(central) as s:
        return s.scalar(select(func.coalesce(func.sum(UsageEntry.question_delta), 0)))


def count(service, table, identity):
    """Rows for ONE conversation: the store is shared by the module and is immutable."""
    where = {"c06_results": "payload->>'conversation_id'=:id"}.get(table, "conversation_id=CAST(:id AS uuid)")
    with service.store.connection() as c:
        return c.scalar(text(f"SELECT count(*) FROM public.{table} WHERE {where}"), {"id": str(identity)})


def test_answer_links_turn_and_replay_keeps_one_debit_and_dispatch(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    first = ask(svc, identity, key, make_process(identity, calls))
    assert first["status"] == "answered" and first["turn"]["sequence"] == 1 and not first["replayed"]
    # Replay after the turn exists: stored context revision keeps the fingerprint identical.
    second = ask(svc, identity, key, make_process(identity, calls))
    assert second["status"] == "answered" and second["replayed"] and second["turn"]["sequence"] == 1
    assert len(calls) == 1 and debit(central) == -1
    assert count(service, "c06_turns", identity) == 1 and count(service, "c06_question_intents", identity) == 1
    with service.store.connection() as c:
        assert c.scalar(text("SELECT context_revision FROM public.c06_question_intents WHERE conversation_id=CAST(:id AS uuid)"), {"id": identity}).endswith(":0")


def test_no_answer_charges_one_unit_and_links(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls = create(service), []
    result = ask(svc, identity, "na-" + uuid4().hex, make_process(identity, calls, "no_answer"))
    assert result["status"] == "no_answer" and result["turn"]["sequence"] == 1 and debit(central) == -1


def test_same_key_with_changed_question_conflicts_without_effects(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    ask(svc, identity, key, make_process(identity, calls))
    with pytest.raises(UsageError, match="question_request_conflict"):
        ask(svc, identity, key, make_process(identity, calls), question="سؤال مختلف")
    assert len(calls) == 1 and debit(central) == -1 and count(service, "c06_turns", identity) == 1


def test_orchestrator_conversation_and_foreign_conversation_are_refused(lifecycle, context):
    svc, service, central, runtime = lifecycle
    orchestrator = service.create("orchestrator", [DEPARTMENT], "orc-" + uuid4().hex)["conversation_id"]
    calls = []
    with pytest.raises(UsageError, match="question_mode_unsupported"):
        ask(svc, orchestrator, "ask-" + uuid4().hex, make_process(orchestrator, calls))
    mine = create(service)
    other = QuestionLifecycleService(ConversationService(store(runtime, user="admin"), central), central,
                                     quote_provider=lambda _: 10)
    with pytest.raises(UsageError, match="conversation_not_found"):
        ask(other, mine, "ask-" + uuid4().hex, make_process(mine, calls))
    assert calls == [] and debit(central) == 0
    assert count(service, "c06_question_intents", orchestrator) == 0 and count(service, "c06_question_intents", mine) == 0


def test_missing_quote_or_bad_quote_disables_questions_before_any_record(lifecycle):
    _, service, central, _ = lifecycle
    identity, calls = create(service), []
    for provider in (None, lambda _: 0, lambda _: "10"):
        svc = QuestionLifecycleService(service, central, quote_provider=provider)
        with pytest.raises(UsageError, match="question_quote_unavailable"):
            ask(svc, identity, "ask-" + uuid4().hex, make_process(identity, calls))
    assert calls == [] and debit(central) == 0


def test_unbound_payload_is_never_saved_and_request_stays_uncertain(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    first = ask(svc, identity, key, make_process(identity, calls, bound=False))
    assert first["status"] == "uncertain" and first["response"] is None and first["turn"] is None
    second = ask(svc, identity, key, make_process(identity, calls))
    assert second["status"] == "uncertain" and len(calls) == 1
    assert count(service, "c06_results", identity) == 0 and count(service, "c06_turns", identity) == 0


def test_crash_between_finish_and_link_recovers_on_replay_without_redispatch(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    with patch.object(ConversationService, "append_completed", side_effect=UsageError(503, "result_store_unavailable")):
        with pytest.raises(UsageError, match="result_store_unavailable"):
            ask(svc, identity, key, make_process(identity, calls))
    assert count(service, "c06_turns", identity) == 0 and count(service, "c06_results", identity) == 1
    recovered = ask(svc, identity, key, make_process(identity, calls))
    assert recovered["status"] == "answered" and recovered["replayed"] and recovered["turn"]["sequence"] == 1
    assert len(calls) == 1 and debit(central) == -1 and count(service, "c06_turns", identity) == 1


def test_concurrent_identical_requests_dispatch_and_link_once(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex

    def slow(c, command):
        sleep(0.4)
        return make_process(identity, calls)(c, command)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: ask(svc, identity, key, slow), range(2)))
    assert {o["status"] for o in outcomes} <= {"answered", "processing"} and "answered" in {o["status"] for o in outcomes}
    final = ask(svc, identity, key, slow)
    assert final["status"] == "answered" and final["replayed"]
    assert len(calls) == 1 and debit(central) == -1
    assert count(service, "c06_turns", identity) == 1 and count(service, "c06_question_intents", identity) == 1


def test_revoked_source_on_replay_denies_content_without_redispatch(lifecycle, context):
    svc, service, central, runtime = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    ask(svc, identity, key, make_process(identity, calls))
    denied = QuestionLifecycleService(ConversationService(store(runtime, authorize=lambda *_: False), central),
                                      central, quote_provider=lambda _: 10)
    with pytest.raises(UsageError, match="question_source_access_revoked"):
        ask(denied, identity, key, make_process(identity, calls))
    assert len(calls) == 1 and debit(central) == -1


def test_intents_are_insert_only_and_hold_no_central_content(lifecycle, engines):
    svc, service, central, runtime = lifecycle
    identity, calls = create(service), []
    ask(svc, identity, "ask-" + uuid4().hex, make_process(identity, calls))
    with service.store.connection() as c:
        for query in ("UPDATE c06_question_intents SET question='bad'", "DELETE FROM c06_question_intents",
                      "TRUNCATE c06_question_intents"):
            with pytest.raises(DBAPIError):
                with c.begin_nested():
                    c.execute(text(query))
    with Session(central) as s:
        columns = {col.name for col in UsageRequest.__table__.columns}
        assert not {"question", "answer", "payload"} & columns


def test_missing_intent_grant_fails_closed_before_any_charge(lifecycle, engines):
    svc, service, central, _ = lifecycle
    admin, _ = engines
    identity, calls = create(service), []
    with admin.begin() as c:
        c.execute(text("REVOKE INSERT ON c06_question_intents FROM c06_test_api"))
    try:
        with pytest.raises(UsageError, match="result_store_unavailable"):
            ask(svc, identity, "ask-" + uuid4().hex, make_process(identity, calls))
    finally:
        with admin.begin() as c:
            c.execute(text("GRANT INSERT ON c06_question_intents TO c06_test_api"))
    assert calls == [] and debit(central) == 0
