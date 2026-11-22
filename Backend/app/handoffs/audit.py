"""Admin audit read (H5): read-only, every list and read logged in the same transaction before data is returned.

The actor needs the global `handoffs:audit` permission in Central (re-read per call). The C06 connection is opened in
audit mode, so row-level security shows every handoff but the Admin can still insert nothing except access-log rows."""
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.handoffs import access
from app.handoffs.notifications import parse_cursor
from app.handoffs.service import KINDS_BY_STATUS, STATUSES, VIEW, HandoffService, handoff_id_of, page_limit
from app.usage.service import UsageError


class HandoffAudit:
    def __init__(self, store, central_engine):
        self.store, self.central, self.user_id = store, central_engine, store.user_id

    def allow(self):
        with Session(self.central) as s:
            access.require_active(s, self.user_id)
            if not access.may_audit(s, self.user_id):
                raise UsageError(403, "handoff_audit_forbidden")

    def log(self, c, action, handoff_id=None, department_id=None):
        c.execute(text("""INSERT INTO public.c06_handoff_access_log(id,actor_id,action,handoff_id,department_id)
            VALUES (CAST(:id AS uuid),:a,:k,CAST(:h AS uuid),:d)"""),
            dict(id=str(uuid4()), a=self.user_id, k=action, h=handoff_id, d=department_id))

    def summary(self, row):
        return dict(handoff_id=str(row["id"]), department_id=row["department_id"], requester_id=row["requester_id"],
                    status=HandoffService.status(row), assignee_id=row["last_assignee_id"], summary=row["summary"],
                    created_at=row["created_at"], last_event_at=row["last_event_at"], message_count=row["last_sequence"])

    def row(self, c, identity):
        found = c.execute(text(VIEW + " WHERE h.id=CAST(:id AS uuid)"),
                          dict(id=handoff_id_of(identity), actor=self.user_id)).mappings().one_or_none()
        if found is None:
            raise UsageError(404, "handoff_not_found")
        return found

    def listing(self, department_id=None, status=None, limit=20, after=None):
        page_limit(limit)
        if status is not None and status not in STATUSES:
            raise UsageError(422, "invalid_handoff_status")
        if department_id is not None and (type(department_id) is not str or not 1 <= len(department_id) <= 36):
            raise UsageError(422, "invalid_handoff_command")
        self.allow()
        with self.store.connection(audit=True) as c:
            self.log(c, "list", None, department_id)
            where, params = "true", dict(actor=self.user_id, limit=limit + 1)
            if department_id is not None:
                where += " AND h.department_id=:department"
                params["department"] = department_id
            if status is not None:
                where += " AND e.kind = ANY(CAST(:kinds AS text[]))"
                params["kinds"] = KINDS_BY_STATUS[status]
            if after is not None:
                cursor = self.row(c, after)
                where += " AND (h.created_at,h.id)>(:ct,CAST(:ci AS uuid))"
                params.update(ct=cursor["created_at"], ci=str(cursor["id"]))
            rows = c.execute(text(VIEW + " WHERE " + where + " ORDER BY h.created_at,h.id LIMIT :limit"), params).mappings().all()
            return dict(items=[self.summary(r) for r in rows[:limit]],
                        next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None)

    def get(self, identity):
        self.allow()
        with self.store.connection(audit=True) as c:
            row = self.row(c, identity)
            self.log(c, "read", str(row["id"]), row["department_id"])
            events = c.execute(text("""SELECT seq,kind,actor_id,assignee_id,created_at FROM public.c06_handoff_events
                WHERE handoff_id=:h ORDER BY seq"""), dict(h=row["id"])).mappings().all()
            return {**self.summary(row), "events": [dict(e) for e in events]}

    def messages(self, identity, limit=50, after=0):
        page_limit(limit)
        if type(after) is not int or not 0 <= after <= 2**31 - 1:
            raise UsageError(422, "invalid_page_limit")
        self.allow()
        with self.store.connection(audit=True) as c:
            row = self.row(c, identity)
            self.log(c, "read", str(row["id"]), row["department_id"])
            rows = c.execute(text("""SELECT sequence,author_id,author_role,body,created_at FROM public.c06_handoff_messages
                WHERE handoff_id=:h AND sequence>:after ORDER BY sequence LIMIT :limit"""),
                dict(h=row["id"], after=after, limit=limit + 1)).mappings().all()
        return dict(items=[dict(r) for r in rows[:limit]], next_cursor=rows[limit - 1]["sequence"] if len(rows) > limit else None)

    def access_log(self, limit=50, after=None):
        page_limit(limit)
        cursor = parse_cursor(after) if after is not None else None
        self.allow()
        with self.store.connection(audit=True) as c:
            params = dict(limit=limit + 1)
            where = ""
            if cursor:
                where = " WHERE (created_at,id)<(:ct,CAST(:ci AS uuid))"
                params.update(ct=cursor[0], ci=cursor[1])
            rows = c.execute(text("SELECT id,actor_id,action,handoff_id,department_id,created_at FROM public.c06_handoff_access_log"
                                  + where + " ORDER BY created_at DESC,id DESC LIMIT :limit"), params).mappings().all()
        items = [dict(actor_id=r["actor_id"], action=r["action"], handoff_id=str(r["handoff_id"]) if r["handoff_id"] else None,
                      department_id=r["department_id"], created_at=r["created_at"]) for r in rows[:limit]]
        last = rows[limit - 1] if len(rows) > limit else None
        return dict(items=items, next_cursor=f"{last['created_at'].isoformat()}|{last['id']}" if last else None)
