import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Integer,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    CheckConstraint,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
)


def utc_now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# =====================================
# USERS
# =====================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


# =====================================
# DEPARTMENTS
# =====================================

class Department(Base):
    __tablename__ = "departments"

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    name: Mapped[str] = mapped_column(
        String(150),
        unique=True,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
    )


# =====================================
# DEPARTMENT MEMBERSHIPS
# =====================================

class DepartmentMembership(Base):
    __tablename__ = "department_memberships"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "department_id",
            name="uq_user_department",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    department_id: Mapped[str] = mapped_column(
        ForeignKey("departments.id"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
    )


# =====================================
# ROLES
# =====================================

class Role(Base):
    __tablename__ = "roles"

    __table_args__ = (
        CheckConstraint(
            "scope IN ('global', 'department')",
            name="ck_roles_scope",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )

    scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )


# =====================================
# PERMISSIONS
# =====================================

class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    code: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )


# =====================================
# ROLE PERMISSIONS
# =====================================

class RolePermission(Base):
    __tablename__ = "role_permissions"

    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "permission_id",
            name="uq_role_permission",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )

    permission_id: Mapped[str] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"),
        nullable=False,
    )


# =====================================
# ROLE ASSIGNMENTS
# =====================================

class RoleAssignment(Base):
    __tablename__ = "role_assignments"

    __table_args__ = (
        Index(
            "uq_global_role_assignment",
            "user_id",
            "role_id",
            unique=True,
            postgresql_where=text(
                "department_id IS NULL"
            ),
        ),
        Index(
            "uq_department_role_assignment",
            "user_id",
            "role_id",
            "department_id",
            unique=True,
            postgresql_where=text(
                "department_id IS NOT NULL"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )

    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

# =====================================
# DEPARTMENT DATABASE REGISTRY
# =====================================

class DepartmentDatabase(Base):
    __tablename__ = "department_databases"

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_revision: Mapped[str | None] = mapped_column(String(100), nullable=True)
    verified_contract: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('provisioning', 'active', 'failed')",
            name="ck_department_databases_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_provisioning_attempt_count"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    department_id: Mapped[str] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    database_name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )

    credential_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="provisioning",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

