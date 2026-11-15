"""Typed external-result boundary; no storage engine, provider or document imports."""
from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol

from app.usage.processor import AccountingContext
from app.usage.question_adapter import QuestionCommand, QuestionResult, execute_question
from app.usage.service import UsageError

Outcome = Literal["answered", "no_answer"]


@dataclass(frozen=True)
class PreparedQuestionResult:
    outcome: Outcome
    payload: object = field(repr=False)


@dataclass(frozen=True)
class StoredQuestionResult:
    user_id: str
    request_id: str
    fingerprint: str
    outcome: Outcome
    payload: object = field(repr=False)


class ExternalResultStore(Protocol):
    """Trusted content-store implementation, never the Central usage ledger.

    save_once durably commits an immutable record outside Central before returning.
    The owner/request key is unique: conflicting writes must fail, never overwrite.
    load_authorized checks ownership AND current source/audience permissions for
    every delivery, including replay; return None if missing, raise 403 if revoked.
    DTO freezing is not persistence immutability or a source authorization check.
    """

    def save_once(self, result: StoredQuestionResult) -> None: ...

    def load_authorized(self, user_id: str, request_id: str) -> StoredQuestionResult | None: ...


def execute_with_result_store(engine, *, authenticated_user_id: str,
                              command: QuestionCommand, estimated_micro_usd: int,
                              authorize_content: Callable[[str, QuestionCommand], bool],
                              process: Callable[[AccountingContext], PreparedQuestionResult],
                              store: ExternalResultStore) -> QuestionResult:
    """Optional typed adapter; existing execute_question callbacks stay unchanged.

    process must not persist a second authoritative copy. No automatic reconciliation,
    refund or redispatch follows an uncertain save/finish. Provider receipts remain
    the processor's responsibility. Call this synchronous API off the async loop.
    """
    if not isinstance(command, QuestionCommand):
        raise UsageError(422, "validated_question_command_required")
    if not all(callable(getattr(store, name, None)) for name in ("save_once", "load_authorized")):
        raise UsageError(422, "external_result_store_required")
    fingerprint = command.fingerprint()

    def persist(context):
        prepared = process(context)
        if (not isinstance(prepared, PreparedQuestionResult) or prepared.outcome not in ("answered", "no_answer")
                or prepared.payload is None):
            raise ValueError("Invalid prepared question result")
        store.save_once(StoredQuestionResult(authenticated_user_id, context.request_id,
                                            fingerprint, prepared.outcome, prepared.payload))
        return prepared.outcome

    def load(user_id, request_id):
        record = store.load_authorized(user_id, request_id)
        if record is None:
            return None
        if (not isinstance(record, StoredQuestionResult) or record.user_id != user_id
                or record.request_id != request_id or record.fingerprint != fingerprint
                or record.outcome not in ("answered", "no_answer") or record.payload is None):
            raise UsageError(503, "question_result_integrity_error")
        return record

    result = execute_question(engine, authenticated_user_id=authenticated_user_id,
        command=command, estimated_micro_usd=estimated_micro_usd,
        authorize_content=authorize_content, process=persist, load_response=load)
    if result.response is None:
        return result
    record = result.response
    if record.outcome != result.receipt.outcome:
        raise UsageError(503, "question_result_integrity_error")
    return QuestionResult(result.receipt, record.payload)
