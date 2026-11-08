import os
from pathlib import Path
from fastapi import Form
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.credentials import configured_url
from app.runtime_security import restrict_runtime_engine

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = configured_url("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found in backend/.env")

engine = restrict_runtime_engine(create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    hide_parameters=True,
))

def check_database_connection():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- دالة الاتصال الآمنة ---
def get_department_db(department_id: str = Form(...)):
    """
    جلسة قاعدة بيانات آمنة.
    (شذى طلبت عدم استخدام search_path من الطلب، فنستخدم القاعدة المركزية ونفلتر بالـ department_id)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- استيراد الموديلز عشان Alembic يتعرف عليها ---
from app.models import Base, User, Department, DepartmentMembership, Role, Permission, RolePermission, RoleAssignment, DepartmentDatabase
from app.documents.models import Document, DocumentChunk, DocumentAuditLog, DocumentVersion, DocumentRevisionLog