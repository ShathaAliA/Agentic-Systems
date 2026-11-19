"""Gate 4.5: recovery diagnostics and safe re-link. New cluster only; synthetic Central."""
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.conversation_store.conversations import ConversationService
from app.conversation_store.lifecycle import QuestionLifecycleService
from app.conversation_store.recovery import QuestionRecovery
from app.usage.models import UsageRequest
from app.usage.service import UsageError
from c06_store_fixtures import DEPARTMENT
from test_c06_conversations_postgres import context, create
from test_c06_lifecycle_postgres import lifecycle, make_process, ask, debit, count
from test_c06_result_store_postgres import engines, store, pytestmark
from test_usage import usage, configure
from test_c06_security_postgres import CANARY, central_dump


def by_key(report):
    return {item["request_key"]: item["status"] for item in report["items"]}


def unlink_once(svc, identity, key, calls):
    with patch.object(ConversationService, "append_completed", side_effect=UsageError(503, "result_store_unavailable")):
        with pytest.raises(UsageError):
            ask(svc, identity, key, make_process(identity, calls))


def test_report_classifies_every_lifecycle_state_without_content(lifecycle):
    svc, service, central, runtime = lifecycle
    identity, calls = create(service), []
    keys = {name: f"{name}-{uuid4().hex}" for name in ("linked", "unlinked", "uncertain", "refused")}
    ask(svc, identity, keys["linked"], make_process(identity, calls), question=f"linked {CANARY}")
    unlink_once(svc, identity, keys["unlinked"], calls)

    def failing(c, command):
        raise RuntimeError(CANARY)
    assert ask(svc, identity, keys["uncertain"], failing)["status"] == "uncertain"
    configure(central, enabled=False)
    with pytest.raises(UsageError):
        ask(svc, identity, keys["refused"], make_process(identity, calls))
    report = QuestionRecovery(service, central).report()
    states = by_key(report)
    assert states[keys["linked"]] == "linked" and states[keys["unlinked"]] == "completed_unlinked"
    assert states[keys["uncertain"]] == "uncertain" and states[keys["refused"]] == "not_started"
    assert set(report["summary"]) >= {"linked", "completed_unlinked", "uncertain", "not_started"}
    assert CANARY not in repr(report) and "question" not in str(report["items"][0].keys())


def test_report_is_owner_scoped(lifecycle, context):
    svc, service, central, runtime = lifecycle
    identity, calls = create(service), []
    ask(svc, identity, "mine-" + uuid4().hex, make_process(identity, calls))
    other = QuestionRecovery(ConversationService(store(runtime, user="admin"), central), central).report()
    assert all(item["conversation_id"] != identity for item in other["items"])


def test_relink_repairs_completed_result_without_dispatch_or_new_charge(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    unlink_once(svc, identity, key, calls)
    recovery = QuestionRecovery(service, central)
    assert count(service, "c06_turns", identity) == 0
    first = recovery.relink(identity, key)
    second = recovery.relink(identity, key)
    assert first["sequence"] == 1 and not first["replayed"] and second["replayed"]
    assert len(calls) == 1 and debit(central) == -1 and count(service, "c06_turns", identity) == 1
    assert by_key(recovery.report())[key] == "linked"


def test_relink_refuses_uncertain_unstarted_foreign_and_malformed(lifecycle):
    svc, service, central, runtime = lifecycle
    identity, key = create(service), "ask-" + uuid4().hex

    def failing(c, command):
        raise RuntimeError("boom")
    ask(svc, identity, key, failing)
    recovery = QuestionRecovery(service, central)
    with pytest.raises(UsageError) as error:
        recovery.relink(identity, key)
    assert error.value.status == 409
    with pytest.raises(UsageError) as error:
        recovery.relink(identity, "missing-" + uuid4().hex)
    assert error.value.status == 404
    with pytest.raises(UsageError) as error:
        recovery.relink("not-a-uuid", key)
    assert error.value.status == 422
    stranger = QuestionRecovery(ConversationService(store(runtime, user="admin"), central), central)
    with pytest.raises(UsageError) as error:
        stranger.relink(identity, key)
    assert error.value.status == 404 and count(service, "c06_turns", identity) == 0


def test_tampered_accounting_fingerprint_is_reported_and_blocks_relink(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    unlink_once(svc, identity, key, calls)
    with Session(central) as s:
        s.query(UsageRequest).filter_by(user_id="employee", request_key=key).update({"fingerprint": "f" * 64})
        s.commit()
    recovery = QuestionRecovery(service, central)
    assert by_key(recovery.report())[key] == "integrity_error"
    with pytest.raises(UsageError) as error:
        recovery.relink(identity, key)
    assert error.value.status == 409 and count(service, "c06_turns", identity) == 0


def test_report_pagination_flag_and_limit_validation(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls = create(service), []
    for _ in range(3):
        ask(svc, identity, "ask-" + uuid4().hex, make_process(identity, calls))
    recovery = QuestionRecovery(service, central)
    small = recovery.report(limit=1)
    assert len(small["items"]) == 1 and small["truncated"] is True
    for bad in (0, 501, "5", True):
        with pytest.raises(UsageError) as error:
            recovery.report(limit=bad)
        assert error.value.status == 422


def test_relink_after_access_is_lost_is_denied(lifecycle):
    from app.models import RoleAssignment
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    unlink_once(svc, identity, key, calls)
    with Session(central) as s:
        s.query(RoleAssignment).filter_by(user_id="employee", department_id=DEPARTMENT).delete()
        s.commit()
    with pytest.raises(UsageError) as error:
        QuestionRecovery(service, central).relink(identity, key)
    assert error.value.status == 403 and count(service, "c06_turns", identity) == 0
    assert CANARY not in central_dump(central)
