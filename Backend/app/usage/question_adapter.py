"""Internal U1 boundary. No HTTP identity, document storage or provider client."""
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.authorization import has_permission
from app.usage import service
from app.usage.processor import AccountingContext, ProcessingReceipt, run_once, transaction

Identifier = Annotated[str, Field(min_length=1, max_length=36)]


class QuestionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    request_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
    question: str = Field(min_length=1, max_length=4000)
    department_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=20)
    context_revision: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def nonempty_unique(self):
        if not self.question.strip() or len(set(self.department_ids)) != len(self.department_ids):
            raise ValueError("Nonblank question and unique departments required")
        return self

    def fingerprint(self) -> str:
        # Exact text is intentional: do not collapse materially different prompts.
        # context_revision is an immutable, server-resolved conversation/context ID.
        canonical = json.dumps({"adapter_version": 1, "question": self.question,
            "department_ids": sorted(self.department_ids), "context_revision": self.context_revision},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class QuestionResult:
    receipt: ProcessingReceipt
    response: object | None


def execute_question(engine, *, authenticated_user_id: str, command: QuestionCommand,
                     estimated_micro_usd: int,
                     authorize_content: Callable[[str, QuestionCommand], bool],
                     process: Callable[[AccountingContext], str],
                     load_response: Callable[[str, str], object | None]) -> QuestionResult:
    """Trusted synchronous adapter; call off the async event loop.

    Identity must originate from get_current_user; quote/context from trusted server
    code, never client billing fields. process persists its result outside U1 BEFORE
    returning answered/no_answer. load_response checks ownership and current source
    audiences and returns only an authorized response. Exceptions propagate without
    logging private content here. A missing result never authorizes redispatch.
    """
    if not isinstance(command, QuestionCommand):
        raise service.UsageError(422, "validated_question_command_required")

    def check_access():
        def check(session):
            service.user(session, authenticated_user_id, active=True)
            if not all(has_permission(session, authenticated_user_id, "documents:read", department)
                       for department in command.department_ids):
                raise service.UsageError(403, "question_department_forbidden")
        transaction(engine, check)
        # Require affirmative authorization, not a truthy object or missing check.
        if authorize_content(authenticated_user_id, command) is not True:
            raise service.UsageError(403, "question_content_forbidden")

    check_access()
    receipt = run_once(engine, authenticated_user_id, command.request_key,
                       command.fingerprint(), estimated_micro_usd, process)
    if receipt.state != "completed" or receipt.outcome not in {"answered", "no_answer"}:
        return QuestionResult(receipt, None)
    check_access()
    response = load_response(authenticated_user_id, receipt.request_id)
    if response is None:
        raise service.UsageError(503, "question_result_unavailable")
    return QuestionResult(receipt, response)
