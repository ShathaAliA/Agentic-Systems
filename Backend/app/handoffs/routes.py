"""Human handoff API. Identity comes from the session; scope and roles are re-read from Central per request."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.orm import Session

from app.auth.dependencies import get_central_session, get_current_user
from app.conversation_store.authorization import ConversationResultStoreFactory
from app.departments.service import DepartmentError
from app.handoffs import notifications, policy
from app.handoffs.audit import HandoffAudit
from app.handoffs.service import HandoffService
from app.usage.service import UsageError
from app.users.routes import administrator

router = APIRouter(prefix="/api/handoffs", tags=["human handoff"])
notifications_router = APIRouter(prefix="/api/notifications", tags=["human handoff notifications"])
admin_router = APIRouter(prefix="/api/admin/handoffs", tags=["human handoff administration"])


class HandoffCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
    conversation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
    turn_sequence: StrictInt = Field(ge=1, le=2**31 - 1)
    department_id: str = Field(min_length=1, max_length=36)
    summary: str = Field(min_length=1, max_length=4000)


class MessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    body: str = Field(min_length=1, max_length=8000)


class ReadBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    up_to_sequence: StrictInt | None = Field(default=None, ge=0, le=2**31 - 1)


class TransferBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    to_user_id: str = Field(min_length=1, max_length=36)


class PolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    open_cap: StrictInt = Field(ge=1, le=50)
    expected_version: StrictInt = Field(ge=0, le=2**31 - 1)


def handoff_service(request: Request, user=Depends(get_current_user), session=Depends(get_central_session)):
    factory = request.app.state.conversation_store_factory
    if factory is None:
        raise HTTPException(503, "Conversation store is not configured")
    try:
        store = ConversationResultStoreFactory(factory, session.get_bind(),
            request.app.state.source_authorizer_provider).metadata_for(user.id)
    except UsageError as e:
        raise HTTPException(e.status, e.code) from None
    if store.user_id != user.id:
        raise HTTPException(503, "Conversation store identity mismatch")
    return HandoffService(store, session.get_bind(), open_cap=policy.open_cap_provider(session.get_bind()))


def perform(callback):
    try:
        return callback()
    except UsageError as e:
        raise HTTPException(e.status, e.code) from None


@router.post("", status_code=201)
def create(body: HandoffCreate, service=Depends(handoff_service)):
    return perform(lambda: service.create(body.conversation_id, body.turn_sequence, body.department_id,
                                          body.summary, body.request_key))


@router.get("")
def mine(limit: int = Query(20, ge=1, le=100), after: UUID | None = None, service=Depends(handoff_service)):
    return perform(lambda: service.list_mine(limit, str(after) if after else None))


@router.get("/inbox")
def inbox(status: Literal["new", "claimed", "closed"] | None = None, department_id: str | None = Query(None, max_length=36),
          limit: int = Query(20, ge=1, le=100), after: UUID | None = None, service=Depends(handoff_service)):
    return perform(lambda: service.list_inbox(status, department_id, limit, str(after) if after else None))


@router.get("/{handoff_id}")
def get(handoff_id: UUID, service=Depends(handoff_service)):
    return perform(lambda: service.get(str(handoff_id)))


@router.get("/{handoff_id}/messages")
def messages(handoff_id: UUID, limit: int = Query(50, ge=1, le=100), after: int = Query(0, ge=0, le=2**31 - 1),
             service=Depends(handoff_service)):
    return perform(lambda: service.messages(str(handoff_id), limit, after))


@router.post("/{handoff_id}/messages", status_code=201)
def post_message(handoff_id: UUID, body: MessageBody, service=Depends(handoff_service)):
    return perform(lambda: service.post_message(str(handoff_id), body.body))


@router.post("/{handoff_id}/read")
def mark_read(handoff_id: UUID, body: ReadBody, service=Depends(handoff_service)):
    return perform(lambda: service.mark_read(str(handoff_id), body.up_to_sequence))


@router.post("/{handoff_id}/claim")
def claim(handoff_id: UUID, service=Depends(handoff_service)):
    return perform(lambda: service.claim(str(handoff_id)))


@router.post("/{handoff_id}/transfer")
def transfer(handoff_id: UUID, body: TransferBody, service=Depends(handoff_service)):
    return perform(lambda: service.transfer(str(handoff_id), body.to_user_id))


@router.post("/{handoff_id}/close")
def close(handoff_id: UUID, service=Depends(handoff_service)):
    return perform(lambda: service.close(str(handoff_id)))


@router.post("/{handoff_id}/reopen")
def reopen(handoff_id: UUID, service=Depends(handoff_service)):
    return perform(lambda: service.reopen(str(handoff_id)))


@notifications_router.get("")
def feed(limit: int = Query(20, ge=1, le=100), after: str | None = Query(None, max_length=80), service=Depends(handoff_service)):
    return perform(lambda: notifications.feed(service, limit, after))


@notifications_router.get("/count")
def unread(service=Depends(handoff_service)):
    return perform(lambda: notifications.unread_count(service))


@admin_router.get("/policy")
def get_policy(actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return policy.view(policy.current(session))


@admin_router.put("/policy")
def set_policy(body: PolicyBody, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    try:
        result = policy.configure(session, actor.id, body.open_cap, body.expected_version)
        session.commit()
        return result
    except DepartmentError as e:
        session.rollback()
        raise HTTPException(e.status, e.code) from None


def audit_service(actor=Depends(administrator), service=Depends(handoff_service)):  # the Admin check runs first
    if actor.id != service.user_id:
        raise HTTPException(503, "Conversation store identity mismatch")
    return HandoffAudit(service.store, service.central)


@admin_router.get("")
def audit_list(status: Literal["new", "claimed", "closed"] | None = None, department_id: str | None = Query(None, max_length=36),
               limit: int = Query(20, ge=1, le=100), after: UUID | None = None, audit=Depends(audit_service)):
    return perform(lambda: audit.listing(department_id, status, limit, str(after) if after else None))


@admin_router.get("/access-log")
def audit_access_log(limit: int = Query(50, ge=1, le=100), after: str | None = Query(None, max_length=80),
                     audit=Depends(audit_service)):
    return perform(lambda: audit.access_log(limit, after))


@admin_router.get("/{handoff_id}")
def audit_get(handoff_id: UUID, audit=Depends(audit_service)):
    return perform(lambda: audit.get(str(handoff_id)))


@admin_router.get("/{handoff_id}/messages")
def audit_messages(handoff_id: UUID, limit: int = Query(50, ge=1, le=100), after: int = Query(0, ge=0, le=2**31 - 1),
                   audit=Depends(audit_service)):
    return perform(lambda: audit.messages(str(handoff_id), limit, after))
