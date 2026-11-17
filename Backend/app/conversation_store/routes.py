"""Owner-only conversation metadata/read APIs. No question/provider dispatch."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth.dependencies import get_current_user, get_central_session
from app.conversation_store.conversations import ConversationService
from app.conversation_store.authorization import ConversationResultStoreFactory
from app.usage.service import UsageError

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
    mode: Literal["department", "orchestrator"]
    department_ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def scope(self):
        if len(set(self.department_ids)) != len(self.department_ids) or (self.mode == "department" and len(self.department_ids) != 1):
            raise ValueError("Invalid conversation scope")
        return self


def conversation_service(request: Request, user=Depends(get_current_user), session=Depends(get_central_session)):
    factory = request.app.state.conversation_store_factory
    if factory is None: raise HTTPException(503, "Conversation store is not configured")
    try:
        store = ConversationResultStoreFactory(factory, session.get_bind(),
            request.app.state.source_authorizer_provider).metadata_for(user.id)
    except UsageError as e:
        raise HTTPException(e.status, e.code) from None
    if store.user_id != user.id: raise HTTPException(503, "Conversation store identity mismatch")
    return ConversationService(store, session.get_bind())


def perform(callback):
    try: return callback()
    except UsageError as e: raise HTTPException(e.status, e.code) from None


@router.post("")
def create(body: ConversationCreate, service=Depends(conversation_service)):
    return perform(lambda: service.create(body.mode, body.department_ids, body.request_key))


@router.get("")
def listing(limit: int = Query(20, ge=1, le=100), after: UUID | None = None, service=Depends(conversation_service)):
    return perform(lambda: service.list(limit, str(after) if after else None))


@router.get("/{conversation_id}")
def get(conversation_id: UUID, service=Depends(conversation_service)):
    return perform(lambda: service.get(str(conversation_id)))


@router.get("/{conversation_id}/messages")
def messages(conversation_id: UUID, limit: int = Query(20, ge=1, le=100),
             after: int = Query(0, ge=0, le=9223372036854775807), service=Depends(conversation_service)):
    return perform(lambda: service.messages(str(conversation_id), limit, after))
