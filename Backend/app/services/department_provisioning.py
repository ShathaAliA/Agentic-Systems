"""Day 1 compatibility wrapper around the transactional central registration service."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.departments.service import DepartmentError, admin_create, is_admin
from app.models import DepartmentDatabase


def is_global_department_admin(session: Session, user_id: str) -> bool:
    return is_admin(session, user_id)


def request_department_creation(admin_user_id: str, department_name: str, *, central_engine=None) -> dict:
    """Trusted actor only. Register work; never create a PostgreSQL database here."""
    if central_engine is None:
        from app.database import engine as central_engine
    try:
        with Session(central_engine) as session, session.begin():
            department = admin_create(session, admin_user_id, department_name)
            registry = session.scalar(select(DepartmentDatabase).where(
                DepartmentDatabase.department_id == department.id))
            # Retain the internal Day 1 response. HTTP routes expose safe DTOs only.
            return {"department_id": department.id, "department_name": department.name,
                    "database_name": registry.database_name, "status": department.status}
    except DepartmentError as error:
        if error.status == 403:
            raise PermissionError("Only authorized admins can create departments") from None
        raise ValueError(error.code) from None
    except IntegrityError:
        raise ValueError("Department could not be registered") from None
