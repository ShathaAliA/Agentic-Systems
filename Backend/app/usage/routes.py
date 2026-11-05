from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_central_session, get_current_user
from app.departments.service import DepartmentError
from app.models import User
from app.usage import service
from app.usage.models import UsageEntry, UsagePolicyAudit, UsageRequest
from app.users.routes import administrator

router = APIRouter(tags=["usage accounting"])


class PolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    timezone: str = Field(min_length=1, max_length=64)
    monthly_questions: StrictInt = Field(ge=0, le=10**9)
    daily_questions: StrictInt = Field(ge=0, le=10**9)
    rate_count: StrictInt = Field(ge=0, le=10**9)
    rate_seconds: StrictInt = Field(ge=1, le=3600)
    monthly_budget_micro_usd: StrictInt = Field(ge=0, le=10**15)


class PolicyView(PolicyBody):
    model_config = ConfigDict(from_attributes=True)
    version: int


class PolicyChange(PolicyBody):
    expected_version: StrictInt = Field(ge=0, le=2**31 - 1)


class PersonalView(BaseModel):
    user_id: str
    configured: bool
    enabled: bool
    timezone: str | None = None
    month: str | None = None
    day: str | None = None
    monthly_used_or_held: int | None = None
    monthly_remaining: int | None = None
    daily_used_or_held: int | None = None
    daily_remaining: int | None = None
    rate_remaining: int | None = None
    spent_micro_usd: int | None = None
    company_budget_blocked: bool | None = None


class CompanyView(BaseModel):
    configured: bool
    enabled: bool
    month: str | None = None
    budget_micro_usd: int | None = None
    spent_micro_usd: int | None = None
    held_micro_usd: int | None = None
    remaining_micro_usd: int | None = None
    over_budget_micro_usd: int | None = None
    unresolved_requests: int | None = None


class EntryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    request_id: str
    user_id: str
    event: str
    question_month: str
    question_day: str
    cost_month: str | None
    question_delta: int
    cost_micro_usd: int
    hold_delta_micro_usd: int
    created_at: datetime


class RequestView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    request_key: str
    state: str
    outcome: str | None
    question_month: str
    question_day: str
    held_micro_usd: int
    created_at: datetime


class PolicyAuditView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    actor_user_id: str
    before: dict | None
    after: dict
    created_at: datetime


def invoke(session, operation, *, commit=False):
    try:
        result = operation()
        if commit:
            session.commit()
        return result
    except (service.UsageError, DepartmentError) as error:
        session.rollback()
        raise HTTPException(error.status, error.code) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "usage_accounting_conflict") from None


@router.get("/me/usage", response_model=PersonalView)
def personal(user: User = Depends(get_current_user), session: Session = Depends(get_central_session)):
    return invoke(session, lambda: service.personal_summary(session, user.id))


@router.get("/me/usage/ledger", response_model=list[EntryView])
def my_ledger(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
              user: User = Depends(get_current_user), session: Session = Depends(get_central_session)):
    return session.scalars(select(UsageEntry).where(UsageEntry.user_id == user.id)
        .order_by(UsageEntry.created_at, UsageEntry.id).offset(offset).limit(limit)).all()


@router.get("/me/usage/requests", response_model=list[RequestView])
def my_requests(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                user: User = Depends(get_current_user), session: Session = Depends(get_central_session)):
    return session.scalars(select(UsageRequest).where(UsageRequest.user_id == user.id)
        .order_by(UsageRequest.created_at, UsageRequest.id).offset(offset).limit(limit)).all()


@router.get("/admin/usage/policy", response_model=PolicyView | None)
def policy(actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return service.policy(session)


@router.put("/admin/usage/policy", response_model=PolicyView)
def configure(body: PolicyChange, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return invoke(session, lambda: service.configure(session, actor.id,
        body.model_dump(exclude={"expected_version"}), expected_version=body.expected_version), commit=True)


@router.get("/admin/usage/policy/history", response_model=list[PolicyAuditView])
def policy_history(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                   actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return session.scalars(select(UsagePolicyAudit).order_by(UsagePolicyAudit.created_at, UsagePolicyAudit.id)
                           .offset(offset).limit(limit)).all()


@router.get("/admin/usage/summary", response_model=CompanyView)
def company(actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return invoke(session, lambda: service.company_summary(session, actor.id))


@router.get("/admin/usage/users/{user_id}", response_model=PersonalView)
def user_usage(user_id: str, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return invoke(session, lambda: service.personal_summary(session, user_id))


@router.get("/admin/usage/ledger", response_model=list[EntryView])
def ledger(user_id: str | None = Query(None, max_length=36), offset: int = Query(0, ge=0),
           limit: int = Query(50, ge=1, le=100), actor=Depends(administrator), session: Session = Depends(get_central_session)):
    query = select(UsageEntry)
    if user_id:
        query = query.where(UsageEntry.user_id == user_id)
    return session.scalars(query.order_by(UsageEntry.created_at, UsageEntry.id).offset(offset).limit(limit)).all()


@router.get("/admin/usage/unresolved", response_model=list[RequestView])
def unresolved(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
               actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return session.scalars(select(UsageRequest).where(UsageRequest.state.in_(service.ACTIVE))
        .order_by(UsageRequest.created_at, UsageRequest.id).offset(offset).limit(limit)).all()
