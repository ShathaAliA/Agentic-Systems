from sqlalchemy import select
from sqlalchemy.orm import Session

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env before anything else
load_dotenv(Path(__file__).resolve().parent / ".env")

from app.models import (
    User,
    Department,
    DepartmentMembership,
    Role,
    Permission,
    RolePermission,
    RoleAssignment,
)


def get_or_create(session, model, lookup, defaults=None):
    instance = session.scalar(
        select(model).filter_by(**lookup)
    )

    if instance is None:
        instance = model(
            **lookup,
            **(defaults or {})
        )
        session.add(instance)
        session.flush()

    return instance


def seed(*, development_fixtures=False, central_engine=None):
    if not development_fixtures or os.getenv("APP_ENV") != "development":
        raise RuntimeError("Demo seed requires APP_ENV=development and explicit --development-fixtures")
    if central_engine is None:
        # Seed is an administrative CLI — use direct connection (like worker.py).
        from sqlalchemy import create_engine
        from app.credentials import configured_url
        central_engine = create_engine(
            configured_url("DATABASE_URL"),
            pool_pre_ping=True,
            hide_parameters=True,
        )
    from app.departments.models import CreationGrant
    from app.departments.service import normalize_name
    with Session(central_engine) as session:
        with session.begin():

            # 1. Departments
            hr = get_or_create(session, Department, {"name": "HR"})
            finance = get_or_create(session, Department, {"name": "Finance"})

            for department in (hr, finance):
                name, key = normalize_name(department.name)
                get_or_create(session, CreationGrant, {"department_id": department.id},
                              {"name": name, "normalized_name": key, "state": "consumed"})

            # 2. Roles
            role_definitions = {
                "Admin": "global",
                "Department Manager": "department",
                "Employee": "department",
            }
            roles = {}
            for name, scope in role_definitions.items():
                roles[name] = get_or_create(session, Role, {"name": name}, {"scope": scope})

            # 3. Permissions
            permission_codes = [
                "departments:manage",
                "users:manage",
                "documents:read",
                "documents:upload",
                "documents:update",
                "documents:delete",
                "documents:manage_permissions",
                "audit:read",
            ]
            permissions = {}
            for code in permission_codes:
                permissions[code] = get_or_create(session, Permission, {"code": code})

            # 4. Role -> Permissions
            role_permissions = {
                "Admin": permission_codes,
                "Department Manager": [
                    "documents:read",
                    "documents:upload",
                    "documents:update",
                    "documents:delete",
                    "documents:manage_permissions",
                    "audit:read",
                ],
                "Employee": ["documents:read"],
            }
            for role_name, codes in role_permissions.items():
                for code in codes:
                    get_or_create(
                        session, RolePermission,
                        {"role_id": roles[role_name].id, "permission_id": permissions[code].id},
                    )

            # 5. Demo Users
            admin = get_or_create(session, User, {"email": "admin@example.com"}, {"name": "Platform Admin"})
            hr_manager = get_or_create(session, User, {"email": "hr.manager@example.com"}, {"name": "HR Manager"})
            finance_employee = get_or_create(session, User, {"email": "finance.employee@example.com"}, {"name": "Finance Employee"})

            # 6. Department Memberships
            get_or_create(session, DepartmentMembership, {"user_id": hr_manager.id, "department_id": hr.id})
            get_or_create(session, DepartmentMembership, {"user_id": finance_employee.id, "department_id": finance.id})

            # 7. Role Assignments
            get_or_create(session, RoleAssignment, {"user_id": admin.id, "role_id": roles["Admin"].id, "department_id": None})
            get_or_create(session, RoleAssignment, {"user_id": hr_manager.id, "role_id": roles["Department Manager"].id, "department_id": hr.id})
            get_or_create(session, RoleAssignment, {"user_id": finance_employee.id, "role_id": roles["Employee"].id, "department_id": finance.id})

    print("Seed data created successfully!")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Explicit development fixtures only; never run for production")
    parser.add_argument("--development-fixtures", action="store_true")
    args = parser.parse_args()
    seed(development_fixtures=args.development_fixtures)