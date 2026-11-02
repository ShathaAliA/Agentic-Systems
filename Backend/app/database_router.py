
import os
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import Session

from app.database import engine as central_engine
from app.models import Department
from app.services.authorization import has_permission


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5435"))


# Only approved databases are registered here.
DEPARTMENT_DATABASES = {
    "HR": {
        "database": "hr_db",
        "username": os.getenv("HR_DB_USER"),
        "password": os.getenv("HR_DB_PASSWORD"),
    },
    "Finance": {
        "database": "finance_db",
        "username": os.getenv("FINANCE_DB_USER"),
        "password": os.getenv("FINANCE_DB_PASSWORD"),
    },
}


def create_department_engine(config):
    if not config["username"] or not config["password"]:
        raise RuntimeError(
            "Missing department database credentials"
        )

    database_url = URL.create(
        "postgresql+psycopg",
        username=config["username"],
        password=config["password"],
        host=DB_HOST,
        port=DB_PORT,
        database=config["database"],
    )

    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
    )


# Create one reusable engine per department.
DEPARTMENT_ENGINES = {
    name: create_department_engine(config)
    for name, config in DEPARTMENT_DATABASES.items()
}


@contextmanager
def authorized_department_session(
    user_id: str,
    department_id: str,
    permission_code: str,
):
    """
    Validate authorization against the central DB
    before opening the department database.
    """

    with Session(central_engine) as central_session:

        allowed = has_permission(
            session=central_session,
            user_id=user_id,
            permission_code=permission_code,
            department_id=department_id,
        )

        if not allowed:
            raise PermissionError(
                "Access denied for this department"
            )

        department_name = central_session.scalar(
            select(Department.name).where(
                Department.id == department_id
            )
        )

    department_engine = DEPARTMENT_ENGINES.get(
        department_name
    )

    if department_engine is None:
        raise ValueError(
            "No database configured for department"
        )

    with Session(department_engine) as session:
        yield session