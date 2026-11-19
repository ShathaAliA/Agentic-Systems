from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.auth.dependencies import get_central_session
from app.departments.service import DepartmentError
from app.models import Permission
from app.users.routes import administrator, RoleView
from app.users.service import approved_role
from app.role_policies import service
from app.role_policies.models import RolePolicyAudit

router = APIRouter(prefix="/admin", tags=["role permissions"])


class PermissionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    code: str
    description: str | None


class PolicyView(BaseModel):
    role_id: str
    version: int
    editable: bool
    permissions: list[str]
    allowed_permissions: list[str]
    unsupported_permissions: list[str]


class PolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    permissions: list[str] = Field(max_length=100)


class AuditView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role_id: str
    actor_user_id: str
    version: int
    before: list[str]
    after: list[str]
    created_at: datetime


def domain(operation):
    try:
        return operation()
    except DepartmentError as error:
        raise HTTPException(error.status, error.code) from None


@router.get("/permissions", response_model=list[PermissionView])
def catalog(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
            actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return session.scalars(select(Permission).order_by(Permission.code, Permission.id).offset(offset).limit(limit)).all()


@router.get("/roles/{role_id}", response_model=RoleView)
def detail(role_id: str, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return domain(lambda: approved_role(session, role_id))


@router.get("/roles/{role_id}/permissions", response_model=PolicyView)
def policy(role_id: str, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    return domain(lambda: service.snapshot(session, role_id))


@router.put("/roles/{role_id}/permissions", response_model=PolicyView)
def replace(role_id: str, body: PolicyBody, actor=Depends(administrator), session: Session = Depends(get_central_session)):
    try:
        result = domain(lambda: service.replace(session, actor.id, role_id, body.expected_version, body.permissions))
        session.commit()
        return result
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "role_policy_conflict") from None
    except Exception:
        session.rollback()
        raise


@router.get("/roles/{role_id}/permissions/audit", response_model=list[AuditView])
def audit(role_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
          actor=Depends(administrator), session: Session = Depends(get_central_session)):
    domain(lambda: approved_role(session, role_id))
    return session.scalars(select(RolePolicyAudit).where(RolePolicyAudit.role_id == role_id)
        .order_by(RolePolicyAudit.version, RolePolicyAudit.id).offset(offset).limit(limit)).all()
