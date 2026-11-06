import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, utc_now


class CreationGrant(Base):
    """A consumed grant is also the permanent ownership/name reservation ledger."""
    __tablename__ = "department_creation_grants"
    __table_args__ = (
        CheckConstraint("state IN ('pending','consumed','revoked')", name="ck_creation_grant_state"),
        CheckConstraint("(state='consumed' AND department_id IS NOT NULL) OR (state!='consumed' AND department_id IS NULL)", name="ck_creation_grant_department"),
        CheckConstraint("state!='pending' OR head_user_id IS NOT NULL", name="ck_pending_grant_head"),
        Index("uq_reserved_department_name", "normalized_name", unique=True,
              postgresql_where=text("state != 'revoked'"), sqlite_where=text("state != 'revoked'")),
        Index("uq_reserved_department_head", "head_user_id", unique=True,
              postgresql_where=text("state != 'revoked'"), sqlite_where=text("state != 'revoked'")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(150))
    normalized_name: Mapped[str] = mapped_column(String(450))
    head_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id", ondelete="RESTRICT"), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DepartmentAudit(Base):
    __tablename__ = "department_audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(50))
    grant_id: Mapped[str | None] = mapped_column(ForeignKey("department_creation_grants.id", ondelete="RESTRICT"))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
