
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import engine
from app.models import (
    Department,
    DepartmentDatabase,
    Permission,
    Role,
    RoleAssignment,
    RolePermission,
    User,
)


def is_global_department_admin(session: Session, user_id: str) -> bool:
    """Check global permission to manage departments."""

    user = session.get(User, user_id)

    if user is None or user.status != "active":
        return False

    permission_id = session.scalar(
        select(Permission.id)
        .join(
            RolePermission,
            RolePermission.permission_id == Permission.id,
        )
        .join(Role, Role.id == RolePermission.role_id)
        .join(
            RoleAssignment,
            RoleAssignment.role_id == Role.id,
        )
        .where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.department_id.is_(None),
            Role.scope == "global",
            Permission.code == "departments:manage",
        )
        .limit(1)
    )

    return permission_id is not None


def request_department_creation(
    admin_user_id: str,
    department_name: str,
) -> dict:
    """
    Register a new department for provisioning.

    This does NOT create a PostgreSQL database.
    admin_user_id must come from a trusted authentication
    context, never directly from an API request body.
    """

    name = department_name.strip()

    if not name or len(name) > 150:
        raise ValueError(
            "Department name must be between 1 and 150 characters"
        )

    department_id = str(uuid.uuid4())
    unique_id = uuid.uuid4().hex

    # Immutable database identity; independent of department name.
    database_name = f"dept_{unique_id}"
    credential_key = f"DEPT_{unique_id.upper()}"

    try:
        with Session(engine) as session:
            with session.begin():

                if not is_global_department_admin(
                    session, admin_user_id
                ):
                    raise PermissionError(
                        "Only authorized admins can create departments"
                    )

                existing = session.scalar(
                    select(Department.id).where(
                        func.lower(Department.name) == name.lower()
                    )
                )

                if existing is not None:
                    raise ValueError(
                        "Department already exists"
                    )

                department = Department(
                    id=department_id,
                    name=name,
                    status="provisioning",
                )

                database_registry = DepartmentDatabase(
                    id=str(uuid.uuid4()),
                    department_id=department_id,
                    database_name=database_name,
                    credential_key=credential_key,
                    status="provisioning",
                )

                session.add(department)
                session.add(database_registry)

    except IntegrityError as exc:
        raise ValueError(
            "Department could not be registered"
        ) from exc

    return {
        "department_id": department_id,
        "department_name": name,
        "database_name": database_name,
        "status": "provisioning",
    }
