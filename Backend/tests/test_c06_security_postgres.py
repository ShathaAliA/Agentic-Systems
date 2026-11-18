"""Gate 4.4: isolation, permission and secret-handling checks across the question lifecycle.
New cluster only; synthetic Central, never project databases. Counts are per conversation
because the C06 store is module-scoped and immutable."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.conversation_store.conversations import ConversationService
from app.conversation_store.lifecycle import QuestionLifecycleService
from app.models import Department, DepartmentMembership, RoleAssignment, User
from app.usage.service import UsageError
from c06_store_fixtures import DEPARTMENT
from test_c06_conversations_postgres import context, create
from test_c06_lifecycle_postgres import lifecycle, make_process, ask, debit, count
from test_c06_result_store_postgres import engines, store, pytestmark
from test_usage import usage, configure

CANARY = "CANARY-SECRET-" + uuid4().hex


def central_dump(central):
    raw = central.raw_connection()
    try:
        return "\n".join(raw.driver_connection.iterdump())
    finally:
        raw.close()


def no_effects(service, central, identity, calls):
    assert calls == [] and debit(central) == 0
    assert count(service, "c06_question_intents", identity) == 0 and count(service, "c06_turns", identity) == 0


@pytest.mark.parametrize("change", ["inactive_user", "membership", "role", "failed_department", "archived_department"])
def test_lost_access_blocks_new_question_before_any_record_charge_or_dispatch(lifecycle, change):
    svc, service, central, _ = lifecycle
    identity, calls = create(service), []
    with Session(central) as s:
        if change == "inactive_user": s.get(User, "employee").status = "inactive"
        if change == "membership": s.query(DepartmentMembership).filter_by(user_id="employee", department_id=DEPARTMENT).update({"status": "inactive"})
        if change == "role": s.query(RoleAssignment).filter_by(user_id="employee", department_id=DEPARTMENT).delete()
        if change == "failed_department": s.get(Department, DEPARTMENT).status = "failed"
        if change == "archived_department": s.get(Department, DEPARTMENT).archived_at = datetime.now(timezone.utc)
        s.commit()
    with pytest.raises(UsageError) as error:
        ask(svc, identity, "ask-" + uuid4().hex, make_process(identity, calls))
    assert error.value.status == 403
    no_effects(service, central, identity, calls)


def test_access_revoked_during_processing_never_delivers_and_recovers_after_restore(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    inner = make_process(identity, calls)

    def revoking(c, command):
        prepared = inner(c, command)
        with Session(central) as s:
            s.query(RoleAssignment).filter_by(user_id="employee", department_id=DEPARTMENT).delete()
            s.commit()
        return prepared
    with pytest.raises(UsageError) as error:
        ask(svc, identity, key, revoking)
    assert error.value.status == 403 and count(service, "c06_turns", identity) == 0
    with Session(central) as s:
        s.add(RoleAssignment(user_id="employee", department_id=DEPARTMENT, role_id="reader"))
        s.commit()
    again = ask(svc, identity, key, inner)
    assert again["status"] == "answered" and again["replayed"] and len(calls) == 1 and debit(central) == -1


def test_same_key_is_independent_per_owner_and_admin_cannot_use_foreign_conversation(lifecycle, context):
    svc, service, central, runtime = lifecycle
    mine, calls, key = create(service), [], "shared-" + uuid4().hex
    first = ask(svc, mine, key, make_process(mine, calls))
    admin_service = ConversationService(store(runtime, user="admin"), central)
    admin_svc = QuestionLifecycleService(admin_service, central, quote_provider=lambda _: 10)
    with pytest.raises(UsageError) as error:
        ask(admin_svc, mine, key, make_process(mine, calls))
    assert error.value.status == 404 and count(service, "c06_question_intents", mine) == 1
    theirs = admin_service.create("department", [DEPARTMENT], "admin-" + uuid4().hex)["conversation_id"]
    second = ask(admin_svc, theirs, key, make_process(theirs, calls))
    assert second["status"] == "answered" and second["request_id"] != first["request_id"] and len(calls) == 2
    assert count(service, "c06_turns", mine) == 1 and count(admin_service, "c06_turns", theirs) == 1


def test_database_rls_hides_and_rejects_other_owner_rows(lifecycle, engines):
    svc, service, central, runtime = lifecycle
    identity, calls = create(service), []
    ask(svc, identity, "ask-" + uuid4().hex, make_process(identity, calls))
    stranger = store(runtime, user="outsider")
    with stranger.connection() as c:
        for table in ("c06_question_intents", "c06_turns", "c06_conversations"):
            assert c.scalar(text(f"SELECT count(*) FROM public.{table} WHERE conversation_id=CAST(:i AS uuid)"
                                 if table != "c06_conversations" else
                                 "SELECT count(*) FROM public.c06_conversations WHERE id=CAST(:i AS uuid)"), {"i": identity}) == 0
        assert c.scalar(text("SELECT count(*) FROM public.c06_results WHERE payload->>'conversation_id'=:i"), {"i": identity}) == 0
    insert = """INSERT INTO public.c06_question_intents(id,conversation_id,owner_id,request_key,fingerprint,context_revision,question)
        VALUES(CAST(:id AS uuid),CAST(:conv AS uuid),:owner,:key,:fp,'r','q')"""
    # Wrong owner under RLS, and own owner pointing at another owner's conversation (composite FK).
    for owner in ("employee", "outsider"):
        with pytest.raises(UsageError):
            with stranger.connection() as c:
                c.execute(text(insert), dict(id=str(uuid4()), conv=identity, owner=owner, key="x-" + uuid4().hex, fp="a" * 64))


def test_runtime_role_has_only_the_granted_privileges_and_cannot_change_schema(lifecycle, engines):
    _, service, _, _ = lifecycle
    with service.store.connection() as c:
        for table, allowed in {"c06_results": {"SELECT", "INSERT"}, "c06_conversations": {"SELECT", "INSERT"},
                               "c06_turns": {"SELECT", "INSERT"}, "c06_question_intents": {"SELECT", "INSERT"},
                               "c06_result_sources": {"SELECT"}}.items():
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                granted = c.scalar(text("SELECT has_table_privilege(current_user,:t,:p)"), {"t": "public." + table, "p": privilege})
                assert granted == (privilege in allowed), (table, privilege)
    for statement in ("CREATE TABLE public.c06_rogue(x int)", "ALTER TABLE public.c06_question_intents DISABLE ROW LEVEL SECURITY",
                      "DROP TABLE public.c06_question_intents", "DROP TRIGGER refuse_mutation ON public.c06_question_intents"):
        with pytest.raises(UsageError):
            with service.store.connection() as c:
                c.execute(text(statement))


def test_hostile_text_is_stored_verbatim_inert_and_absent_from_central(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls = create(service), []
    hostile = f"'; DROP TABLE c06_turns; -- <script>alert(1)</script> {CANARY}"
    result = ask(svc, identity, "ask-" + uuid4().hex, make_process(identity, calls), question=hostile)
    assert result["status"] == "answered"
    page = service.messages(identity)["items"][0]
    assert page["question"] == hostile and page["content_status"] == "available"
    assert count(service, "c06_turns", identity) == 1  # the tables are intact
    assert CANARY not in central_dump(central)


def test_process_failure_text_never_reaches_the_caller_central_or_store(lifecycle):
    svc, service, central, _ = lifecycle
    identity, key = create(service), "ask-" + uuid4().hex

    def failing(c, command):
        raise RuntimeError(f"provider password={CANARY} host=db.internal")
    result = ask(svc, identity, key, failing)
    assert result["status"] == "uncertain" and CANARY not in repr(result)
    assert CANARY not in central_dump(central)
    assert count(service, "c06_results", identity) == 0 and count(service, "c06_turns", identity) == 0
    # Replay stays uncertain and never dispatches again.
    again = ask(svc, identity, key, failing)
    assert again["status"] == "uncertain" and CANARY not in repr(again)


def test_source_outside_conversation_scope_is_saved_but_never_linked_or_shown(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    inner = make_process(identity, calls)

    def foreign_source(c, command):
        prepared = inner(c, command)
        prepared.payload["sources"][0]["department_id"] = "44444444-4444-4444-8444-444444444444"
        return prepared
    with pytest.raises(UsageError) as error:
        ask(svc, identity, key, foreign_source)
    assert error.value.status == 409
    assert count(service, "c06_turns", identity) == 0 and service.messages(identity)["items"] == []
    assert debit(central) == -1  # work was performed: no silent refund


@pytest.mark.parametrize("question,key", [("   ", "ok-key"), ("x" * 4001, "ok-key"), ("سؤال", "bad key"),
                                         ("سؤال", "k'; DROP TABLE x;--"), ("سؤال", "k" * 81), ("سؤال", "")])
def test_invalid_commands_are_rejected_before_any_effect(lifecycle, question, key):
    svc, service, central, _ = lifecycle
    identity, calls = create(service), []
    with pytest.raises(UsageError) as error:
        ask(svc, identity, key, make_process(identity, calls), question=question)
    assert error.value.status == 422
    no_effects(service, central, identity, calls)


def test_malformed_conversation_identifier_is_refused_without_effects(lifecycle):
    svc, service, central, _ = lifecycle
    calls = []
    with pytest.raises(UsageError) as error:
        ask(svc, "not-a-uuid'; --", "ask-" + uuid4().hex, make_process("x", calls))
    assert error.value.status == 422 and calls == [] and debit(central) == 0


def test_disabled_policy_and_exhausted_quota_block_dispatch_but_not_replay(lifecycle):
    svc, service, central, _ = lifecycle
    identity, calls, key = create(service), [], "ask-" + uuid4().hex
    configure(central, monthly_questions=1)
    ask(svc, identity, key, make_process(identity, calls))
    with pytest.raises(UsageError):
        ask(svc, identity, "ask-" + uuid4().hex, make_process(identity, calls))
    assert len(calls) == 1 and debit(central) == -1
    replay = ask(svc, identity, key, make_process(identity, calls))
    assert replay["status"] == "answered" and replay["replayed"] and len(calls) == 1
    configure(central, enabled=False)
    with pytest.raises(UsageError):
        ask(svc, identity, "ask-" + uuid4().hex, make_process(identity, calls))
    assert len(calls) == 1 and count(service, "c06_turns", identity) == 1
