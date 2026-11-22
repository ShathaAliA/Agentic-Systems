"""Notification feed (H4), derived entirely from handoff events, messages and read receipts: nothing extra is stored.

A handoff is *new* for the actor when someone else wrote a message after the actor's last read position, or someone
else's event (new request, claim, transfer, close, reopen) happened after the actor's last read receipt. Marking the
handoff read inserts a receipt and clears both. The feed carries no message text."""
from datetime import datetime
from uuid import UUID
import re

from sqlalchemy import text

from app.usage.service import UsageError

# Staff are only notified of what they can act on: unclaimed requests of their departments and handoffs assigned to
# them. A requester is notified about all of their own handoffs.
FEED = """
SELECT * FROM (
  SELECT s.*, CASE WHEN s.message_at IS NOT NULL AND (s.event_at IS NULL OR s.message_at >= s.event_at) THEN 'reply'
                   ELSE CASE s.event_kind WHEN 'created' THEN 'new_request' ELSE s.event_kind END END AS kind,
         GREATEST(s.message_at, s.event_at) AS activity_at
  FROM (
    SELECT h.id, h.department_id, h.requester_id, e.kind AS last_kind,
      CASE WHEN e.kind IN ('claimed','transferred') THEN (SELECT a.assignee_id FROM public.c06_handoff_events a
           WHERE a.handoff_id=h.id AND a.kind IN ('claimed','transferred') ORDER BY a.seq DESC LIMIT 1) END AS assignee_id,
      (SELECT count(*) FROM public.c06_handoff_messages m WHERE m.handoff_id=h.id AND m.author_id<>:actor
          AND m.sequence>rd.seq) AS unread_messages,
      (SELECT max(m.created_at) FROM public.c06_handoff_messages m WHERE m.handoff_id=h.id AND m.author_id<>:actor
          AND m.sequence>rd.seq) AS message_at,
      (SELECT v.kind FROM public.c06_handoff_events v WHERE v.handoff_id=h.id AND v.actor_id<>:actor
          AND v.created_at>rd.at ORDER BY v.seq DESC LIMIT 1) AS event_kind,
      (SELECT max(v.created_at) FROM public.c06_handoff_events v WHERE v.handoff_id=h.id AND v.actor_id<>:actor
          AND v.created_at>rd.at) AS event_at
    FROM public.c06_handoffs h
    JOIN LATERAL (SELECT kind FROM public.c06_handoff_events WHERE handoff_id=h.id ORDER BY seq DESC LIMIT 1) e ON true
    JOIN LATERAL (SELECT COALESCE(max(up_to_sequence),0) AS seq,
                  GREATEST(COALESCE(max(created_at),'-infinity'::timestamptz),
                    COALESCE((SELECT max(x.created_at) FROM public.c06_handoff_events x
                              WHERE x.handoff_id=h.id AND x.actor_id=:actor),'-infinity'::timestamptz),
                    COALESCE((SELECT max(x.created_at) FROM public.c06_handoff_messages x
                              WHERE x.handoff_id=h.id AND x.author_id=:actor),'-infinity'::timestamptz)) AS at
                  FROM public.c06_handoff_reads WHERE handoff_id=h.id AND reader_id=:actor) rd ON true
  ) s
  WHERE s.requester_id=:actor OR (s.department_id = ANY(CAST(:departments AS text[])) AND s.requester_id<>:actor
        AND (s.last_kind IN ('created','reopened') OR s.assignee_id=:actor))
) n WHERE n.activity_at IS NOT NULL
"""
CURSOR = re.compile(r"(?P<at>[0-9T:.+-]{20,40})\|(?P<id>[0-9a-fA-F-]{36})")


def parse_cursor(value):
    match = CURSOR.fullmatch(value or "")
    try:
        return datetime.fromisoformat(match["at"]), str(UUID(match["id"]))
    except (TypeError, ValueError):
        raise UsageError(422, "invalid_cursor") from None


def feed(service, limit=20, after=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise UsageError(422, "invalid_page_limit")
    cursor = parse_cursor(after) if after is not None else None
    staff = service.staff()
    params = dict(actor=service.user_id, departments=sorted(staff))
    with service.store.connection(staff_departments=staff) as c:
        total = c.execute(text("SELECT count(*) FROM (" + FEED + ") q"), params).scalar_one()
        query = FEED + (" AND (n.activity_at,n.id) < (:ct,CAST(:ci AS uuid))" if cursor else "")
        rows = c.execute(text(query + " ORDER BY n.activity_at DESC, n.id DESC LIMIT :limit"),
                         dict(params, limit=limit + 1, **({"ct": cursor[0], "ci": cursor[1]} if cursor else {}))).mappings().all()
    items = [dict(handoff_id=str(r["id"]), department_id=r["department_id"], kind=r["kind"],
                  activity_at=r["activity_at"], unread_messages=r["unread_messages"],
                  role="requester" if r["requester_id"] == service.user_id else "staff") for r in rows[:limit]]
    last = rows[limit - 1] if len(rows) > limit else None
    return dict(unread_handoffs=total, items=items,
                next_cursor=f"{last['activity_at'].isoformat()}|{last['id']}" if last else None)


def unread_count(service):
    staff = service.staff()
    with service.store.connection(staff_departments=staff) as c:
        return dict(unread_handoffs=c.execute(text("SELECT count(*) FROM (" + FEED + ") q"),
                                              dict(actor=service.user_id, departments=sorted(staff))).scalar_one())
