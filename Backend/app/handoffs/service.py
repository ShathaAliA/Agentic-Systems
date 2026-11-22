"""Human handoff service (H2).

Identity and department scope come from Central and are re-read on every call; content lives only in the C06
store. The database enforces the state machine, sequences and visibility (row-level security); this layer adds
the rules the database cannot know: who may act, the unanswered-turn rule, the open cap and the credit rule.
"""
from datetime import datetime
from typing import Callable
from uuid import UUID, uuid4
import re
import unicodedata

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.handoffs import access
from app.usage.service import UsageError, clock, count_questions, periods, policy

DEFAULT_OPEN_CAP = 5
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,79}")
DEPARTMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,35}")
STATUSES = ("new", "claimed", "closed")
KINDS_BY_STATUS = {"new": ["created", "reopened"], "claimed": ["claimed", "transferred"], "closed": ["closed"]}

# One query gives the whole state: the latest event decides, the assignee is the latest claim or transfer.
VIEW = """
SELECT h.id, h.requester_id, h.department_id, h.conversation_id, h.turn_sequence, h.request_key, h.summary,
       h.created_at, e.kind AS last_kind, e.created_at AS last_event_at,
       CASE WHEN e.kind IN ('claimed','transferred') THEN
            (SELECT a.assignee_id FROM public.c06_handoff_events a WHERE a.handoff_id=h.id
              AND a.kind IN ('claimed','transferred') ORDER BY a.seq DESC LIMIT 1) END AS assignee_id,
       (SELECT a.assignee_id FROM public.c06_handoff_events a WHERE a.handoff_id=h.id
         AND a.kind IN ('claimed','transferred') ORDER BY a.seq DESC LIMIT 1) AS last_assignee_id,
       COALESCE((SELECT max(m.sequence) FROM public.c06_handoff_messages m WHERE m.handoff_id=h.id),0) AS last_sequence,
       (SELECT count(*) FROM public.c06_handoff_messages m WHERE m.handoff_id=h.id AND m.author_id<>:actor
          AND m.sequence>COALESCE((SELECT max(r.up_to_sequence) FROM public.c06_handoff_reads r
              WHERE r.handoff_id=h.id AND r.reader_id=:actor),0)) AS unread
FROM public.c06_handoffs h
JOIN LATERAL (SELECT kind, created_at FROM public.c06_handoff_events WHERE handoff_id=h.id
              ORDER BY seq DESC LIMIT 1) e ON true
"""


def text_field(value, maximum, code):
    """Human text: trimmed, bounded, no control or format characters except line breaks and tabs."""
    if type(value) is not str:
        raise UsageError(422, code)
    value = value.strip()
    if (not 1 <= len(value) <= maximum
            or any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn") and ch not in "\n\r\t" for ch in value)):
        raise UsageError(422, code)
    return value


def handoff_id_of(value):
    try:
        return str(UUID(str(value)))
    except ValueError:
        raise UsageError(404, "handoff_not_found") from None


def page_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise UsageError(422, "invalid_page_limit")
    return limit


class HandoffService:
    """Bound to one authenticated user through its result store, like ConversationService."""

    def __init__(self, store, central_engine, *, open_cap: Callable[[], int] = lambda: DEFAULT_OPEN_CAP,
                 now: Callable[[], datetime] | None = None):
        self.store, self.central, self.open_cap, self.now = store, central_engine, open_cap, now
        self.user_id = store.user_id

    # -- Central ---------------------------------------------------------------------------------------------
    def staff(self) -> frozenset[str]:
        with Session(self.central) as s:
            access.require_active(s, self.user_id)
            return access.staff_departments(s, self.user_id)

    def target_staff(self, user_id) -> frozenset[str]:
        with Session(self.central) as s:
            return access.staff_departments(s, user_id)

    def credit_exhausted(self, session) -> bool:
        settings = policy(session)
        if settings is None or not settings.enabled:
            return False
        month, _ = periods(clock(self.now() if self.now else None), settings.timezone)
        return count_questions(session, self.user_id, month=month) >= settings.monthly_questions

    # -- views -----------------------------------------------------------------------------------------------
    @staticmethod
    def status(row):
        return {"closed": "closed", "claimed": "claimed", "transferred": "claimed"}.get(row["last_kind"], "new")

    def view(self, row, role):
        out = dict(handoff_id=str(row["id"]), department_id=row["department_id"], status=self.status(row),
                   role=role, summary=row["summary"], assignee_id=row["assignee_id"],
                   created_at=row["created_at"], last_event_at=row["last_event_at"],
                   last_sequence=row["last_sequence"], unread=row["unread"])
        if role == "requester":
            # The department never sees the employee's agent conversation, so only the requester gets its location.
            out.update(requester_id=row["requester_id"], conversation_id=str(row["conversation_id"]),
                       turn_sequence=row["turn_sequence"])
        else:
            out["requester_id"] = row["requester_id"]
        return out

    def load(self, c, identity, staff):
        row = c.execute(text(VIEW + " WHERE h.id=CAST(:id AS uuid)"),
                        dict(id=handoff_id_of(identity), actor=self.user_id)).mappings().one_or_none()
        if row is None:
            # Not visible and not existing look the same: existence is never revealed.
            raise UsageError(404, "handoff_not_found")
        return row, self.role(row, staff)

    def role(self, row, staff):
        if row["requester_id"] == self.user_id:
            return "requester"
        if row["department_id"] in staff:
            return "staff"
        raise UsageError(404, "handoff_not_found")

    def refresh(self, c, row, staff):
        return self.view(*self.load(c, row["id"], staff))

    @staticmethod
    def insert_event(c, row, kind, actor, assignee=None):
        c.execute(text("""INSERT INTO public.c06_handoff_events(id,handoff_id,requester_id,department_id,kind,actor_id,assignee_id)
            VALUES (CAST(:id AS uuid),:h,:r,:d,:k,:a,:s)"""),
            dict(id=str(uuid4()), h=row["id"], r=row["requester_id"], d=row["department_id"], k=kind, a=actor, s=assignee))

    def require_handler(self, row, role):
        if role != "staff":
            raise UsageError(403, "handoff_handler_required")

    def require_assignee(self, row):
        """The assignee acts; if they lost the handler role in Central, any current handler may take over."""
        who = row["assignee_id"]
        if who != self.user_id and row["department_id"] in self.target_staff(who):
            raise UsageError(403, "handoff_not_assignee")

    # -- employee --------------------------------------------------------------------------------------------
    def create(self, conversation_id, turn_sequence, department_id, summary, request_key):
        if (type(turn_sequence) is not int or not 1 <= turn_sequence <= 2**31 - 1
                or type(department_id) is not str or not DEPARTMENT.fullmatch(department_id)
                or type(request_key) is not str or not KEY.fullmatch(request_key)):
            raise UsageError(422, "invalid_handoff_command")
        summary = text_field(summary, 4000, "invalid_handoff_summary")
        try:
            conversation = str(UUID(str(conversation_id)))
        except ValueError:
            raise UsageError(404, "conversation_not_found") from None
        staff = self.staff()
        with Session(self.central) as s:
            access.require_active(s, self.user_id)
            if not access.may_request(s, self.user_id, department_id):
                raise UsageError(403, "handoff_request_forbidden")
            exhausted = self.credit_exhausted(s)
        cap = self.open_cap()
        with self.store.connection(staff_departments=staff) as c:
            c.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"), {"k": "c06_handoff_cap:" + self.user_id})
            existing = c.execute(text("SELECT id,conversation_id,turn_sequence,department_id,summary FROM public.c06_handoffs "
                                      "WHERE requester_id=:u AND request_key=:k"),
                                 dict(u=self.user_id, k=request_key)).mappings().one_or_none()
            if existing is not None:
                if (str(existing["conversation_id"]), existing["turn_sequence"], existing["department_id"],
                        existing["summary"]) != (conversation, turn_sequence, department_id, summary):
                    raise UsageError(409, "handoff_creation_conflict")
                return {**self.refresh(c, existing, staff), "replayed": True}
            owned = c.execute(text("SELECT departments FROM public.c06_conversations WHERE id=CAST(:i AS uuid) AND owner_id=:o"),
                              dict(i=conversation, o=self.user_id)).mappings().one_or_none()
            if owned is None:
                raise UsageError(404, "conversation_not_found")
            if department_id not in owned["departments"]:
                raise UsageError(422, "handoff_department_not_in_conversation")
            turn = c.execute(text("""SELECT r.outcome FROM public.c06_turns t JOIN public.c06_results r
                ON r.request_id=t.request_id AND r.owner_id=t.owner_id
                WHERE t.conversation_id=CAST(:i AS uuid) AND t.sequence=:q AND t.owner_id=:o"""),
                dict(i=conversation, q=turn_sequence, o=self.user_id)).mappings().one_or_none()
            if turn is None:
                raise UsageError(404, "turn_not_found")
            if turn["outcome"] != "no_answer":
                raise UsageError(409, "handoff_turn_answered")
            if exhausted:
                # Credit is used up: only the last unanswered question of the conversation may still be handed over.
                last = c.execute(text("SELECT max(sequence) FROM public.c06_turns WHERE conversation_id=CAST(:i AS uuid)"),
                                 dict(i=conversation)).scalar_one()
                if turn_sequence != last:
                    raise UsageError(429, "handoff_credit_exhausted")
            if c.execute(text("SELECT 1 FROM public.c06_handoffs WHERE conversation_id=CAST(:i AS uuid) "
                              "AND turn_sequence=:q AND department_id=:d"),
                         dict(i=conversation, q=turn_sequence, d=department_id)).first():
                raise UsageError(409, "handoff_already_exists")
            opened = c.execute(text("""SELECT count(*) FROM public.c06_handoffs h JOIN LATERAL (SELECT kind
                FROM public.c06_handoff_events WHERE handoff_id=h.id ORDER BY seq DESC LIMIT 1) e ON true
                WHERE h.requester_id=:u AND e.kind<>'closed'"""), dict(u=self.user_id)).scalar_one()
            if opened >= cap:
                raise UsageError(429, "handoff_open_limit")
            identity = str(uuid4())
            c.execute(text("""INSERT INTO public.c06_handoffs(id,requester_id,department_id,conversation_id,turn_sequence,request_key,summary)
                VALUES (CAST(:id AS uuid),:u,:d,CAST(:c AS uuid),:q,:k,:s)"""),
                dict(id=identity, u=self.user_id, d=department_id, c=conversation, q=turn_sequence, k=request_key, s=summary))
            return {**self.refresh(c, {"id": identity}, staff), "replayed": False}

    def get(self, identity):
        staff = self.staff()
        with self.store.connection(staff_departments=staff) as c:
            return self.view(*self.load(c, identity, staff))

    def listing(self, where, params, limit, after, staff):
        page_limit(limit)
        with self.store.connection(staff_departments=staff) as c:
            condition = ""
            if after is not None:
                cursor = self.load(c, after, staff)[0]  # only a handoff this actor may see can be a cursor
                condition = " AND (h.created_at,h.id)>(:ct,CAST(:ci AS uuid))"
                params = {**params, "ct": cursor["created_at"], "ci": str(cursor["id"])}
            rows = c.execute(text(VIEW + " WHERE " + where + condition + " ORDER BY h.created_at,h.id LIMIT :limit"),
                             dict(params, actor=self.user_id, limit=limit + 1)).mappings().all()
            return dict(items=[self.view(r, self.role(r, staff)) for r in rows[:limit]],
                        next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None)

    def list_mine(self, limit=20, after=None):
        return self.listing("h.requester_id=:actor", {}, limit, after, self.staff())

    def list_inbox(self, status=None, department_id=None, limit=20, after=None):
        staff = self.staff()
        if status is not None and status not in STATUSES:
            raise UsageError(422, "invalid_handoff_status")
        if department_id is not None and department_id not in staff:
            raise UsageError(403, "handoff_department_forbidden")
        departments = sorted(staff if department_id is None else {department_id})
        if not departments:
            page_limit(limit)
            return dict(items=[], next_cursor=None)
        where = "h.department_id = ANY(:departments) AND h.requester_id<>:actor"
        params = {"departments": departments}
        if status is not None:
            where += " AND e.kind = ANY(:kinds)"
            params["kinds"] = KINDS_BY_STATUS[status]
        return self.listing(where, params, limit, after, staff)

    def messages(self, identity, limit=50, after=0):
        page_limit(limit)
        if type(after) is not int or not 0 <= after <= 2**31 - 1:
            raise UsageError(422, "invalid_page_limit")
        staff = self.staff()
        with self.store.connection(staff_departments=staff) as c:
            row, _ = self.load(c, identity, staff)
            rows = c.execute(text("""SELECT sequence,author_id,author_role,body,created_at FROM public.c06_handoff_messages
                WHERE handoff_id=:h AND sequence>:after ORDER BY sequence LIMIT :limit"""),
                dict(h=row["id"], after=after, limit=limit + 1)).mappings().all()
        items = [dict(sequence=r["sequence"], author_role=r["author_role"], mine=r["author_id"] == self.user_id,
                      author_id=r["author_id"], body=r["body"], created_at=r["created_at"]) for r in rows[:limit]]
        return dict(items=items, next_cursor=rows[limit - 1]["sequence"] if len(rows) > limit else None)

    def post_message(self, identity, body):
        body = text_field(body, 8000, "invalid_handoff_message")
        staff = self.staff()
        with self.store.connection(staff_departments=staff) as c:
            row, role = self.load(c, identity, staff)
            if self.status(row) == "closed":
                raise UsageError(409, "handoff_closed")
            if role == "requester":
                with Session(self.central) as s:
                    if not access.may_request(s, self.user_id, row["department_id"]):
                        raise UsageError(403, "handoff_request_forbidden")
            elif self.status(row) == "new":
                self.insert_event(c, row, "claimed", self.user_id, self.user_id)  # replying claims first, atomically
            elif row["assignee_id"] != self.user_id:
                raise UsageError(409, "handoff_not_assignee")
            message = c.execute(text("""INSERT INTO public.c06_handoff_messages
                (id,handoff_id,requester_id,department_id,author_id,author_role,body)
                VALUES (CAST(:id AS uuid),:h,:r,:d,:a,:role,:b) RETURNING sequence,created_at"""),
                dict(id=str(uuid4()), h=row["id"], r=row["requester_id"], d=row["department_id"], a=self.user_id,
                     role="requester" if role == "requester" else "staff", b=body)).one()
            return dict(handoff_id=str(row["id"]), sequence=message.sequence, created_at=message.created_at,
                        author_role="requester" if role == "requester" else "staff")

    def mark_read(self, identity, up_to_sequence=None):
        staff = self.staff()
        with self.store.connection(staff_departments=staff) as c:
            row, _ = self.load(c, identity, staff)
            up_to = row["last_sequence"] if up_to_sequence is None else up_to_sequence
            if type(up_to) is not int or not 0 <= up_to <= row["last_sequence"]:
                raise UsageError(422, "invalid_read_position")
            c.execute(text("""INSERT INTO public.c06_handoff_reads(id,handoff_id,requester_id,department_id,reader_id,up_to_sequence)
                VALUES (CAST(:id AS uuid),:h,:r,:d,:u,:p)"""),
                dict(id=str(uuid4()), h=row["id"], r=row["requester_id"], d=row["department_id"], u=self.user_id, p=up_to))
            return dict(handoff_id=str(row["id"]), up_to_sequence=up_to)

    # -- department head / deputy -----------------------------------------------------------------------------
    def handled(self, c, identity, staff):
        row, role = self.load(c, identity, staff)
        self.require_handler(row, role)
        return row

    def claim(self, identity):
        staff = self.staff()
        with self.store.connection(staff_departments=staff) as c:
            row = self.handled(c, identity, staff)
            if self.status(row) == "claimed" and row["assignee_id"] == self.user_id:
                return self.refresh(c, row, staff)  # replay
            if self.status(row) != "new":
                raise UsageError(409, "handoff_not_claimable")
            self.insert_event(c, row, "claimed", self.user_id, self.user_id)
            return self.refresh(c, row, staff)

    def transfer(self, identity, to_user_id):
        if type(to_user_id) is not str or not 1 <= len(to_user_id) <= 36:
            raise UsageError(422, "invalid_handoff_command")
        staff = self.staff()
        with self.store.connection(staff_departments=staff) as c:
            row = self.handled(c, identity, staff)
            if self.status(row) != "claimed":
                raise UsageError(409, "handoff_not_transferable")
            self.require_assignee(row)
            if (to_user_id == self.user_id or to_user_id == row["requester_id"]
                    or row["department_id"] not in self.target_staff(to_user_id)):
                raise UsageError(422, "handoff_transfer_target_invalid")
            self.insert_event(c, row, "transferred", self.user_id, to_user_id)
            return self.refresh(c, row, staff)

    def close(self, identity):
        staff = self.staff()
        with self.store.connection(staff_departments=staff) as c:
            row = self.handled(c, identity, staff)
            if self.status(row) != "claimed":
                raise UsageError(409, "handoff_not_closable")
            self.require_assignee(row)
            self.insert_event(c, row, "closed", self.user_id)
            return self.refresh(c, row, staff)

    def reopen(self, identity):
        staff = self.staff()
        with self.store.connection(staff_departments=staff) as c:
            row = self.handled(c, identity, staff)
            if self.status(row) != "closed":
                raise UsageError(409, "handoff_not_reopenable")
            self.insert_event(c, row, "reopened", self.user_id)
            return self.refresh(c, row, staff)
