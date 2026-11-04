from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_central_session, get_current_user
from app.departments.service import DepartmentError
from app.models import DepartmentMembership, Role, RoleAssignment, User
from app.users import service
from app.users.models import UserAudit

router = APIRouter(prefix="/admin", tags=["user administration"])


def administrator(user: User = Depends(get_current_user), session: Session = Depends(get_central_session)):
    try:
        service.require_admin(session, user.id)
    except DepartmentError as error:
        raise HTTPException(error.status, error.code) from None
    return user


def write(session, operation):
    try:
        value = operation()
        session.commit()
        return value
    except DepartmentError as error:
        session.rollback()
        raise HTTPException(error.status, error.code) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "identity_or_assignment_conflict") from None


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewUser(StrictBody):
    name: str = Field(min_length=1, max_length=150)
    email: str = Field(min_length=3, max_length=255)


class StatusBody(StrictBody):
    status: Literal["active", "inactive"]


class AssignmentBody(StrictBody):
    role_id: str = Field(min_length=1, max_length=36)
    department_id: str | None = Field(default=None, min_length=1, max_length=36)


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    email: str
    status: str
    created_at: datetime


class RoleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    scope: str


class MembershipView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    department_id: str
    status: str


class AssignmentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    role_id: str
    department_id: str | None


class AuditView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    actor_user_id: str
    target_user_id: str
    action: str
    before: dict | None
    after: dict | None
    created_at: datetime


@router.get("/users", response_model=list[UserView])
def users(status: Literal["active", "inactive"] | None = None, offset: int = Query(0, ge=0),
          limit: int = Query(50, ge=1, le=100), actor=Depends(administrator), session: Session = Depends(get_central_session)):
    query = select(User)
    if status:
        query = query.where(User.status == status)
    return session.scalars(query.order_by(User.created_at, User.id).offset(offset).limit(limit)).all()


@router.post("/users", response_model=UserView, status_code=201)
def create(body: NewUser, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return write(session, lambda: service.create_user(session, actor.id, body.name, body.email))


@router.get("/users/{user_id}", response_model=UserView)
def user_detail(user_id: str, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    try:
        return service.target(session, user_id)
    except DepartmentError as error:
        raise HTTPException(error.status, error.code) from None


@router.patch("/users/{user_id}/status", response_model=UserView)
def status(user_id: str, body: StatusBody, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return write(session, lambda: service.set_status(session, actor.id, user_id, body.status))


@router.get("/roles", response_model=list[RoleView])
def roles(actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return [role for role in session.scalars(select(Role).order_by(Role.name))
            if service.ROLE_SCOPES.get(role.name) == role.scope]


@router.get("/users/{user_id}/memberships", response_model=list[MembershipView])
def memberships(user_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                actor=Depends(administrator), session: Session = Depends(get_central_session)):
    user_detail(user_id, actor, session)
    return session.scalars(select(DepartmentMembership).where(DepartmentMembership.user_id == user_id)
        .order_by(DepartmentMembership.department_id).offset(offset).limit(limit)).all()


@router.put("/users/{user_id}/memberships/{department_id}", response_model=MembershipView)
def membership(user_id: str, department_id: str, body: StatusBody, actor=Depends(administrator),
               session: Session = Depends(get_central_session)):
    return write(session, lambda: service.set_membership(session, actor.id, user_id, department_id, body.status))


@router.get("/users/{user_id}/roles", response_model=list[AssignmentView])
def assignments(user_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                actor=Depends(administrator), session: Session = Depends(get_central_session)):
    user_detail(user_id, actor, session)
    return session.scalars(select(RoleAssignment).where(RoleAssignment.user_id == user_id)
        .order_by(RoleAssignment.created_at, RoleAssignment.id).offset(offset).limit(limit)).all()


@router.post("/users/{user_id}/roles", response_model=AssignmentView, status_code=201)
def assign(user_id: str, body: AssignmentBody, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return write(session, lambda: service.grant_role(session, actor.id, user_id, body.role_id, body.department_id))


@router.delete("/users/{user_id}/roles/{assignment_id}", status_code=204)
def revoke(user_id: str, assignment_id: str, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    write(session, lambda: service.revoke_role(session, actor.id, user_id, assignment_id))
    return Response(status_code=204)


@router.get("/users/{user_id}/audit", response_model=list[AuditView])
def events(user_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
           actor=Depends(administrator), session: Session = Depends(get_central_session)):
    user_detail(user_id, actor, session)
    return session.scalars(select(UserAudit).where(UserAudit.target_user_id == user_id)
        .order_by(UserAudit.created_at, UserAudit.id).offset(offset).limit(limit)).all()
