"""Owner-scoped recovery diagnostics for the question lifecycle.

Read-only report plus one idempotent repair (re-link a completed result). Nothing here
dispatches work, refunds, deletes or edits history, and no question/answer text is returned.
A global (all-owner) view needs audited Admin access and is intentionally not provided."""
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.conversation_store.conversations import ConversationService
from app.usage.models import UsageRequest
from app.usage.question_adapter import QuestionCommand
from app.usage.service import UsageError

_OPEN_STATES = {"reserved": "reserved", "running": "running", "uncertain": "uncertain", "refunded": "refunded"}


class QuestionRecovery:
    def __init__(self, conversations: ConversationService, central_engine):
        self.conversations, self.central = conversations, central_engine

    @property
    def store(self):
        return self.conversations.store

    def _requests(self, keys):
        if not keys:
            return {}
        with Session(self.central) as s:
            rows = s.query(UsageRequest).filter(UsageRequest.user_id == self.store.user_id,
                                                UsageRequest.request_key.in_(keys)).all()
            return {r.request_key: dict(id=r.id, state=r.state, outcome=r.outcome, fingerprint=r.fingerprint)
                    for r in rows}

    @staticmethod
    def _status(intent, request, has_result, has_turn):
        if request is None:
            return "not_started"  # refused or interrupted before admission: no charge, no dispatch
        if request["fingerprint"] != intent["fingerprint"]:
            return "integrity_error"
        if request["state"] in _OPEN_STATES:
            return _OPEN_STATES[request["state"]]
        if request["state"] != "completed" or request["outcome"] not in ("answered", "no_answer"):
            return "integrity_error"
        if not has_result:
            return "integrity_error"  # completed accounting without a stored result
        return "linked" if has_turn else "completed_unlinked"

    def report(self, limit: int = 100) -> dict:
        self.conversations.require_active()
        if type(limit) is not int or not 1 <= limit <= 500:
            raise UsageError(422, "invalid_page_limit")
        with self.store.connection() as c:
            intents = c.execute(text("""SELECT conversation_id,request_key,fingerprint,created_at
                FROM public.c06_question_intents WHERE owner_id=:o ORDER BY created_at,id LIMIT :n"""),
                dict(o=self.store.user_id, n=limit + 1)).mappings().all()
            truncated, intents = len(intents) > limit, intents[:limit]
            requests = self._requests([i["request_key"] for i in intents])
            ids = [str(r["id"]) for r in requests.values()]
            results = set(map(str, c.scalars(text("""SELECT request_id FROM public.c06_results
                WHERE owner_id=:o AND request_id = ANY(CAST(:ids AS uuid[]))"""), dict(o=self.store.user_id, ids=ids))))
            turns = set(map(str, c.scalars(text("""SELECT request_id FROM public.c06_turns
                WHERE owner_id=:o AND request_id = ANY(CAST(:ids AS uuid[]))"""), dict(o=self.store.user_id, ids=ids))))
        items, summary = [], {}
        for intent in intents:
            request = requests.get(intent["request_key"])
            request_id = str(request["id"]) if request else None
            status = self._status(intent, request, request_id in results, request_id in turns)
            summary[status] = summary.get(status, 0) + 1
            items.append(dict(conversation_id=str(intent["conversation_id"]), request_key=intent["request_key"],
                              request_id=request_id, status=status, created_at=intent["created_at"]))
        return dict(items=items, summary=summary, truncated=truncated)

    def relink(self, conversation_id: str, request_key: str) -> dict:
        """Link an already completed result to its turn. Never calls a processor or touches accounting."""
        try:
            conversation_id = str(UUID(str(conversation_id)))
        except ValueError:
            raise UsageError(422, "invalid_conversation_id") from None
        self.conversations.require_active()
        with self.store.connection() as c:
            conv = self.conversations.owned(c, conversation_id)
            intent = c.execute(text("""SELECT * FROM public.c06_question_intents
                WHERE owner_id=:o AND request_key=:k AND conversation_id=CAST(:c AS uuid)"""),
                dict(o=self.store.user_id, k=request_key, c=conversation_id)).mappings().one_or_none()
        if intent is None:
            raise UsageError(404, "question_intent_not_found")
        request = self._requests([request_key]).get(request_key)
        if request is None or request["state"] != "completed" or request["outcome"] not in ("answered", "no_answer"):
            raise UsageError(409, "conversation_result_not_completed")
        command = QuestionCommand(request_key=request_key, question=intent["question"],
                                  department_ids=tuple(conv["departments"]),
                                  context_revision=intent["context_revision"])
        if command.fingerprint() != intent["fingerprint"] or request["fingerprint"] != intent["fingerprint"]:
            raise UsageError(409, "question_request_conflict")
        return self.conversations.append_completed(conversation_id, str(request["id"]), command)
