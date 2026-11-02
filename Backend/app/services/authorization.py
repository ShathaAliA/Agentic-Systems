from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    User,
    Department,
    DepartmentMembership,
    Role,
    Permission,
    RolePermission,
    RoleAssignment,
)


def has_permission(
    session: Session,
    user_id: str,
    permission_code: str,
    department_id: str,
) -> bool:
    """
    Check whether a user has a permission
    within a specified department.

    Global Admin roles can apply across departments.
    Department roles require active membership.
    """

    # 1. Verify user is active
    user = session.get(User, user_id)

    if user is None or user.status != "active":
        return False

    # 2. Verify department is active
    department = session.get(Department, department_id)

    if department is None or department.status != "active":
        return False

    # 3. Check global permissions
    global_permission = session.scalar(
        select(Permission.id)
        .join(
            RolePermission,
            RolePermission.permission_id == Permission.id,
        )
        .join(
            Role,
            Role.id == RolePermission.role_id,
        )
        .join(
            RoleAssignment,
            RoleAssignment.role_id == Role.id,
        )
        .where(
            RoleAssignment.user_id == user_id,
            Role.scope == "global",
            RoleAssignment.department_id.is_(None),
            Permission.code == permission_code,
        )
        .limit(1)
    )

    if global_permission is not None:
        return True

    # 4. Verify membership in the target department
    membership = session.scalar(
        select(DepartmentMembership.id)
        .where(
            DepartmentMembership.user_id == user_id,
            DepartmentMembership.department_id == department_id,
            DepartmentMembership.status == "active",
        )
        .limit(1)
    )

    if membership is None:
        return False

    # 5. Check department-scoped permission
    department_permission = session.scalar(
        select(Permission.id)
        .join(
            RolePermission,
            RolePermission.permission_id == Permission.id,
        )
        .join(
            Role,
            Role.id == RolePermission.role_id,
        )
        .join(
            RoleAssignment,
            RoleAssignment.role_id == Role.id,
        )
        .where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.department_id == department_id,
            Role.scope == "department",
            Permission.code == permission_code,
        )
        .limit(1)
    )

    return department_permission is not None
