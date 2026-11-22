"""New cluster only; real store + synthetic Central/U1, never project databases."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import Mock, patch
import sqlite3

import pytest
from sqlalchemy import create_engine, text, select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from app.models import Department, DepartmentMembership, RoleAssignment
from app.conversation_store.conversations import ConversationService
from app.usage.external_results import PreparedQuestionResult, execute_with_result_store
from app.usage.models import UsageEntry
from app.usage.service import UsageError
from c06_store_fixtures import DEPARTMENT, answer
from test_c06_result_store_postgres import engines, store, pytestmark
from test_question_adapter import command
from test_usage import usage, configure


@pytest.fixture
def context(engines, usage, tmp_path):
    _, runtime = engines
    central, client, headers = usage
    configure(central)
    with Session(central) as s:
        s.add(Department(id=DEPARTMENT, name="Synthetic Conversation Department"))
        s.flush()
        s.add_all([DepartmentMembership(user_id="employee", department_id=DEPARTMENT),
                   RoleAssignment(user_id="employee", role_id="reader", department_id=DEPARTMENT)])
        s.commit()
    # Concurrent Central reads must not share StaticPool's single SQLite DBAPI
    # connection. Clone this synthetic fixture only; never a development database.
    path=tmp_path / "synthetic-central.sqlite"
    raw=central.raw_connection()
    try:
        with sqlite3.connect(path) as target: raw.driver_connection.backup(target)
    finally: raw.close()
    isolated=create_engine("sqlite:///"+path.as_posix(),connect_args={"check_same_thread":False})
    client.app.state.central_engine=isolated
    adapter=store(runtime)
    service=ConversationService(adapter, isolated)
    client.app.state.conversation_store_factory=lambda user: store(runtime, user=user)
    try:
        yield service, isolated, client, headers, runtime
    finally:
        client.app.state.central_engine=central
        isolated.dispose()


def create(service):
    return service.create("department", [DEPARTMENT], "create-"+uuid4().hex)["conversation_id"]


def result(service, central, identity, key=None):
    cmd=command(request_key=key or "ask-"+uuid4().hex, department_ids=(DEPARTMENT,), context_revision="conversation:"+identity)
    def process(c):
        payload=answer(c.request_id)
        payload["conversation_id"]=identity
        return PreparedQuestionResult("answered", payload)
    processed=execute_with_result_store(central, authenticated_user_id="employee", command=cmd,
        estimated_micro_usd=10, authorize_content=lambda *_: True, process=process, store=service.store)
    return processed.receipt.request_id, cmd


def test_owner_creation_idempotency_and_no_consumption(context):
    service, central, _, _, runtime=context
    key="create-"+uuid4().hex
    first=service.create("department", [DEPARTMENT], key)
    assert service.create("department", [DEPARTMENT], key) == first
    with pytest.raises(UsageError, match="creation_conflict"):
        service.create("orchestrator", [DEPARTMENT], key)
    other=ConversationService(store(runtime, user="admin"), central)
    with pytest.raises(UsageError, match="not_found"): other.get(first["conversation_id"])
    with Session(central) as s: assert s.scalar(select(func.count()).select_from(UsageEntry)) == 0


def test_concurrent_link_once_and_history_is_not_copied(context):
    service, central, _, _, _=context
    identity=create(service)
    request,cmd=result(service,central,identity)
    with ThreadPoolExecutor(max_workers=2) as pool:
        links=list(pool.map(lambda _: service.append_completed(identity,request,cmd), range(2)))
    assert {row["sequence"] for row in links} == {1}
    assert sorted(row["replayed"] for row in links) == [False,True]
    messages=service.messages(identity)
    assert len(messages["items"]) == 1 and messages["items"][0]["question"] == cmd.question
    assert messages["items"][0]["content_status"] == "available"
    with Session(central) as s: assert s.scalar(select(func.sum(UsageEntry.question_delta))) == -1
    with service.store.connection() as c:
        columns=set(c.scalars(text("SELECT column_name FROM information_schema.columns WHERE table_name='c06_turns'")))
        assert "question" in columns and not {"answer","payload"} & columns


def test_wrong_conversation_or_question_cannot_link(context):
    service, central, _, _, _=context
    first=create(service); second=create(service)
    request,cmd=result(service,central,first)
    with pytest.raises(UsageError, match="result_mismatch"): service.append_completed(second,request,cmd)
    changed=command(request_key=cmd.request_key, question="different question", department_ids=(DEPARTMENT,), context_revision=cmd.context_revision)
    with pytest.raises(UsageError, match="not_completed"): service.append_completed(first,request,changed)
    assert service.messages(first)["items"] == []


def test_link_failure_does_not_redispatch_or_add_charge(context,engines):
    service, central, _, _, runtime=context
    identity=create(service); request,cmd=result(service,central,identity)
    admin,_=engines
    with admin.begin() as c: c.execute(text("REVOKE INSERT ON c06_turns FROM c06_test_api"))
    try:
        with pytest.raises(UsageError,match="result_store_unavailable"):
            service.append_completed(identity,request,cmd)
    finally:
        with admin.begin() as c: c.execute(text("GRANT INSERT ON c06_turns TO c06_test_api"))
    assert service.messages(identity)["items"] == []
    service.append_completed(identity,request,cmd)
    service.append_completed(identity,request,cmd)
    with Session(central) as s: assert s.scalar(select(func.sum(UsageEntry.question_delta))) == -1


def test_independent_concurrent_turns_have_stable_sequence_pages(context):
    service,central,_,_,_=context
    identity=create(service)
    prepared=[result(service,central,identity) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        links=list(pool.map(lambda pair:service.append_completed(identity,*pair),prepared))
    assert sorted(r["sequence"] for r in links) == [1,2]
    for _ in range(3):
        with ThreadPoolExecutor(max_workers=2) as pool:
            repeated=list(pool.map(lambda pair:service.append_completed(identity,*pair),prepared))
        assert all(row["replayed"] for row in repeated)
    first=service.messages(identity,limit=1)
    second=service.messages(identity,limit=1,after=first["next_cursor"])
    assert first["items"][0]["sequence"] == 1 and second["items"][0]["sequence"] == 2
    assert first["next_cursor"] == 1 and second["next_cursor"] is None


@pytest.mark.parametrize("uncertain",[False,True])
def test_uncompleted_request_cannot_be_linked(context,uncertain):
    service,central,_,_,_=context
    identity=create(service)
    if uncertain:
        original=service.store.save_once
        def lose_ack(item): original(item); raise TimeoutError("synthetic lost ack")
        with patch.object(service.store,"save_once",side_effect=lose_ack):
            request,cmd=result(service,central,identity)
    else:
        request=str(uuid4()); cmd=command(department_ids=(DEPARTMENT,))
    with patch.object(service.store,"load_authorized",wraps=service.store.load_authorized) as load:
        with pytest.raises(UsageError,match="not_completed"):
            service.append_completed(identity,request,cmd)
        load.assert_not_called()
    assert service.messages(identity)["items"] == []


@pytest.mark.parametrize("state", ["revoked", "missing_validator"])
def test_history_hides_question_and_sources_without_changing_record(context,state):
    service, central, _, _, _=context
    identity=create(service); request,cmd=result(service,central,identity)
    service.append_completed(identity,request,cmd)
    service.store.authorize=(lambda *_:False) if state == "revoked" else None
    item=service.messages(identity)["items"][0]
    assert item["question"] is item["response"] is None
    assert item["content_status"] in ("access_revoked","verification_unavailable")
    with service.store.connection() as c:
        assert c.scalar(text("SELECT question FROM c06_turns WHERE request_id=:id"),{"id":request}) == cmd.question


def test_scope_revocation_denies_history_and_legal_creation(context):
    service, central, _, _, _=context
    identity=create(service); request,cmd=result(service,central,identity)
    service.append_completed(identity,request,cmd)
    with Session(central) as s:
        s.query(DepartmentMembership).filter_by(user_id="employee",department_id=DEPARTMENT).update({"status":"inactive"})
        s.commit()
    assert service.messages(identity)["items"][0]["content_status"] == "access_revoked"
    with pytest.raises(UsageError,match="scope_forbidden"): service.create("department",["legal"],"legal-denied")


def test_keyset_pagination_cannot_use_another_owner_cursor(context):
    service, central, _, _, runtime=context
    ids=[create(service) for _ in range(3)]
    other=ConversationService(store(runtime,user="admin"),central)
    foreign=create(other)
    found=[]; cursor=None
    for _ in range(30):
        page=service.list(limit=2,after=cursor)
        found.extend(x["conversation_id"] for x in page["items"])
        cursor=page["next_cursor"]
        if cursor is None: break
    assert cursor is None and set(ids) <= set(found)
    assert len(found) == len(set(found)) and foreign not in found
    with pytest.raises(UsageError,match="not_found"): service.list(after=foreign)
    with pytest.raises(UsageError,match="not_found"):
        ConversationService(store(runtime,user="outsider"),central).list(after=ids[0])


def test_turn_rows_are_immutable_even_through_sql(context):
    service, central, _, _, runtime=context
    identity=create(service); request,cmd=result(service,central,identity)
    service.append_completed(identity,request,cmd)
    for query in ("UPDATE c06_turns SET question='bad'", "DELETE FROM c06_turns", "TRUNCATE c06_turns"):
        with runtime.begin() as c, pytest.raises(DBAPIError) as error: c.execute(text(query))
        assert error.value.orig.sqlstate == "42501"


def test_real_http_routes_owner_only_validation_and_cache(context):
    _, _, client, headers, _=context
    body=dict(mode="department",department_ids=[DEPARTMENT],request_key="api-"+uuid4().hex)
    response=client.post("/api/conversations",json=body,headers={**headers["employee"],"X-User-ID":"admin"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    identity=response.json()["conversation_id"]
    assert client.get(f"/api/conversations/{identity}",headers=headers["employee"]).status_code == 200
    for user in ("outsider","admin"):
        assert client.get(f"/api/conversations/{identity}",headers=headers[user]).status_code == 404
    assert client.get(f"/api/conversations/{identity}/messages",headers=headers["employee"]).json() == dict(items=[],next_cursor=None)
    for query in ("limit=0","limit=101","after=not-a-uuid"):
        assert client.get("/api/conversations?"+query,headers=headers["employee"]).status_code == 422
    assert client.get(f"/api/conversations/{identity}/messages?after=9223372036854775808",
                      headers=headers["employee"]).status_code == 422
