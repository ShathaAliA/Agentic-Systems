"""Strict document business commands; callers never supply identities or costs."""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field
from app.conversation_store.schema import UUIDText

class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class Audience(Command):
    department_id: UUIDText | None
    role_id: UUIDText | None

class Publish(Command):
    expected_version: int = Field(ge=1)
    audiences: list[Audience] = Field(min_length=1, max_length=100)

class Revision(Command):
    expected_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=1000000)

class VersionCommand(Command):
    expected_version: int = Field(ge=1)

class Question(Command):
    question: str = Field(min_length=1, max_length=4000)
    department_id: UUIDText
    conversation_id: UUIDText
    request_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")

class Metadata(VersionCommand):
    filename: str = Field(min_length=1, max_length=250)

class AudienceChange(Audience):
    expected_version: int = Field(ge=1)
