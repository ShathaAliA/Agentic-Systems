
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import User, Department, DepartmentDatabase
from app.services.department_provisioning import (
    request_department_creation,
)


with Session(engine) as session:
    admin = session.scalar(
        select(User).where(
            User.email == "admin@example.com"
        )
    )

    if admin is None:
        raise RuntimeError("Seeded admin user not found")

    admin_id = admin.id

print("TEST: Admin requests Legal department")

result = request_department_creation(
    admin_user_id=admin_id,
    department_name="Legal",
)

print(result)

with Session(engine) as session:
    department = session.get(
        Department,
        result["department_id"],
    )

    registry = session.scalar(
        select(DepartmentDatabase).where(
            DepartmentDatabase.department_id == department.id
        )
    )

    assert department.status == "provisioning"
    assert registry is not None
    assert registry.status == "provisioning"
    assert registry.database_name == result["database_name"]

print("PASS: Department registered for provisioning")
