
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL not found in backend/.env"
    )

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)


def check_database_connection():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
