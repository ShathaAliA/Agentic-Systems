
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import User, Department
from app.services.authorization import has_permission


def get_user_id(session, email):
    user_id = session.scalar(
        select(User.id).where(User.email == email)
    )

    if user_id is None:
        raise ValueError(f"User not found: {email}")

    return user_id


def get_department_id(session, name):
    department_id = session.scalar(
        select(Department.id).where(Department.name == name)
    )

    if department_id is None:
        raise ValueError(f"Department not found: {name}")

    return department_id


def main():
    with Session(engine) as session:

        admin = get_user_id(
            session, "admin@example.com"
        )

        hr_manager = get_user_id(
            session, "hr.manager@example.com"
        )

        finance_employee = get_user_id(
            session, "finance.employee@example.com"
        )

        hr = get_department_id(session, "HR")
        finance = get_department_id(session, "Finance")

        tests = [
            (
                "Admin manages HR",
                admin,
                "documents:upload",
                hr,
                True,
            ),
            (
                "HR Manager uploads to HR",
                hr_manager,
                "documents:upload",
                hr,
                True,
            ),
            (
                "HR Manager uploads to Finance",
                hr_manager,
                "documents:upload",
                finance,
                False,
            ),
            (
                "Finance Employee reads Finance",
                finance_employee,
                "documents:read",
                finance,
                True,
            ),
            (
                "Finance Employee deletes Finance",
                finance_employee,
                "documents:delete",
                finance,
                False,
            ),
            (
                "Finance Employee reads HR",
                finance_employee,
                "documents:read",
                hr,
                False,
            ),
        ]

        passed = 0

        for name, user_id, permission, department, expected in tests:

            result = has_permission(
                session,
                user_id,
                permission,
                department,
            )

            success = result == expected

            print(
                f"{'PASS' if success else 'FAIL'}: {name}"
                f" | Allowed={result}"
            )

            if success:
                passed += 1

        print(f"\nTests passed: {passed}/{len(tests)}")

        if passed != len(tests):
            raise SystemExit(1)


if __name__ == "__main__":
    main()