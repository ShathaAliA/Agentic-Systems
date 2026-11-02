
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import engine as central_engine
from app.database_router import authorized_department_session
from app.models import User, Department


def get_user_id(session, email):
    user_id = session.scalar(
        select(User.id).where(User.email == email)
    )

    if user_id is None:
        raise ValueError(f"User not found: {email}")

    return user_id


def get_department_id(session, name):
    department_id = session.scalar(
        select(Department.id).where(
            Department.name == name
        )
    )

    if department_id is None:
        raise ValueError(f"Department not found: {name}")

    return department_id


def test_allowed(user_id, department_id, expected_db):

    with authorized_department_session(
        user_id=user_id,
        department_id=department_id,
        permission_code="documents:read",
    ) as session:

        actual_db = session.scalar(
            text("SELECT current_database()")
        )

        print("Connected to:", actual_db)

        assert actual_db == expected_db


def test_denied(user_id, department_id):

    try:
        with authorized_department_session(
            user_id=user_id,
            department_id=department_id,
            permission_code="documents:read",
        ) as session:

            session.execute(text("SELECT 1"))

    except PermissionError:
        print("PASS: Unauthorized access denied")
        return

    raise AssertionError(
        "Unauthorized access was allowed"
    )


def main():

    with Session(central_engine) as session:

        hr_manager = get_user_id(
            session,
            "hr.manager@example.com"
        )

        finance_employee = get_user_id(
            session,
            "finance.employee@example.com"
        )

        hr_id = get_department_id(session, "HR")

        finance_id = get_department_id(
            session, "Finance"
        )

    print("\nTEST 1: HR Manager -> HR")
    test_allowed(hr_manager, hr_id, "hr_db")

    print("\nTEST 2: Finance Employee -> Finance")
    test_allowed(
        finance_employee,
        finance_id,
        "finance_db",
    )

    print("\nTEST 3: HR Manager -> Finance")
    test_denied(hr_manager, finance_id)

    print("\nTEST 4: Finance Employee -> HR")
    test_denied(finance_employee, hr_id)

    print("\nAll database routing tests passed!")


if __name__ == "__main__":
    main()