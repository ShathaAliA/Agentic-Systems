"""Core-proposed document API shapes. No authorization or document implementation."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class QuestionRequest(StrictDraft):
    department_id: str = Field(min_length=1, max_length=36)
    question: str = Field(min_length=1, max_length=4000)
    request_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")


class SourceReference(StrictDraft):
    document_id: int = Field(ge=1)
    version_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=250)
    location: str = Field(min_length=1, max_length=250)
    quote: str = Field(min_length=1, max_length=4000)


class AnswerResponse(StrictDraft):
    status: Literal["answered", "no_answer", "partial", "conflict"]
    answer: str = Field(min_length=1, max_length=16000)
    sources: list[SourceReference] = Field(max_length=30)
    unanswered_departments: list[str] = Field(default_factory=list, max_length=20)
    handoff_available: bool

    @model_validator(mode="after")
    def supported_answer(self):
        if self.status in {"answered", "partial", "conflict"} and not self.sources:
            raise ValueError("Sources required")
        if self.status == "no_answer" and self.sources:
            raise ValueError("No-answer cannot claim supporting evidence")
        if self.status == "partial" and not self.unanswered_departments:
            raise ValueError("Unanswered departments required")
        if self.status == "conflict" and len(self.sources) < 2:
            raise ValueError("Both conflicting sources required")
        return self
