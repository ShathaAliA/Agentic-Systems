import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base, utc_now


class UsagePolicy(Base):
    __tablename__ = "usage_policy"
    __table_args__ = (CheckConstraint("id=1", name="ck_usage_singleton"),
        CheckConstraint("monthly_questions>=0 AND daily_questions>=0 AND rate_count>=0 AND rate_seconds>0 AND monthly_budget_micro_usd>=0 AND version>0", name="ck_usage_policy_limits"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    timezone: Mapped[str] = mapped_column(String(64))
    monthly_questions: Mapped[int] = mapped_column(Integer)
    daily_questions: Mapped[int] = mapped_column(Integer)
    rate_count: Mapped[int] = mapped_column(Integer)
    rate_seconds: Mapped[int] = mapped_column(Integer)
    monthly_budget_micro_usd: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer)


class UsageRequest(Base):
    __tablename__ = "usage_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key", name="uq_usage_request_key"),
        CheckConstraint("state IN ('reserved','running','uncertain','completed','refunded')", name="ck_usage_request_state"),
        CheckConstraint("estimated_micro_usd>=0 AND held_micro_usd>=0", name="ck_usage_request_money"),
        CheckConstraint("(state='completed' AND outcome IS NOT NULL AND outcome IN ('answered','no_answer')) OR (state='refunded' AND outcome IS NOT NULL AND outcome IN ('technical_error','cancelled')) OR (state IN ('reserved','running','uncertain') AND outcome IS NULL)", name="ck_usage_request_outcome"),
        CheckConstraint("state NOT IN ('completed','refunded') OR held_micro_usd=0", name="ck_usage_terminal_hold"),
        Index("ix_usage_user_period", "user_id", "question_month", "question_day"),
        Index("ix_usage_user_created", "user_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    request_key: Mapped[str] = mapped_column(String(80))
    fingerprint: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[int] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64))
    question_month: Mapped[str] = mapped_column(String(7))
    question_day: Mapped[str] = mapped_column(String(10))
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    outcome: Mapped[str | None] = mapped_column(String(20))
    estimated_micro_usd: Mapped[int] = mapped_column(BigInteger)
    held_micro_usd: Mapped[int] = mapped_column(BigInteger)
    claim_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderAttempt(Base):
    __tablename__ = "usage_provider_attempts"
    __table_args__ = (
        UniqueConstraint("request_id", "attempt_key", name="uq_usage_attempt_key"),
        UniqueConstraint("provider", "receipt_key", name="uq_usage_provider_receipt"),
        CheckConstraint("state IN ('authorized','settled') AND max_micro_usd>=0 AND input_tokens>=0 AND output_tokens>=0", name="ck_usage_attempt_limits"),
        CheckConstraint("(state='authorized' AND actual_micro_usd IS NULL AND receipt_key IS NULL AND charged_month IS NULL) OR (state='settled' AND actual_micro_usd IS NOT NULL AND actual_micro_usd>=0 AND receipt_key IS NOT NULL AND charged_month IS NOT NULL)", name="ck_usage_attempt_receipt"),
        Index("ix_usage_attempt_month", "charged_month"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id: Mapped[str] = mapped_column(ForeignKey("usage_requests.id", ondelete="RESTRICT"))
    attempt_key: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), default="authorized")
    max_micro_usd: Mapped[int] = mapped_column(BigInteger)
    actual_micro_usd: Mapped[int | None] = mapped_column(BigInteger)
    receipt_key: Mapped[str | None] = mapped_column(String(128))
    charged_month: Mapped[str | None] = mapped_column(String(7))
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEntry(Base):
    __tablename__ = "usage_entries"
    __table_args__ = (
        UniqueConstraint("request_id", "event_key", name="uq_usage_event_key"),
        CheckConstraint("question_delta IN (-1,0,1) AND cost_micro_usd>=0", name="ck_usage_entry_amounts"),
        Index("ix_usage_entries_user_created", "user_id", "created_at", "id"),
        Index("ix_usage_entries_user_period", "user_id", "question_month", "question_day"),
        Index("ix_usage_entries_cost_month", "cost_month"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id: Mapped[str] = mapped_column(ForeignKey("usage_requests.id", ondelete="RESTRICT"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    event_key: Mapped[str] = mapped_column(String(180))
    event: Mapped[str] = mapped_column(String(40))
    question_month: Mapped[str] = mapped_column(String(7))
    question_day: Mapped[str] = mapped_column(String(10))
    cost_month: Mapped[str | None] = mapped_column(String(7))
    question_delta: Mapped[int] = mapped_column(Integer, default=0)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger, default=0)
    hold_delta_micro_usd: Mapped[int] = mapped_column(BigInteger, default=0)
    verification_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UsagePolicyAudit(Base):
    __tablename__ = "usage_policy_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
