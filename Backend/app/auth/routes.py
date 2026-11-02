from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import (get_auth_context, get_central_session, get_current_user,
                                   require_development_auth)
from app.auth.service import login
from app.models import Department, DepartmentMembership, Role, RoleAssignment, User

router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    authentication_provider: str = "development_password"
    production_ready: bool = False


class RoleProfile(BaseModel):
    name: str
    scope: str
    department_id: str | None


class UserProfile(BaseModel):
    id: str
    name: str
    email: str
    status: str
    roles: list[RoleProfile]
    authentication_provider: str = "development_password"
    production_ready: bool = False


@router.post("/dev/login", response_model=TokenResponse)
def development_login(body: LoginRequest, request: Request,
                      settings=Depends(require_development_auth),
                      session: Session = Depends(get_central_session)):
    token = login(session, settings, body.email, body.password.get_secret_value(),
                  request.client.host if request.client else "unknown")
    return TokenResponse(access_token=token, expires_in=settings.session_seconds)


@router.get("/me", response_model=UserProfile)
def me(user: User = Depends(get_current_user), session: Session = Depends(get_central_session)):
    rows = session.execute(
        select(Role.name, Role.scope, RoleAssignment.department_id)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .where(RoleAssignment.user_id == user.id)
    ).all()
    active_departments = set(session.scalars(
        select(DepartmentMembership.department_id)
        .join(Department, Department.id == DepartmentMembership.department_id)
        .where(DepartmentMembership.user_id == user.id, DepartmentMembership.status == "active",
               Department.status == "active")
    ))
    roles = [RoleProfile(name=name, scope=scope, department_id=department_id)
             for name, scope, department_id in rows
             if (scope == "global" and department_id is None)
             or (scope == "department" and department_id in active_departments)]
    return UserProfile(id=user.id, name=user.name, email=user.email, status=user.status, roles=roles)


@router.post("/logout", status_code=204)
def logout(context=Depends(get_auth_context), session: Session = Depends(get_central_session)):
    context.session_record.revoked_at = datetime.now(timezone.utc)
    session.commit()
    return Response(status_code=204)
