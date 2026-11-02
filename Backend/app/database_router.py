
import os
import re
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import Session

from app.database import engine as central_engine
from app.models import DepartmentDatabase
from app.services.authorization import has_permission


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5435"))

# Limit the number of department connection pools per process.
MAX_CACHED_DEPARTMENTS = 8

_department_engines = {}
_engine_lock = Lock()


def create_department_engine(database_name, credential_key):
    """
    Create a database engine using credentials
    stored in environment variables.
    """

    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", credential_key):
        raise ValueError("Invalid credential key")

    if not re.fullmatch(r"[A-Za-z0-9_]+", database_name):
        raise ValueError("Invalid database name")

    prefix = credential_key.upper()

    username = os.getenv(f"{prefix}_DB_USER")
    password = os.getenv(f"{prefix}_DB_PASSWORD")

    if not username or not password:
        raise RuntimeError(
            f"Missing database credentials for {prefix}"
        )

    database_url = URL.create(
        "postgresql+psycopg",
        username=username,
        password=password,
        host=DB_HOST,
        port=DB_PORT,
        database=database_name,
    )

    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=1,
        pool_timeout=10,
    )


def get_department_engine(
    department_id,
    database_name,
    credential_key,
):
    """
    Reuse an existing engine when available.
    """

    cache_key = (
        department_id,
        database_name,
        credential_key,
    )

    with _engine_lock:

        if cache_key in _department_engines:
            return _department_engines[cache_key]

        if len(_department_engines) >= MAX_CACHED_DEPARTMENTS:
            raise RuntimeError(
                "Department engine cache limit reached"
            )

        engine = create_department_engine(
            database_name,
            credential_key,
        )

        _department_engines[cache_key] = engine

        return engine


@contextmanager
def authorized_department_session(
    user_id: str,
    department_id: str,
    permission_code: str,
):
    """
    Check central RBAC permissions before accessing
    the target department database.
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

        database_config = central_session.scalar(
            select(DepartmentDatabase).where(
                DepartmentDatabase.department_id == department_id,
                DepartmentDatabase.status == "active",
            )
        )

        if database_config is None:
            raise ValueError(
                "No active database registered for department"
            )

        database_name = database_config.database_name
        credential_key = database_config.credential_key

    department_engine = get_department_engine(
        department_id=department_id,
        database_name=database_name,
        credential_key=credential_key,
    )

    with Session(department_engine) as session:
        yield session