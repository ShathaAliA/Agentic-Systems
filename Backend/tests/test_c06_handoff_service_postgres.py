"""H2: HandoffService on the real C06 store (fresh isolated cluster) with a synthetic Central.

The C06 store is module-scoped and immutable, so every test builds its own users and department ids: nothing
here counts or lists across tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.handoffs import access
from app.handoffs.service import HandoffService
from app.models import (Base, Department, DepartmentMembership, Permission, Role, RoleAssignment, RolePermission, User)
from app.usage import models as usage_models  # noqa: F401  (registers the usage tables)
from app.usage.models import UsageEntry, UsagePolicy, UsageRequest
from test_c06_result_store_postgres import engines, store, record, pytestmark  # noqa: F401

NOW = datetime(2026, 1, 15, 9, tzinfo=timezone.utc)


@contextmanager
def acting(runtime, user):
    with runtime.begin() as c:
        c.execute(text("SELECT set_config('app.c06_user_id', :u, true), set_config('app.c06_staff_departments', '', true),"
                       " set_config('app.c06_audit', '', true)"), dict(u=user))
        yield c


@pytest.fixture
def world(engines, tmp_path):
    _, runtime = engines
    tag = uuid4().hex[:8]
    w = SimpleNamespace(runtime=runtime, tag=tag, dept="d" + tag, other="o" + tag, **{n: f"{n}-{tag}" for n in
                        ("emp", "emp2", "head", "deputy", "outsider", "ghost", "admin")})
    # A file database: handoff service calls run in threads and must not share one SQLite connection.
    w.central = create_engine("sqlite:///" + (tmp_path / "central.sqlite").as_posix(), connect_args={"check_same_thread": False})
    Base.metadata.create_all(w.central)
    with Session(w.central) as s:
        for who in (w.emp, w.emp2, w.head, w.deputy, w.outsider, w.ghost, w.admin):
            s.add(User(id=who, name=who, email=who + "@example.test", status="inactive" if who == w.ghost else "active"))
        s.add_all([Department(id=w.dept, name="Dept " + w.dept), Department(id=w.other, name="Dept " + w.other)])
        s.flush()
        perms = {code: Permission(id=str(uuid4()), code=code) for code in (access.REQUEST, access.HANDLE, access.AUDIT)}
        roles = {name: Role(id=str(uuid4()), name=f"{name}-{tag}", scope=scope) for name, scope in
                 (("requester", "department"), ("handler", "department"), ("audit", "global"), ("global-handler", "global"))}
        s.add_all([*perms.values(), *roles.values()])
        s.flush()
        for name, code in (("requester", access.REQUEST), ("handler", access.HANDLE), ("audit", access.AUDIT),
                           ("global-handler", access.HANDLE)):
            s.add(RolePermission(role_id=roles[name].id, permission_id=perms[code].id))
        w.roles = {name: role.id for name, role in roles.items()}
        for who, dept in ((w.emp, w.dept), (w.emp2, w.dept), (w.head, w.dept), (w.deputy, w.dept), (w.ghost, w.dept),
                          (w.outsider, w.other)):
            s.add(DepartmentMembership(user_id=who, department_id=dept))
        for who in (w.emp, w.emp2, w.ghost):
            s.add(RoleAssignment(user_id=who, role_id=w.roles["requester"], department_id=w.dept))
        for who in (w.head, w.deputy):
            s.add(RoleAssignment(user_id=who, role_id=w.roles["handler"], department_id=w.dept))
        s.add(RoleAssignment(user_id=w.outsider, role_id=w.roles["handler"], department_id=w.other))
        s.add_all([RoleAssignment(user_id=w.admin, role_id=w.roles["audit"], department_id=None),
                   RoleAssignment(user_id=w.admin, role_id=w.roles["global-handler"], department_id=None)])
        s.commit()
    w.svc = lambda user, **kw: HandoffService(store(runtime, user=user), w.central, now=lambda: NOW, **kw)
    w.turn = lambda user, *outcomes, dept=None: make_conversation(runtime, user, dept or w.dept, outcomes or ("no_answer",))
    yield w
    w.central.dispose()


def make_conversation(runtime, user, dept, outcomes):
    conversation = str(uuid4())
    with acting(runtime, user) as c:
        c.execute(text("""INSERT INTO public.c06_conversations(id,owner_id,creation_key,mode,departments)
            VALUES (CAST(:i AS uuid),:o,:k,'department',CAST(:d AS jsonb))"""),
            dict(i=conversation, o=user, k="conv-" + uuid4().hex, d=f'["{dept}"]'))
    for sequence, outcome in enumerate(outcomes, 1):
        item = record(user=user, outcome=outcome)
        store(runtime, user=user).save_once(item)
        with acting(runtime, user) as c:
            c.execute(text("""INSERT INTO public.c06_turns(id,conversation_id,owner_id,sequence,request_id,fingerprint,question)
                VALUES (CAST(:id AS uuid),CAST(:c AS uuid),:o,:q,CAST(:r AS uuid),:fp,'سؤال')"""),
                dict(id=str(uuid4()), c=conversation, o=user, q=sequence, r=item.request_id, fp=item.fingerprint))
    return conversation


def make(w, user=None, *, key=None, sequence=1, summary="ملخص الاستفسار", **kw):
    user = user or w.emp
    conversation = kw.pop("conversation", None) or w.turn(user)
    return w.svc(user).create(conversation, sequence, kw.pop("dept", w.dept), summary, key or "k-" + uuid4().hex), conversation


def refused(call, status, code):
    with pytest.raises(Exception) as error:
        call()
    assert (error.value.status, error.value.code) == (status, code), (error.value.status, error.value.code)


def test_create_returns_new_state_and_replays_idempotently(world):
    w = world
    created, conversation = make(w, key="same-key")
    assert (created["status"], created["role"], created["unread"], created["replayed"]) == ("new", "requester", 0, False)
    assert created["department_id"] == w.dept and created["conversation_id"] == conversation
    again = w.svc(w.emp).create(conversation, 1, w.dept, "ملخص الاستفسار", "same-key")
    assert again["handoff_id"] == created["handoff_id"] and again["replayed"] is True
    refused(lambda: w.svc(w.emp).create(conversation, 1, w.dept, "ملخص مختلف", "same-key"), 409, "handoff_creation_conflict")


def test_create_rules(world):
    w = world
    conversation = w.turn(w.emp, "answered", "no_answer")
    svc = w.svc(w.emp)
    refused(lambda: svc.create(conversation, 1, w.dept, "ملخص", "a-" + uuid4().hex), 409, "handoff_turn_answered")
    refused(lambda: svc.create(conversation, 9, w.dept, "ملخص", "a-" + uuid4().hex), 404, "turn_not_found")
    refused(lambda: svc.create(conversation, 2, w.other, "ملخص", "a-" + uuid4().hex), 403, "handoff_request_forbidden")
    refused(lambda: svc.create(str(uuid4()), 1, w.dept, "ملخص", "a-" + uuid4().hex), 404, "conversation_not_found")
    refused(lambda: svc.create("not-a-uuid", 1, w.dept, "ملخص", "a-" + uuid4().hex), 404, "conversation_not_found")
    refused(lambda: w.svc(w.emp2).create(conversation, 2, w.dept, "ملخص", "a-" + uuid4().hex), 404, "conversation_not_found")
    for bad in ("", "   ", "x" * 4001, "نص\x00خفي", "نص‮معكوس", 5):
        refused(lambda: svc.create(conversation, 2, w.dept, bad, "a-" + uuid4().hex), 422, "invalid_handoff_summary")
    refused(lambda: svc.create(conversation, 0, w.dept, "ملخص", "a-" + uuid4().hex), 422, "invalid_handoff_command")
    refused(lambda: svc.create(conversation, 2, w.dept, "ملخص", "bad key"), 422, "invalid_handoff_command")
    refused(lambda: w.svc(w.ghost).create(conversation, 2, w.dept, "ملخص", "a-" + uuid4().hex), 403, "handoff_actor_inactive")
    first = svc.create(conversation, 2, w.dept, "ملخص", "first-" + uuid4().hex)
    refused(lambda: svc.create(conversation, 2, w.dept, "ملخص", "second-" + uuid4().hex), 409, "handoff_already_exists")
    assert first["status"] == "new"


def test_open_cap_counts_only_open_handoffs(world):
    w = world
    capped = w.svc(w.emp, open_cap=lambda: 2)
    made = [capped.create(w.turn(w.emp), 1, w.dept, "ملخص", f"cap-{i}-{uuid4().hex}") for i in range(2)]
    refused(lambda: capped.create(w.turn(w.emp), 1, w.dept, "ملخص", "cap-3-" + uuid4().hex), 429, "handoff_open_limit")
    head = w.svc(w.head)
    head.claim(made[0]["handoff_id"])
    head.close(made[0]["handoff_id"])
    capped.create(w.turn(w.emp), 1, w.dept, "ملخص", "cap-4-" + uuid4().hex)  # a closed one frees its place
    assert w.svc(w.emp).create(w.turn(w.emp), 1, w.dept, "ملخص", "cap-5-" + uuid4().hex)["status"] == "new"  # default is 5


def test_exhausted_credit_allows_only_the_last_unanswered_turn(world):
    w = world
    with Session(w.central) as s:
        s.add(UsagePolicy(id=1, enabled=True, timezone="Asia/Riyadh", monthly_questions=2, daily_questions=9, rate_count=9,
                          rate_seconds=60, monthly_budget_micro_usd=1000, version=1))
        for n in (1, 2):  # two completed questions this month: the credit of two is used up
            s.add(UsageRequest(id=f"req-{n}-{w.tag}", user_id=w.emp, request_key=f"r{n}", fingerprint="f" * 64, policy_version=1,
                               timezone="Asia/Riyadh", question_month="2026-01", question_day="2026-01-15", state="completed",
                               outcome="answered", estimated_micro_usd=1, held_micro_usd=0))
        s.flush()
        for n in (1, 2):
            s.add(UsageEntry(request_id=f"req-{n}-{w.tag}", user_id=w.emp, event_key="completed", event="completed",
                             question_month="2026-01", question_day="2026-01-15", question_delta=-1))
        s.commit()
    conversation = w.turn(w.emp, "no_answer", "no_answer")
    svc = w.svc(w.emp)
    refused(lambda: svc.create(conversation, 1, w.dept, "ملخص", "c1-" + uuid4().hex), 429, "handoff_credit_exhausted")
    assert svc.create(conversation, 2, w.dept, "ملخص", "c2-" + uuid4().hex)["status"] == "new"
    # Human messages never consume credit: replying works with the credit exhausted.
    created = svc.create(w.turn(w.emp), 1, w.dept, "ملخص", "c3-" + uuid4().hex)
    assert svc.post_message(created["handoff_id"], "رسالة")["sequence"] == 1


def test_visibility_matrix(world):
    w = world
    created, _ = make(w)
    identity = created["handoff_id"]
    assert w.svc(w.head).get(identity)["role"] == "staff" and w.svc(w.deputy).get(identity)["role"] == "staff"
    assert "conversation_id" not in w.svc(w.head).get(identity)  # the department never sees the agent conversation
    for stranger in (w.emp2, w.outsider, w.admin, w.ghost):
        refused(lambda: w.svc(stranger).get(identity), 404 if stranger != w.ghost else 403,
                "handoff_not_found" if stranger != w.ghost else "handoff_actor_inactive")
    refused(lambda: w.svc(w.head).get(str(uuid4())), 404, "handoff_not_found")
    refused(lambda: w.svc(w.head).get("nope"), 404, "handoff_not_found")
    assert [h["handoff_id"] for h in w.svc(w.head).list_inbox()["items"]] == [identity]
    assert w.svc(w.outsider).list_inbox()["items"] == []
    assert w.svc(w.admin).list_inbox()["items"] == []  # a global handler role never makes Admin a handler
    assert w.svc(w.emp2).list_inbox()["items"] == [] and w.svc(w.emp2).list_mine()["items"] == []
    assert [h["handoff_id"] for h in w.svc(w.emp).list_mine()["items"]] == [identity]
    assert w.svc(w.emp).list_inbox()["items"] == []
    refused(lambda: w.svc(w.head).list_inbox(department_id=w.other), 403, "handoff_department_forbidden")
    refused(lambda: w.svc(w.head).list_inbox(status="open"), 422, "invalid_handoff_status")
    refused(lambda: w.svc(w.emp).claim(identity), 403, "handoff_handler_required")


def test_reply_claims_first_then_only_the_assignee_replies(world):
    w = world
    created, _ = make(w)
    identity = created["handoff_id"]
    employee, head, deputy = w.svc(w.emp), w.svc(w.head), w.svc(w.deputy)
    assert employee.post_message(identity, "سؤال إضافي")["author_role"] == "requester"
    assert head.get(identity)["unread"] == 1 and employee.get(identity)["unread"] == 0
    assert head.post_message(identity, "تفضل")["sequence"] == 2  # replying claims first, atomically
    assert head.get(identity)["status"] == "claimed" and head.get(identity)["assignee_id"] == w.head
    refused(lambda: deputy.post_message(identity, "أنا أيضاً"), 409, "handoff_not_assignee")
    refused(lambda: deputy.claim(identity), 409, "handoff_not_claimable")
    assert head.claim(identity)["assignee_id"] == w.head  # replay by the assignee
    assert employee.get(identity)["unread"] == 1
    assert employee.mark_read(identity)["up_to_sequence"] == 2 and employee.get(identity)["unread"] == 0
    refused(lambda: employee.mark_read(identity, 3), 422, "invalid_read_position")
    assert head.mark_read(identity, 2)["up_to_sequence"] == 2 and head.get(identity)["unread"] == 0
    page = employee.messages(identity, limit=1)
    assert [m["body"] for m in page["items"]] == ["سؤال إضافي"] and page["next_cursor"] == 1 and page["items"][0]["mine"]
    rest = employee.messages(identity, after=page["next_cursor"])
    assert [(m["author_role"], m["mine"]) for m in rest["items"]] == [("staff", False)] and rest["next_cursor"] is None
    for bad in ("", "x" * 8001, "a\x07b"):
        refused(lambda: employee.post_message(identity, bad), 422, "invalid_handoff_message")
    refused(lambda: w.svc(w.emp2).post_message(identity, "تطفل"), 404, "handoff_not_found")


def test_transfer_close_reopen_lifecycle(world):
    w = world
    created, _ = make(w)
    identity = created["handoff_id"]
    employee, head, deputy = w.svc(w.emp), w.svc(w.head), w.svc(w.deputy)
    refused(lambda: head.transfer(identity, w.deputy), 409, "handoff_not_transferable")  # not claimed yet
    refused(lambda: head.close(identity), 409, "handoff_not_closable")
    head.claim(identity)
    for target in (w.head, w.emp, w.outsider, w.emp2, "nobody"):
        refused(lambda: head.transfer(identity, target), 422, "handoff_transfer_target_invalid")
    refused(lambda: deputy.transfer(identity, w.head), 403, "handoff_not_assignee")
    refused(lambda: deputy.close(identity), 403, "handoff_not_assignee")
    assert head.transfer(identity, w.deputy)["assignee_id"] == w.deputy
    refused(lambda: head.post_message(identity, "لم أعد المسؤول"), 409, "handoff_not_assignee")
    assert deputy.post_message(identity, "أكمل أنا")["sequence"] == 1
    assert deputy.close(identity)["status"] == "closed"
    for call in (lambda: employee.post_message(identity, "بعد الإغلاق"), lambda: deputy.post_message(identity, "بعد الإغلاق")):
        refused(call, 409, "handoff_closed")
    refused(lambda: deputy.claim(identity), 409, "handoff_not_claimable")
    refused(lambda: employee.claim(identity), 403, "handoff_handler_required")
    assert [h["handoff_id"] for h in head.list_inbox(status="closed")["items"]] == [identity]
    assert head.list_inbox(status="new")["items"] == []
    refused(lambda: employee.reopen(identity), 403, "handoff_handler_required")
    reopened = head.reopen(identity)
    assert reopened["status"] == "new" and reopened["assignee_id"] is None
    refused(lambda: head.reopen(identity), 409, "handoff_not_reopenable")
    assert employee.post_message(identity, "عودة")["sequence"] == 2
    assert head.claim(identity)["assignee_id"] == w.head


def test_concurrent_claims_have_exactly_one_winner(world):
    w = world
    created, _ = make(w)
    barrier = Barrier(2)

    def claim(user):
        service = w.svc(user)
        barrier.wait()
        try:
            return service.claim(created["handoff_id"])["assignee_id"]
        except Exception as error:
            return (error.status, error.code)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(claim, (w.head, w.deputy)))
    winners = [r for r in results if isinstance(r, str)]
    losers = [r for r in results if isinstance(r, tuple)]
    assert len(winners) == 1 and len(losers) == 1 and losers[0][0] == 409, results
    assert w.svc(w.head).get(created["handoff_id"])["assignee_id"] == winners[0]


def test_revoked_handler_loses_access_immediately_and_a_colleague_can_take_over(world):
    w = world
    created, _ = make(w)
    identity = created["handoff_id"]
    w.svc(w.head).claim(identity)
    with Session(w.central) as s:
        for row in s.query(RoleAssignment).filter_by(user_id=w.head).all():
            s.delete(row)
        s.commit()
    refused(lambda: w.svc(w.head).get(identity), 404, "handoff_not_found")
    refused(lambda: w.svc(w.head).post_message(identity, "رد"), 404, "handoff_not_found")
    assert w.svc(w.head).list_inbox()["items"] == []
    # The assignee is no longer a handler, so the other handler may close or transfer it.
    assert w.svc(w.deputy).close(identity)["status"] == "closed"
    # A suspended user is refused outright.
    with Session(w.central) as s:
        s.get(User, w.deputy).status = "inactive"
        s.commit()
    refused(lambda: w.svc(w.deputy).get(identity), 403, "handoff_actor_inactive")


def test_requester_permission_is_rechecked_for_new_messages(world):
    w = world
    created, _ = make(w)
    with Session(w.central) as s:
        for row in s.query(RoleAssignment).filter_by(user_id=w.emp).all():
            s.delete(row)
        s.commit()
    refused(lambda: w.svc(w.emp).post_message(created["handoff_id"], "رسالة"), 403, "handoff_request_forbidden")
    assert w.svc(w.emp).get(created["handoff_id"])["status"] == "new"  # history stays readable


def test_listing_pages_in_order_and_rejects_foreign_cursors(world):
    w = world
    made = [make(w)[0]["handoff_id"] for _ in range(3)]
    inbox = w.svc(w.head)
    first = inbox.list_inbox(limit=2)
    assert [h["handoff_id"] for h in first["items"]] == made[:2] and first["next_cursor"] == made[1]
    assert [h["handoff_id"] for h in inbox.list_inbox(limit=2, after=first["next_cursor"])["items"]] == made[2:]
    refused(lambda: w.svc(w.outsider).list_inbox(after=made[0]), 404, "handoff_not_found")
    refused(lambda: inbox.list_inbox(limit=0), 422, "invalid_page_limit")
    assert w.svc(w.emp).list_mine(limit=3)["next_cursor"] is None


def feed(w, user, **kw):
    from app.handoffs import notifications
    return notifications.feed(w.svc(user), **kw)


def count(w, user):
    from app.handoffs import notifications
    return notifications.unread_count(w.svc(user))["unread_handoffs"]


def test_notification_feed_follows_the_whole_lifecycle(world):
    w = world
    created, _ = make(w)
    identity = created["handoff_id"]
    summary = lambda user: [(i["kind"], i["unread_messages"], i["role"]) for i in feed(w, user)["items"]]
    assert summary(w.emp) == [] and count(w, w.emp) == 0  # your own request is not news to you
    assert summary(w.head) == [("new_request", 0, "staff")] == summary(w.deputy)
    for stranger in (w.outsider, w.admin, w.emp2):
        assert summary(stranger) == [] and count(w, stranger) == 0
    w.svc(w.head).post_message(identity, "تفضل")  # replying claims first
    assert summary(w.head) == [] and summary(w.deputy) == []  # the claimer's own act, and not theirs to handle
    assert summary(w.emp) == [("reply", 1, "requester")] and count(w, w.emp) == 1
    assert set(feed(w, w.emp)["items"][0]) == {"handoff_id", "department_id", "kind", "activity_at", "unread_messages", "role"}
    w.svc(w.emp).mark_read(identity)
    assert summary(w.emp) == [] and count(w, w.emp) == 0
    w.svc(w.emp).post_message(identity, "شكراً، سؤال آخر")
    assert summary(w.head) == [("reply", 1, "staff")]
    w.svc(w.head).mark_read(identity)
    assert summary(w.head) == []
    w.svc(w.head).transfer(identity, w.deputy)
    assert summary(w.deputy) == [("transferred", 2, "staff")] and summary(w.emp) == [("transferred", 0, "requester")]
    assert summary(w.head) == []
    w.svc(w.deputy).close(identity)
    assert summary(w.emp) == [("closed", 0, "requester")] and summary(w.deputy) == [] == summary(w.head)
    w.svc(w.emp).mark_read(identity)
    assert count(w, w.emp) == 0
    w.svc(w.head).reopen(identity)
    assert summary(w.emp) == [("reopened", 0, "requester")] and summary(w.deputy) == [("reopened", 2, "staff")]


def test_notification_feed_pages_newest_first_and_validates_cursor(world):
    w = world
    ids = []
    for _ in range(3):
        made, _ = make(w)
        w.svc(w.head).post_message(made["handoff_id"], "رد")
        ids.append(made["handoff_id"])
    page = feed(w, w.emp, limit=2)
    assert page["unread_handoffs"] == 3 and [i["handoff_id"] for i in page["items"]] == ids[::-1][:2]
    rest = feed(w, w.emp, limit=2, after=page["next_cursor"])
    assert [i["handoff_id"] for i in rest["items"]] == ids[:1] and rest["next_cursor"] is None
    for bad in ("x", "2026-01-01T00:00:00+00:00|nope", "|" + str(uuid4()), None.__class__.__name__):
        refused(lambda: feed(w, w.emp, after=bad), 422, "invalid_cursor")
    refused(lambda: feed(w, w.emp, limit=0), 422, "invalid_page_limit")
    refused(lambda: feed(w, w.ghost), 403, "handoff_actor_inactive")
