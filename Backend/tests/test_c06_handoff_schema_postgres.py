"""H1: human-handoff schema (c06_0005) on a fresh isolated cluster: row-level security matrix, insert-only
history, database-enforced state machine and sequences, concurrency, privileges. Counts and lookups are scoped by
handoff id because the C06 store is module-scoped and immutable."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.usage.service import UsageError
from test_c06_result_store_postgres import engines, store, record, pytestmark  # noqa: F401

DEPT, OTHER_DEPT = "dept-a", "dept-b"
TABLES = ("c06_handoffs", "c06_handoff_events", "c06_handoff_messages", "c06_handoff_reads", "c06_handoff_access_log")
RLS, CHECK, FK, UNIQUE = "42501", "23514", "23503", "23505"
# BEFORE ROW triggers run before RLS checks, so a message from a non-assignee is refused by the guard first.
DENIED = {RLS, CHECK}


@contextmanager
def acting(runtime, user, staff=(), audit=False):
    """What trusted application code does per transaction: set the actor and the RLS context."""
    with runtime.begin() as c:
        c.execute(text("SELECT set_config('app.c06_user_id', :u, true), set_config('app.c06_staff_departments', :s, true),"
                       " set_config('app.c06_audit', :a, true)"), dict(u=user, s=",".join(staff), a="on" if audit else ""))
        yield c


def refused(runtime, user, statement, params=None, *, state, **context):
    with pytest.raises(DBAPIError) as error:
        with acting(runtime, user, **context) as c:
            c.execute(text(statement), params or {})
    allowed = {state} if isinstance(state, str) else set(state)
    assert error.value.orig.sqlstate in allowed, error.value.orig
    return error.value


def make_turn(runtime, user="employee"):
    """A conversation with one answered turn owned by `user` (the handoff's source)."""
    item = record(user=user)
    store(runtime, user=user).save_once(item)
    conversation = str(uuid4())
    with acting(runtime, user) as c:
        c.execute(text("""INSERT INTO public.c06_conversations(id,owner_id,creation_key,mode,departments)
            VALUES (CAST(:i AS uuid),:o,:k,'department',CAST(:d AS jsonb))"""),
            dict(i=conversation, o=user, k="conv-" + uuid4().hex, d=f'["{DEPT}"]'))
        c.execute(text("""INSERT INTO public.c06_turns(id,conversation_id,owner_id,sequence,request_id,fingerprint,question)
            VALUES (CAST(:id AS uuid),CAST(:c AS uuid),:o,1,CAST(:r AS uuid),:fp,'سؤال')"""),
            dict(id=str(uuid4()), c=conversation, o=user, r=item.request_id, fp=item.fingerprint))
    return conversation


def new_handoff(runtime, user="employee", *, conversation=None, department=DEPT, key=None, summary="ملخص الاستفسار"):
    conversation = conversation or make_turn(runtime, user)
    identity = str(uuid4())
    with acting(runtime, user) as c:
        c.execute(text("""INSERT INTO public.c06_handoffs(id,requester_id,department_id,conversation_id,turn_sequence,request_key,summary)
            VALUES (CAST(:id AS uuid),:u,:d,CAST(:c AS uuid),1,:k,:s)"""),
            dict(id=identity, u=user, d=department, c=conversation, k=key or "ho-" + uuid4().hex, s=summary))
    return identity


def event(runtime, user, handoff, kind, *, assignee=None, staff=(DEPT,), requester="employee", department=DEPT):
    with acting(runtime, user, staff=staff) as c:
        c.execute(text("""INSERT INTO public.c06_handoff_events(id,handoff_id,requester_id,department_id,kind,actor_id,assignee_id)
            VALUES (gen_random_uuid(),CAST(:h AS uuid),:r,:d,:k,:a,:assignee)"""),
            dict(h=handoff, r=requester, d=department, k=kind, a=user, assignee=assignee))


def message(runtime, user, handoff, body, role, *, staff=(), requester="employee", department=DEPT):
    with acting(runtime, user, staff=staff) as c:
        return c.execute(text("""INSERT INTO public.c06_handoff_messages(id,handoff_id,requester_id,department_id,author_id,author_role,body)
            VALUES (gen_random_uuid(),CAST(:h AS uuid),:r,:d,:a,:role,:b) RETURNING sequence"""),
            dict(h=handoff, r=requester, d=department, a=user, role=role, b=body)).scalar_one()


def latest(runtime, handoff):
    with acting(runtime, "employee") as c:
        return c.execute(text("SELECT kind,assignee_id,seq FROM public.c06_handoff_events WHERE handoff_id=CAST(:h AS uuid) "
                              "ORDER BY seq DESC LIMIT 1"), {"h": handoff}).one()


def claimed(runtime, staff_user="head", **kw):
    handoff = new_handoff(runtime, **kw)
    event(runtime, staff_user, handoff, "claimed", assignee=staff_user)
    return handoff


# ------------------------------------------------------------------ creation

def test_creating_a_handoff_writes_its_created_event_and_enforces_uniqueness(engines):
    _, runtime = engines
    conversation = make_turn(runtime)
    key = "ho-" + uuid4().hex
    handoff = new_handoff(runtime, conversation=conversation, key=key)
    assert tuple(latest(runtime, handoff)) == ("created", None, 1)
    with acting(runtime, "employee") as c:
        assert c.scalar(text("SELECT summary FROM public.c06_handoffs WHERE id=CAST(:h AS uuid)"), {"h": handoff}) == "ملخص الاستفسار"
    clash = """INSERT INTO public.c06_handoffs(id,requester_id,department_id,conversation_id,turn_sequence,request_key,summary)
        VALUES (gen_random_uuid(),'employee',:d,CAST(:c AS uuid),1,:k,'x')"""
    refused(runtime, "employee", clash, dict(d=OTHER_DEPT, c=conversation, k=key), state=UNIQUE)        # same key
    refused(runtime, "employee", clash, dict(d=DEPT, c=conversation, k="other-" + uuid4().hex), state=UNIQUE)  # same turn+dept
    other_department = new_handoff(runtime, conversation=conversation, department=OTHER_DEPT)
    assert tuple(latest(runtime, other_department))[0] == "created"
    with_summary = clash.replace("'x'", ":s")
    for bad in ("", "   ", "x" * 4001):
        refused(runtime, "employee", with_summary,
                dict(d="dept-c", c=conversation, k="k-" + uuid4().hex, s=bad), state=CHECK)


def test_a_handoff_must_point_at_the_requesters_own_conversation_turn(engines):
    _, runtime = engines
    mine = make_turn(runtime, "employee")
    insert = """INSERT INTO public.c06_handoffs(id,requester_id,department_id,conversation_id,turn_sequence,request_key,summary)
        VALUES (gen_random_uuid(),:u,:d,CAST(:c AS uuid),:n,:k,'x')"""
    refused(runtime, "intruder", insert, dict(u="intruder", d=DEPT, c=mine, n=1, k="k-" + uuid4().hex), state=FK)  # not their conversation
    refused(runtime, "employee", insert, dict(u="employee", d=DEPT, c=mine, n=2, k="k-" + uuid4().hex), state=FK)  # no such turn
    refused(runtime, "intruder", insert, dict(u="employee", d=DEPT, c=mine, n=1, k="k-" + uuid4().hex), state=RLS)  # claims to be the owner


# ------------------------------------------------------------------ visibility

def test_visibility_matrix_requester_staff_other_department_and_audit(engines):
    _, runtime = engines
    handoff = new_handoff(runtime)
    event(runtime, "head", handoff, "claimed", assignee="head")
    message(runtime, "employee", handoff, "سؤال إضافي", "requester")
    count = {t: "SELECT count(*) FROM public.%s WHERE handoff_id=CAST(:h AS uuid)" % t for t in
             ("c06_handoff_events", "c06_handoff_messages")}
    count["c06_handoffs"] = "SELECT count(*) FROM public.c06_handoffs WHERE id=CAST(:h AS uuid)"
    expected = {  # (user, staff, audit) -> visible rows per table
        ("employee", (), False): (1, 2, 1), ("head", (DEPT,), False): (1, 2, 1), ("deputy", (DEPT,), False): (1, 2, 1),
        ("stranger", (), False): (0, 0, 0), ("head-b", (OTHER_DEPT,), False): (0, 0, 0),
        ("head", (), False): (0, 0, 0),            # staff rights come only from the trusted context
        ("auditor", (), True): (1, 2, 1)}
    for (user, staff, audit), (handoffs, events, messages) in expected.items():
        with acting(runtime, user, staff=staff, audit=audit) as c:
            got = tuple(c.scalar(text(count[t]), {"h": handoff}) for t in ("c06_handoffs", "c06_handoff_events", "c06_handoff_messages"))
        assert got == (handoffs, events, messages), (user, staff, audit)


# ------------------------------------------------------------------ insert policies

@pytest.mark.parametrize("case", ["requester_claims", "stranger_claims", "staff_wrong_department", "actor_spoof",
                                  "requester_posts_as_staff", "staff_posts_as_requester", "stranger_posts",
                                  "message_author_spoof", "audit_cannot_write"])
def test_insert_policies_refuse_every_unauthorized_write(engines, case):
    _, runtime = engines
    handoff = claimed(runtime)
    ev = """INSERT INTO public.c06_handoff_events(id,handoff_id,requester_id,department_id,kind,actor_id,assignee_id)
        VALUES (gen_random_uuid(),CAST(:h AS uuid),'employee',:d,'transferred',:a,'deputy')"""
    msg = """INSERT INTO public.c06_handoff_messages(id,handoff_id,requester_id,department_id,author_id,author_role,body)
        VALUES (gen_random_uuid(),CAST(:h AS uuid),'employee',:d,:a,:r,'نص')"""
    attempts = {
        "requester_claims": ("employee", ev, dict(h=handoff, d=DEPT, a="employee"), {}),
        "stranger_claims": ("stranger", ev, dict(h=handoff, d=DEPT, a="stranger"), {}),
        "staff_wrong_department": ("head-b", ev, dict(h=handoff, d=DEPT, a="head-b"), dict(staff=(OTHER_DEPT,))),
        "actor_spoof": ("head", ev, dict(h=handoff, d=DEPT, a="deputy"), dict(staff=(DEPT,))),
        "requester_posts_as_staff": ("employee", msg, dict(h=handoff, d=DEPT, a="employee", r="staff"), {}),
        "staff_posts_as_requester": ("head", msg, dict(h=handoff, d=DEPT, a="head", r="requester"), dict(staff=(DEPT,))),
        "stranger_posts": ("stranger", msg, dict(h=handoff, d=DEPT, a="stranger", r="requester"), {}),
        "message_author_spoof": ("employee", msg, dict(h=handoff, d=DEPT, a="head", r="requester"), {}),
        "audit_cannot_write": ("auditor", msg, dict(h=handoff, d=DEPT, a="auditor", r="staff"), dict(audit=True)),
    }
    user, statement, params, context = attempts[case]
    state = RLS if statement is ev else DENIED
    refused(runtime, user, statement, params, state=state, **context)


# ------------------------------------------------------------------ state machine and messages

def test_state_machine_accepts_only_the_defined_transitions(engines):
    _, runtime = engines
    handoff = new_handoff(runtime)
    for illegal in ("closed", "transferred", "reopened", "created"):
        with pytest.raises(DBAPIError) as error:
            event(runtime, "head", handoff, illegal, assignee="deputy" if illegal == "transferred" else None)
        assert error.value.orig.sqlstate in DENIED, illegal
    event(runtime, "head", handoff, "claimed", assignee="head")
    with pytest.raises(DBAPIError) as error:
        event(runtime, "deputy", handoff, "claimed", assignee="deputy")  # already claimed
    assert error.value.orig.sqlstate == CHECK and "transition refused" in str(error.value.orig)
    event(runtime, "head", handoff, "transferred", assignee="deputy")
    event(runtime, "deputy", handoff, "transferred", assignee="head")
    event(runtime, "head", handoff, "closed")
    for illegal in ("claimed", "transferred", "closed"):
        with pytest.raises(DBAPIError):
            event(runtime, "head", handoff, illegal, assignee="head" if illegal != "closed" else None)
    event(runtime, "head", handoff, "reopened")
    assert tuple(latest(runtime, handoff)) == ("reopened", None, 6)
    event(runtime, "deputy", handoff, "claimed", assignee="deputy")
    assert tuple(latest(runtime, handoff)) == ("claimed", "deputy", 7)
    # An assignee is required exactly for claim/transfer, even when the transition itself is allowed.
    refused(runtime, "head", """INSERT INTO public.c06_handoff_events(id,handoff_id,requester_id,department_id,kind,actor_id)
        VALUES (gen_random_uuid(),CAST(:h AS uuid),'employee',:d,'transferred','head')""",
            dict(h=handoff, d=DEPT), state=CHECK, staff=(DEPT,))


def test_sequences_are_assigned_by_the_database_and_cannot_be_forced(engines):
    _, runtime = engines
    handoff = new_handoff(runtime)
    with acting(runtime, "head", staff=(DEPT,)) as c:
        c.execute(text("""INSERT INTO public.c06_handoff_events(id,handoff_id,requester_id,department_id,seq,kind,actor_id,assignee_id)
            VALUES (gen_random_uuid(),CAST(:h AS uuid),'employee',:d,99,'claimed','head','head')"""), dict(h=handoff, d=DEPT))
    assert latest(runtime, handoff)[2] == 2  # the forced value was ignored
    first = message(runtime, "employee", handoff, "أولى", "requester")
    second = message(runtime, "head", handoff, "ثانية", "staff", staff=(DEPT,))
    third = message(runtime, "employee", handoff, "ثالثة", "requester")
    assert (first, second, third) == (1, 2, 3)


def test_message_rules_assignee_closed_and_reopened(engines):
    _, runtime = engines
    handoff = new_handoff(runtime)
    assert message(runtime, "employee", handoff, "معلومة إضافية", "requester") == 1       # requester may write while new
    with pytest.raises(DBAPIError) as error:
        message(runtime, "head", handoff, "رد", "staff", staff=(DEPT,))                    # nobody claimed it yet
    assert error.value.orig.sqlstate == CHECK and "assignee" in str(error.value.orig)
    event(runtime, "head", handoff, "claimed", assignee="head")
    with pytest.raises(DBAPIError):
        message(runtime, "deputy", handoff, "رد", "staff", staff=(DEPT,))                  # not the assignee
    assert message(runtime, "head", handoff, "رد", "staff", staff=(DEPT,)) == 2
    event(runtime, "head", handoff, "closed")
    for user, role, staff in (("employee", "requester", ()), ("head", "staff", (DEPT,))):
        with pytest.raises(DBAPIError) as error:
            message(runtime, user, handoff, "بعد الإغلاق", role, staff=staff)
        assert error.value.orig.sqlstate == CHECK and "closed" in str(error.value.orig)
    event(runtime, "head", handoff, "reopened")
    assert message(runtime, "employee", handoff, "بعد إعادة الفتح", "requester") == 3
    with pytest.raises(DBAPIError):
        message(runtime, "head", handoff, "رد بلا استلام", "staff", staff=(DEPT,))          # reopened means unassigned
    for body in ("", "   ", "x" * 8001):
        with pytest.raises(DBAPIError) as error:
            message(runtime, "employee", handoff, body, "requester")
        assert error.value.orig.sqlstate == CHECK


# ------------------------------------------------------------------ read receipts

def unread(runtime, handoff, user, staff=()):
    with acting(runtime, user, staff=staff) as c:
        return c.scalar(text("""SELECT count(*) FROM public.c06_handoff_messages m WHERE m.handoff_id=CAST(:h AS uuid)
            AND m.author_id<>:u AND m.sequence > COALESCE((SELECT max(up_to_sequence) FROM public.c06_handoff_reads r
                WHERE r.handoff_id=m.handoff_id AND r.reader_id=:u),0)"""), dict(h=handoff, u=user))


def receipt(runtime, user, handoff, up_to, *, staff=(), requester="employee", reader=None):
    with acting(runtime, user, staff=staff) as c:
        c.execute(text("""INSERT INTO public.c06_handoff_reads(id,handoff_id,requester_id,department_id,reader_id,up_to_sequence)
            VALUES (gen_random_uuid(),CAST(:h AS uuid),:r,:d,:u,:n)"""),
            dict(h=handoff, r=requester, d=DEPT, u=reader or user, n=up_to))


def test_unread_counts_are_derived_from_private_bounded_receipts(engines):
    _, runtime = engines
    handoff = claimed(runtime)
    message(runtime, "head", handoff, "رد 1", "staff", staff=(DEPT,))
    message(runtime, "head", handoff, "رد 2", "staff", staff=(DEPT,))
    message(runtime, "employee", handoff, "شكرًا", "requester")
    assert unread(runtime, handoff, "employee") == 2 and unread(runtime, handoff, "head", (DEPT,)) == 1
    receipt(runtime, "employee", handoff, 2)
    assert unread(runtime, handoff, "employee") == 0 and unread(runtime, handoff, "head", (DEPT,)) == 1
    receipt(runtime, "head", handoff, 3, staff=(DEPT,))
    assert unread(runtime, handoff, "head", (DEPT,)) == 0
    message(runtime, "head", handoff, "رد 3", "staff", staff=(DEPT,))
    assert unread(runtime, handoff, "employee") == 1
    with acting(runtime, "head", staff=(DEPT,)) as c:  # one's receipts are invisible to everyone else
        assert c.scalar(text("SELECT count(*) FROM public.c06_handoff_reads WHERE handoff_id=CAST(:h AS uuid)"), {"h": handoff}) == 1
    with acting(runtime, "employee") as c:
        assert c.scalar(text("SELECT count(*) FROM public.c06_handoff_reads WHERE handoff_id=CAST(:h AS uuid)"), {"h": handoff}) == 1
    for statement in ("past_last", "someone_else", "stranger"):
        with pytest.raises(DBAPIError) as error:
            if statement == "past_last":
                receipt(runtime, "employee", handoff, 99)
            elif statement == "someone_else":
                receipt(runtime, "employee", handoff, 1, reader="head")
            else:
                receipt(runtime, "stranger", handoff, 1)
        assert error.value.orig.sqlstate in {CHECK, RLS}, statement


# ------------------------------------------------------------------ audit log, immutability, privileges

def test_access_log_is_written_and_read_only_in_audit_mode(engines):
    _, runtime = engines
    handoff = new_handoff(runtime)
    insert = """INSERT INTO public.c06_handoff_access_log(id,actor_id,action,handoff_id,department_id)
        VALUES (gen_random_uuid(),:a,:k,CAST(:h AS uuid),:d)"""
    with acting(runtime, "auditor", audit=True) as c:
        c.execute(text(insert), dict(a="auditor", k="read", h=handoff, d=DEPT))
        c.execute(text(insert), dict(a="auditor", k="list", h=None, d=DEPT))
        assert c.scalar(text("SELECT count(*) FROM public.c06_handoff_access_log WHERE actor_id='auditor'")) >= 2
    refused(runtime, "employee", insert, dict(a="employee", k="read", h=handoff, d=DEPT), state=RLS)          # not audit mode
    refused(runtime, "auditor", insert, dict(a="someone-else", k="read", h=handoff, d=DEPT), state=RLS, audit=True)  # spoofed actor
    refused(runtime, "auditor", insert, dict(a="auditor", k="edit", h=handoff, d=DEPT), state=CHECK, audit=True)   # unknown action
    with acting(runtime, "employee", staff=(DEPT,)) as c:
        assert c.scalar(text("SELECT count(*) FROM public.c06_handoff_access_log")) == 0


@pytest.mark.parametrize("table", TABLES)
def test_history_is_insert_only_even_for_the_row_owner(engines, table):
    _, runtime = engines
    new_handoff(runtime)
    with acting(runtime, "auditor", audit=True) as c:
        c.execute(text("""INSERT INTO public.c06_handoff_access_log(id,actor_id,action) VALUES (gen_random_uuid(),'auditor','list')"""))
    statements = (f"UPDATE public.{table} SET id=id", f"DELETE FROM public.{table}", f"TRUNCATE public.{table}")
    for statement in statements:
        refused(runtime, "employee", statement, state=RLS, staff=(DEPT,), audit=True)


def test_runtime_role_holds_only_select_and_insert_on_the_handoff_tables(engines):
    _, runtime = engines
    with acting(runtime, "employee") as c:
        for table in TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                granted = c.scalar(text("SELECT has_table_privilege(current_user,:t,:p)"), {"t": "public." + table, "p": privilege})
                assert granted == (privilege in {"SELECT", "INSERT"}), (table, privilege)


def test_database_objects_are_in_place(engines):
    admin, _ = engines
    with admin.connect() as c:
        flags = dict(c.execute(text("SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class "
                                    "WHERE relname = ANY(:t) AND relkind='r'"), {"t": list(TABLES)}).all())
        assert flags == {table: True for table in TABLES}
        triggers = set(c.execute(text("""SELECT tgrelid::regclass::text || ':' || tgname FROM pg_trigger
            WHERE NOT tgisinternal AND tgrelid::regclass::text = ANY(:t)"""), {"t": list(TABLES)}).scalars())
        for table in TABLES:
            assert f"{table}:refuse_mutation" in triggers
        assert {"c06_handoffs:created_event", "c06_handoff_events:event_guard", "c06_handoff_messages:message_guard",
                "c06_handoff_reads:read_guard"} <= triggers


# ------------------------------------------------------------------ concurrency and the store API

def test_concurrent_claims_have_exactly_one_winner(engines):
    _, runtime = engines
    handoff = new_handoff(runtime)
    barrier = Barrier(2)

    def attempt(user):
        barrier.wait()
        try:
            event(runtime, user, handoff, "claimed", assignee=user)
            return "won"
        except DBAPIError as error:
            return error.orig.sqlstate
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(attempt, ["head", "deputy"]))
    assert outcomes == [CHECK, "won"]
    assert latest(runtime, handoff)[0] == "claimed" and latest(runtime, handoff)[2] == 2


def test_store_connection_carries_the_context_and_maps_rule_violations(engines):
    _, runtime = engines
    handoff = new_handoff(runtime)
    staff_store = store(runtime, user="head")
    with staff_store.connection(staff_departments=(DEPT, DEPT, OTHER_DEPT)) as c:
        assert c.scalar(text("SELECT current_setting('app.c06_staff_departments')")) == f"{DEPT},{OTHER_DEPT}"
        assert c.scalar(text("SELECT count(*) FROM public.c06_handoffs WHERE id=CAST(:h AS uuid)"), {"h": handoff}) == 1
    with staff_store.connection() as c:  # the default context grants nothing
        assert c.scalar(text("SELECT current_setting('app.c06_staff_departments')")) == ""
        assert c.scalar(text("SELECT count(*) FROM public.c06_handoffs WHERE id=CAST(:h AS uuid)"), {"h": handoff}) == 0
    with store(runtime, user="auditor").connection(audit=True) as c:
        assert c.scalar(text("SELECT current_setting('app.c06_audit')")) == "on"
    for bad in (("a,b",), ("a b",), ("",), ("x" * 37,), (1,), ("../etc",)):
        with pytest.raises(UsageError) as error:
            staff_store.connection(staff_departments=bad).__enter__()
        assert error.value.status == 422
    with pytest.raises(UsageError) as error:
        staff_store.connection(audit="on").__enter__()
    assert error.value.status == 422
    insert = text("""INSERT INTO public.c06_handoff_events(id,handoff_id,requester_id,department_id,kind,actor_id)
        VALUES (gen_random_uuid(),CAST(:h AS uuid),'employee',:d,'closed','head')""")
    with pytest.raises(UsageError) as error:
        with staff_store.connection(staff_departments=(DEPT,)) as c:
            c.execute(insert, dict(h=handoff, d=DEPT))  # created -> closed is not a transition
    assert (error.value.status, error.value.code) == (409, "handoff_rule_violation")
