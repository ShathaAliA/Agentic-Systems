"""Central-side checks for human handoff. Every call reads Central afresh: nothing here is cached."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (Department, DepartmentMembership, Permission, Role, RoleAssignment, RolePermission, User)
from app.services.authorization import has_permission
from app.usage.service import UsageError

REQUEST = "handoffs:request"   # employee, department-scoped
HANDLE = "handoffs:handle"     # head and deputy, department-scoped
AUDIT = "handoffs:audit"       # Admin, global (read-only, H5)


def require_active(session: Session, user_id: str) -> None:
    user = session.get(User, user_id)
    if user is None or user.status != "active":
        raise UsageError(403, "handoff_actor_inactive")


def may_request(session: Session, user_id: str, department_id: str) -> bool:
    return has_permission(session, user_id, REQUEST, department_id)


def staff_departments(session: Session, user_id: str) -> frozenset[str]:
    """Departments whose handoffs this user may handle right now.

    Only department-scoped assignments count: a global Admin role never makes someone a handler (Admin is
    audit-only), and the user needs an active membership in an active department."""
    user = session.get(User, user_id)
    if user is None or user.status != "active":
        return frozenset()
    rows = session.scalars(
        select(RoleAssignment.department_id)
        .join(Role, Role.id == RoleAssignment.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .join(DepartmentMembership, (DepartmentMembership.user_id == RoleAssignment.user_id)
              & (DepartmentMembership.department_id == RoleAssignment.department_id))
        .join(Department, Department.id == RoleAssignment.department_id)
        .where(RoleAssignment.user_id == user_id, RoleAssignment.department_id.is_not(None),
               Role.scope == "department", Permission.code == HANDLE,
               DepartmentMembership.status == "active", Department.status == "active",
               Department.archived_at.is_(None))).all()
    return frozenset(rows)
