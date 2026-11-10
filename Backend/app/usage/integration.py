"""U1 integration adapter for external services (documents).

Thin wrapper around `app.usage.service` — no duplicated accounting logic.
Uses `service.run_once` via the same transaction boundary.
"""
from dataclasses import dataclass
from sqlalchemy.orm import Session

from app.usage import service


class ConfirmedTechnicalFailure(Exception):
    """Only raise after authoritative provider receipts (including zero cost)."""


@dataclass(frozen=True)
class ProviderReceipt:
    key: str
    cost_micro_usd: int
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ProcessingReceipt:
    request_id: str
    state: str
    outcome: str | None
    replayed: bool


def transaction(engine, operation):
    with Session(engine, expire_on_commit=False) as session, session.begin():
        return operation(session)


class AccountingContext:
    def __init__(self, engine, request_id, claim):
        self.engine, self.request_id, self._claim = engine, request_id, claim

    def authorize(self, key, provider, model, maximum):
        return transaction(self.engine, lambda s: service.authorize_attempt(
            s, self.request_id, self._claim, key, provider, model, maximum))

    def settle(self, key, receipt):
        return transaction(self.engine, lambda s: service.settle_attempt(
            s, self.request_id, self._claim, key, receipt.key, receipt.cost_micro_usd,
            input_tokens=receipt.input_tokens, output_tokens=receipt.output_tokens))

    def perform(self, key, provider, model, maximum, callback):
        admission = self.authorize(key, provider, model, maximum)
        if not admission.created:
            raise service.UsageError(409, "provider_attempt_must_not_be_replayed")
        receipt = callback(admission.attempt.id)
        if not isinstance(receipt, ProviderReceipt):
            raise service.UsageError(422, "verified_provider_receipt_required")
        self.settle(key, receipt)


def run_once(engine, user_id, request_key, fingerprint, estimated_micro_usd, processor):
    """
    Wrap `service.reserve/start/finish` with the same transaction boundary as Core.

    Processor:
    - returns outcome ('answered' | 'no_answer' | 'technical_error')
    - MAY raise ConfirmedTechnicalFailure (→ refunded)
    - MUST NOT raise on uncertain errors (timeout) — those stay 'uncertain'
    """
    admission = transaction(engine, lambda s: service.reserve(
        s, user_id, request_key, fingerprint, estimated_micro_usd))
    identity = admission.request.id
    if not admission.created:
        return ProcessingReceipt(identity, admission.request.state, admission.request.outcome, True)
    try:
        claim = transaction(engine, lambda s: service.start(s, identity))
    except service.UsageError:
        try:
            row = transaction(engine, lambda s: service.cancel_before_start(s, user_id, request_key))
        except service.UsageError:
            row = transaction(engine, lambda s: service.request_row(s, identity))
        return ProcessingReceipt(identity, row.state, row.outcome, False)
    context = AccountingContext(engine, identity, claim)
    try:
        outcome = processor(context)
    except ConfirmedTechnicalFailure:
        outcome = "technical_error"
    except Exception:
        # Uncertain — do NOT auto-refund.
        row = transaction(engine, lambda s: service.mark_uncertain(s, identity, claim))
        return ProcessingReceipt(identity, row.state, row.outcome, False)
    try:
        row = transaction(engine, lambda s: service.finish(s, identity, claim, outcome))
    except service.UsageError:
        row = transaction(engine, lambda s: service.mark_uncertain(s, identity, claim))
    return ProcessingReceipt(identity, row.state, row.outcome, False)