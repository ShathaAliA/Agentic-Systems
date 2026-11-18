"""Internal question lifecycle inside an owned conversation. No HTTP route, provider or
document import: process, authorize_content and the quote provider are injected."""
from typing import Callable
from uuid import uuid4

from sqlalchemy import text

from app.conversation_store.conversations import ConversationService
from app.usage.external_results import PreparedQuestionResult, execute_with_result_store
from app.usage.processor import AccountingContext
from app.usage.question_adapter import QuestionCommand
from app.usage.service import UsageError

_RECEIPT_STATUS = {"running": "processing", "reserved": "processing", "uncertain": "uncertain",
                   "refunded": "refunded"}


class QuestionLifecycleService:
    def __init__(self, conversations: ConversationService, central_engine, *,
                 quote_provider: Callable[[QuestionCommand], int] | None = None):
        self.conversations, self.central = conversations, central_engine
        self.quote_provider = quote_provider  # None disables questions; no default price.

    @property
    def store(self):
        return self.conversations.store

    def _intent(self, conversation_id, request_key, question):
        """Durably record the question once; replays reuse the stored context revision."""
        owner = self.store.user_id
        with self.store.connection() as c:
            conv = self.conversations.owned(c, conversation_id)
            if conv["mode"] != "department" or len(conv["departments"]) != 1:
                raise UsageError(403, "question_mode_unsupported")
            if not self.conversations.scope_allowed(conv["departments"]):
                raise UsageError(403, "conversation_scope_forbidden")
            # Same lock as append_completed so the revision cannot change under a new intent.
            c.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:id,0))"), {"id": str(conversation_id)})
            row = c.execute(text("SELECT * FROM public.c06_question_intents WHERE owner_id=:o AND request_key=:k"),
                            dict(o=owner, k=request_key)).mappings().one_or_none()
            if row is None:
                last = c.execute(text("SELECT COALESCE(MAX(sequence),0) FROM public.c06_turns WHERE conversation_id=:id"),
                                 {"id": str(conversation_id)}).scalar_one()
                revision = f"{conversation_id}:{last}"
                probe = QuestionCommand(request_key=request_key, question=question,
                                        department_ids=tuple(conv["departments"]), context_revision=revision)
                c.execute(text("""INSERT INTO public.c06_question_intents
                    (id,conversation_id,owner_id,request_key,fingerprint,context_revision,question)
                    VALUES(CAST(:id AS uuid),CAST(:conv AS uuid),:o,:k,:fp,:rev,:q)"""),
                    dict(id=str(uuid4()), conv=str(conversation_id), o=owner, k=request_key,
                         fp=probe.fingerprint(), rev=revision, q=question))
                return probe
            if str(row["conversation_id"]) != str(conversation_id) or row["question"] != question:
                raise UsageError(409, "question_request_conflict")
            command = QuestionCommand(request_key=request_key, question=question,
                                      department_ids=tuple(conv["departments"]),
                                      context_revision=row["context_revision"])
            if command.fingerprint() != row["fingerprint"]:
                raise UsageError(409, "question_request_conflict")
            return command

    def ask(self, conversation_id: str, request_key: str, question: str, *,
            authorize_content: Callable[[str, QuestionCommand], bool],
            process: Callable[[AccountingContext, QuestionCommand], PreparedQuestionResult]) -> dict:
        """Trusted synchronous call; run off the async loop. Identity is the store's bound user."""
        self.conversations.require_active()
        if self.quote_provider is None:
            raise UsageError(503, "question_quote_unavailable")
        if not callable(authorize_content) or not callable(process):
            raise UsageError(422, "question_callbacks_required")
        try:
            command = self._intent(conversation_id, request_key, question)
        except ValueError:
            raise UsageError(422, "invalid_question_command") from None
        quote = self.quote_provider(command)
        if type(quote) is not int or quote <= 0:
            raise UsageError(503, "question_quote_unavailable")

        def bound(context):
            prepared = process(context, command)
            payload = getattr(prepared, "payload", None)
            if not isinstance(payload, dict) or payload.get("conversation_id") != str(conversation_id):
                # Not saved; the adapter marks the request uncertain rather than refunding.
                raise ValueError("Result is not bound to this conversation")
            return prepared

        result = execute_with_result_store(self.central, authenticated_user_id=self.store.user_id,
            command=command, estimated_micro_usd=quote, authorize_content=authorize_content,
            process=bound, store=self.store)
        receipt = result.receipt
        if result.response is None:
            return dict(status=_RECEIPT_STATUS.get(receipt.state, receipt.state), request_id=receipt.request_id,
                        replayed=receipt.replayed, turn=None, response=None)
        link = self.conversations.append_completed(conversation_id, receipt.request_id, command)
        return dict(status=receipt.outcome, request_id=receipt.request_id, replayed=receipt.replayed,
                    turn=dict(sequence=link["sequence"], turn_id=link["turn_id"]), response=result.response)
