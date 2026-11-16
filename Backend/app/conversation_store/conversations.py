"""Owner-only metadata/history; question execution remains outside this service."""
from uuid import uuid4
import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import User
from app.services.authorization import has_permission
from app.usage.models import UsageRequest
from app.usage.question_adapter import QuestionCommand
from app.usage.service import UsageError
from app.conversation_store.postgres import canonical


class ConversationService:
    def __init__(self, store, central_engine):
        self.store, self.central = store, central_engine

    def scope_allowed(self, departments):
        with Session(self.central) as s:
            user = s.get(User, self.store.user_id)
            return (user is not None and user.status == "active" and
                    all(has_permission(s, user.id, "documents:read", d) for d in departments))

    def require_active(self):
        with Session(self.central) as s:
            user = s.get(User, self.store.user_id)
            if user is None or user.status != "active":
                raise UsageError(403, "conversation_owner_inactive")

    @staticmethod
    def metadata(row):
        return dict(conversation_id=str(row["id"]), mode=row["mode"],
                    department_ids=row["departments"], created_at=row["created_at"])

    def owned(self, c, identity):
        row = c.execute(text("SELECT * FROM public.c06_conversations WHERE id=:id AND owner_id=:owner"),
                        dict(id=identity, owner=self.store.user_id)).mappings().one_or_none()
        if row is None: raise UsageError(404, "conversation_not_found")
        return row

    def create(self, mode, departments, request_key):
        if (mode not in ("department", "orchestrator") or not isinstance(departments, (list, tuple))
                or not 1 <= len(departments) <= 20
                or any(type(d) is not str or not 1 <= len(d) <= 36 or not d.strip() for d in departments)
                or len(set(departments)) != len(departments)
                or (mode == "department" and len(departments) != 1)
                or type(request_key) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,79}", request_key)):
            raise UsageError(422, "invalid_conversation_command")
        scope = sorted(departments)
        if not self.scope_allowed(scope): raise UsageError(403, "conversation_scope_forbidden")
        with self.store.connection() as c:
            row = c.execute(text("""INSERT INTO public.c06_conversations(id,owner_id,creation_key,mode,departments)
                VALUES(:id,:owner,:key,:mode,CAST(:scope AS jsonb)) ON CONFLICT(owner_id,creation_key)
                DO NOTHING RETURNING *"""), dict(id=str(uuid4()), owner=self.store.user_id,
                key=request_key, mode=mode, scope=canonical(scope))).mappings().one_or_none()
            if row is None:
                row = c.execute(text("SELECT * FROM public.c06_conversations WHERE owner_id=:owner AND creation_key=:key"),
                                dict(owner=self.store.user_id, key=request_key)).mappings().one()
                if row["mode"] != mode or row["departments"] != scope:
                    raise UsageError(409, "conversation_creation_conflict")
            return self.metadata(row)

    def get(self, identity):
        self.require_active()
        with self.store.connection() as c:
            return self.metadata(self.owned(c, identity))

    def list(self, limit=20, after=None):
        self.require_active()
        if type(limit) is not int or not 1 <= limit <= 100: raise UsageError(422, "invalid_page_limit")
        with self.store.connection() as c:
            params = dict(owner=self.store.user_id, limit=limit + 1)
            condition = ""
            if after is not None:
                cursor = self.owned(c, after)  # Never use another owner's cursor row.
                condition = " AND (created_at,id)>(:time,CAST(:id AS uuid))"
                params.update(time=cursor["created_at"], id=after)
            rows = c.execute(text("SELECT * FROM public.c06_conversations WHERE owner_id=:owner" +
                                  condition + " ORDER BY created_at,id LIMIT :limit"), params).mappings().all()
            return dict(items=[self.metadata(r) for r in rows[:limit]],
                        next_cursor=str(rows[limit-1]["id"]) if len(rows) > limit else None)

    def completed(self, request_id, fingerprint, outcome=None):
        with Session(self.central) as s:
            row = s.get(UsageRequest, request_id)
            return bool(row and row.user_id == self.store.user_id and row.state == "completed"
                        and row.fingerprint == fingerprint and row.outcome in ("answered", "no_answer")
                        and (outcome is None or row.outcome == outcome))

    def append_completed(self, conversation_id, request_id, command: QuestionCommand):
        """Trusted internal path only, not a client message submission endpoint."""
        self.require_active()
        if not isinstance(command, QuestionCommand): raise UsageError(422, "validated_question_command_required")
        fingerprint = command.fingerprint()
        # Authorization/accounting checks precede loading the answer. No provider call.
        with self.store.connection() as c:
            conv = self.owned(c, conversation_id)
        if sorted(command.department_ids) != conv["departments"] or not self.scope_allowed(conv["departments"]):
            raise UsageError(403, "conversation_scope_forbidden")
        if not self.completed(request_id, fingerprint): raise UsageError(409, "conversation_result_not_completed")
        result = self.store.load_authorized(self.store.user_id, request_id)
        if result is None: raise UsageError(503, "question_result_unavailable")
        if (result.fingerprint != fingerprint or result.payload["conversation_id"] != str(conversation_id)
                or not self.completed(request_id, fingerprint, result.outcome)
                or any(s["department_id"] not in conv["departments"] for s in result.payload["sources"])):
            raise UsageError(409, "conversation_result_mismatch")
        with self.store.connection() as c:
            self.owned(c, conversation_id)
            if not self.scope_allowed(conv["departments"]) or not self.completed(request_id, fingerprint, result.outcome):
                raise UsageError(403, "conversation_scope_forbidden")
            # Serialize sequence allocation without granting UPDATE for row locks.
            c.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:id,0))"), {"id": str(conversation_id)})
            existing = c.execute(text("SELECT * FROM public.c06_turns WHERE request_id=:request"),
                                 {"request": request_id}).mappings().one_or_none()
            if existing:
                if (str(existing["conversation_id"]) != str(conversation_id) or existing["fingerprint"] != fingerprint
                        or existing["question"] != command.question):
                    raise UsageError(409, "conversation_link_conflict")
                return dict(turn_id=str(existing["id"]), sequence=existing["sequence"], replayed=True)
            row = c.execute(text("""INSERT INTO public.c06_turns
                (id,conversation_id,owner_id,sequence,request_id,fingerprint,question)
                SELECT CAST(:id AS uuid),CAST(:conversation AS uuid),:owner,COALESCE(MAX(sequence),0)+1,
                       CAST(:request AS uuid),:fp,:question
                FROM public.c06_turns WHERE conversation_id=:conversation RETURNING id,sequence"""),
                dict(id=str(uuid4()), conversation=str(conversation_id), owner=self.store.user_id,
                     request=request_id, fp=fingerprint, question=command.question)).one()
            return dict(turn_id=str(row.id), sequence=row.sequence, replayed=False)

    def messages(self, identity, limit=20, after=0):
        self.require_active()
        if type(limit) is not int or not 1 <= limit <= 100 or type(after) is not int or not 0 <= after <= 9223372036854775807:
            raise UsageError(422, "invalid_page_limit")
        with self.store.connection() as c:
            conv = self.owned(c, identity)
            rows = c.execute(text("""SELECT * FROM public.c06_turns WHERE conversation_id=:id AND owner_id=:owner
                AND sequence>:after ORDER BY sequence LIMIT :limit"""),
                dict(id=identity, owner=self.store.user_id, after=after, limit=limit+1)).mappings().all()
        items = []
        for row in rows[:limit]:
            item = dict(turn_id=str(row["id"]), sequence=row["sequence"], created_at=row["created_at"],
                        question=None, response=None, content_status="verification_unavailable")
            if not self.scope_allowed(conv["departments"]): item["content_status"] = "access_revoked"
            elif self.completed(str(row["request_id"]), row["fingerprint"]):
                try:
                    result = self.store.load_authorized(self.store.user_id, str(row["request_id"]))
                    if result and result.fingerprint == row["fingerprint"] and result.payload["conversation_id"] == str(identity):
                        if (self.completed(str(row["request_id"]), row["fingerprint"], result.outcome)
                                and self.scope_allowed(conv["departments"])
                                and all(s["department_id"] in conv["departments"] for s in result.payload["sources"])):
                            item.update(question=row["question"], response=result.payload, content_status="available")
                except UsageError as e:
                    if e.status == 403: item["content_status"] = "access_revoked"
            items.append(item)
        return dict(items=items, next_cursor=rows[limit-1]["sequence"] if len(rows)>limit else None)
