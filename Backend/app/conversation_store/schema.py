"""Version 1 stored answer DTO, aligned with approved RC2 source locations."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

UUIDText = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PDFLocation(Strict):
    kind: Literal["pdf_page"]
    page_number: int = Field(ge=1)


class SlideLocation(Strict):
    kind: Literal["slide"]
    slide_number: int = Field(ge=1)


class SheetLocation(Strict):
    kind: Literal["sheet_range"]
    sheet_name: str = Field(min_length=1, max_length=100)
    cell_range: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*(?::[A-Z]+[1-9][0-9]*)?$")


class ImageLocation(Strict):
    kind: Literal["image"]
    image_number: int = Field(ge=1)


class SectionLocation(Strict):
    kind: Literal["markdown_section", "reviewed_text"]
    section: str = Field(min_length=1, max_length=250)


Location = Annotated[PDFLocation | SlideLocation | SheetLocation | ImageLocation | SectionLocation,
                     Field(discriminator="kind")]


class Source(Strict):
    department_id: UUIDText
    document_id: int = Field(ge=1)
    version_id: str = Field(min_length=1, max_length=64)
    version_number: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=250)
    quote: str = Field(min_length=1, max_length=4000)
    location: Location


class Answer(Strict):
    question_request_id: UUIDText
    conversation_id: UUIDText
    status: Literal["answered", "no_answer", "partial", "conflict"]
    answer: str = Field(min_length=1, max_length=16000)
    answer_kind: Literal["generated", "verbatim_excerpt", "system_notice"]
    llm_called: bool
    sources: list[Source] = Field(max_length=30)
    unanswered_departments: list[UUIDText] = Field(max_length=20)
    handoff_available: bool

    @model_validator(mode="after")
    def evidence(self):
        if not self.answer.strip() or len(set(self.unanswered_departments)) != len(self.unanswered_departments):
            raise ValueError("Invalid answer")
        if self.status == "no_answer":
            if self.sources or self.answer_kind != "system_notice":
                raise ValueError("No answer must not claim evidence")
        elif not self.sources:
            raise ValueError("Evidence required")
        if self.status == "conflict" and len(self.sources) < 2:
            raise ValueError("Both conflicting sources required")
        if self.status == "partial" and not self.unanswered_departments:
            raise ValueError("Unanswered scope required")
        if self.answer_kind == "verbatim_excerpt" and self.llm_called:
            raise ValueError("Invalid extractive mode")
        return self
