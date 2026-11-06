
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import User
from app.services.department_provisioning import (
    request_department_creation,
)

with Session(engine) as session:
    employee = session.scalar(
        select(User).where(
            User.email == "finance.employee@example.com"
        )
    )

    if employee is None:
        raise RuntimeError("Employee not found")

    employee_id = employee.id

print("TEST: Employee attempts to create a department")

try:
    request_department_creation(
        admin_user_id=employee_id,
        department_name="Unauthorized Department",
    )
except PermissionError:
    print("PASS: Unauthorized department creation denied")
else:
    raise AssertionError(
        "SECURITY FAILURE: Employee created a department"
    )