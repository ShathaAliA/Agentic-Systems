from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
import uuid

from app.models import Base, utc_now


class HandoffPolicy(Base):
    """Singleton Admin setting: how many handoffs one employee may keep open. No row means the default."""
    __tablename__ = "handoff_policy"
    __table_args__ = (CheckConstraint("id=1", name="ck_handoff_policy_singleton"),
                      CheckConstraint("open_cap BETWEEN 1 AND 50 AND version>0", name="ck_handoff_policy_values"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    open_cap: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)


class HandoffPolicyAudit(Base):
    __tablename__ = "handoff_policy_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    after: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
