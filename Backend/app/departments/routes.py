from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_central_session, get_current_user, require_global_permission
from app.departments import service
from app.departments.models import CreationGrant
from app.models import Department, DepartmentDatabase, DepartmentMembership, Role, RoleAssignment, User
from app.provisioning.errors import SAFE_ERROR_CODES

router = APIRouter(tags=["departments"])
admin = require_global_permission("departments:manage")


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GrantRequest(StrictBody):
    head_user_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=150)


class AdminCreation(StrictBody):
    name: str = Field(min_length=1, max_length=150)
    head_user_id: str | None = Field(default=None, min_length=1, max_length=36)


class HeadBinding(StrictBody):
    head_user_id: str = Field(min_length=1, max_length=36)


class GrantView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    head_user_id: str | None
    state: str
    department_id: str | None
    created_at: datetime


class DepartmentView(BaseModel):
    id: str
    name: str
    status: str
    provisioning_status: str | None
    last_error_code: str | None
    attempt_count: int
    archived_at: datetime | None


def view(session, department):
    registry = session.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id == department.id))
    error = registry.last_error_code if registry and registry.last_error_code in SAFE_ERROR_CODES else None
    return DepartmentView(id=department.id, name=department.name,
        status="archived" if department.archived_at else department.status,
        provisioning_status=registry.status if registry else None, last_error_code=error,
        attempt_count=registry.attempt_count if registry else 0, archived_at=department.archived_at)


def write(session, operation):
    try:
        result = operation()
        session.commit()
        return result
    except service.DepartmentError as error:
        session.rollback()
        raise HTTPException(error.status, error.code) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "department_or_head_conflict") from None


@router.post("/admin/department-creation-grants", response_model=GrantView, status_code=201)
def issue(body: GrantRequest, user: User = Depends(admin), session: Session = Depends(get_central_session)):
    return write(session, lambda: service.issue_grant(session, user.id, body.head_user_id, body.name))


@router.get("/admin/department-creation-grants", response_model=list[GrantView])
def grants(state: Literal["pending", "consumed", "revoked"] | None = None,
           offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
           user: User = Depends(admin), session: Session = Depends(get_central_session)):
    query = select(CreationGrant)
    if state:
        query = query.where(CreationGrant.state == state)
    return session.scalars(query.order_by(CreationGrant.created_at, CreationGrant.id).offset(offset).limit(limit)).all()


@router.post("/admin/department-creation-grants/{grant_id}/revoke", response_model=GrantView)
def revoke(grant_id: str, user: User = Depends(admin), session: Session = Depends(get_central_session)):
    return write(session, lambda: service.revoke_grant(session, user.id, grant_id))


@router.get("/me/department-creation-grant", response_model=GrantView)
def my_grant(user: User = Depends(get_current_user), session: Session = Depends(get_central_session)):
    grant = session.scalar(select(CreationGrant).where(CreationGrant.head_user_id == user.id)
                           .order_by(CreationGrant.created_at.desc(), CreationGrant.id.desc()).limit(1))
    if grant is None:
        raise HTTPException(404, "grant_not_found")
    return grant


@router.post("/departments/from-grant", response_model=DepartmentView, status_code=202)
def from_grant(body: StrictBody, user: User = Depends(get_current_user), session: Session = Depends(get_central_session)):
    department = write(session, lambda: service.consume_grant(session, user.id))
    return view(session, department)


@router.post("/admin/departments", response_model=DepartmentView, status_code=202)
def create(body: AdminCreation, user: User = Depends(admin), session: Session = Depends(get_central_session)):
    department = write(session, lambda: service.admin_create(session, user.id, body.name, body.head_user_id))
    return view(session, department)


@router.get("/admin/departments", response_model=list[DepartmentView])
def departments(state: Literal["provisioning", "active", "failed", "archived"] | None = None,
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                user: User = Depends(admin), session: Session = Depends(get_central_session)):
    query = select(Department)
    if state == "archived":
        query = query.where(Department.archived_at.is_not(None))
    elif state:
        query = query.where(Department.archived_at.is_(None), Department.status == state)
    return [view(session, d) for d in session.scalars(query.order_by(Department.name, Department.id).offset(offset).limit(limit))]


@router.get("/admin/departments/{department_id}", response_model=DepartmentView)
def detail(department_id: str, user: User = Depends(admin), session: Session = Depends(get_central_session)):
    department = session.get(Department, department_id)
    if department is None:
        raise HTTPException(404, "department_not_found")
    return view(session, department)


@router.post("/admin/departments/{department_id}/archive", response_model=DepartmentView)
def archive(department_id: str, user: User = Depends(admin), session: Session = Depends(get_central_session)):
    department = write(session, lambda: service.archive(session, user.id, department_id))
    return view(session, department)


@router.get("/me/department", response_model=DepartmentView)
def my_department(user: User = Depends(get_current_user), session: Session = Depends(get_central_session)):
    # Metadata of one's own department may be read before tenant readiness; no tenant connection.
    departments = session.scalars(select(Department).join(RoleAssignment, RoleAssignment.department_id == Department.id)
        .join(Role, Role.id == RoleAssignment.role_id)
        .join(DepartmentMembership, (DepartmentMembership.department_id == Department.id) &
              (DepartmentMembership.user_id == user.id))
        .where(RoleAssignment.user_id == user.id, Role.name == "Department Manager", Role.scope == "department",
               DepartmentMembership.status == "active").distinct().limit(2)).all()
    if not departments:
        raise HTTPException(404, "owned_department_not_found")
    if len(departments) != 1:
        raise HTTPException(409, "existing_head_requires_reconciliation")
    return view(session, departments[0])


@router.post("/admin/departments/{department_id}/head", response_model=DepartmentView)
def bind_head(department_id: str, body: HeadBinding, user: User = Depends(admin),
              session: Session = Depends(get_central_session)):
    department = write(session, lambda: service.bind_head(session, user.id, department_id, body.head_user_id))
    return view(session, department)
