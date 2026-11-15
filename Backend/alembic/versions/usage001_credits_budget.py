"""Central question credits, provider receipts and immutable usage accounting."""
from alembic import op
import sqlalchemy as sa

revision = "usage001"
down_revision = "users001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("usage_policy",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("monthly_questions", sa.Integer(), nullable=False), sa.Column("daily_questions", sa.Integer(), nullable=False),
        sa.Column("rate_count", sa.Integer(), nullable=False), sa.Column("rate_seconds", sa.Integer(), nullable=False),
        sa.Column("monthly_budget_micro_usd", sa.BigInteger(), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("id=1", name="ck_usage_singleton"),
        sa.CheckConstraint("monthly_questions>=0 AND daily_questions>=0 AND rate_count>=0 AND rate_seconds>0 AND monthly_budget_micro_usd>=0 AND version>0", name="ck_usage_policy_limits"))
    op.create_table("usage_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.String(80), nullable=False), sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False), sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("question_month", sa.String(7), nullable=False), sa.Column("question_day", sa.String(10), nullable=False),
        sa.Column("state", sa.String(20), nullable=False), sa.Column("outcome", sa.String(20)),
        sa.Column("estimated_micro_usd", sa.BigInteger(), nullable=False), sa.Column("held_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("claim_id", sa.String(36)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "request_key", name="uq_usage_request_key"),
        sa.CheckConstraint("state IN ('reserved','running','uncertain','completed','refunded')", name="ck_usage_request_state"),
        sa.CheckConstraint("estimated_micro_usd>=0 AND held_micro_usd>=0", name="ck_usage_request_money"),
        sa.CheckConstraint("(state='completed' AND outcome IS NOT NULL AND outcome IN ('answered','no_answer')) OR (state='refunded' AND outcome IS NOT NULL AND outcome IN ('technical_error','cancelled')) OR (state IN ('reserved','running','uncertain') AND outcome IS NULL)", name="ck_usage_request_outcome"),
        sa.CheckConstraint("state NOT IN ('completed','refunded') OR held_micro_usd=0", name="ck_usage_terminal_hold"))
    op.create_index("ix_usage_user_period", "usage_requests", ["user_id", "question_month", "question_day"])
    op.create_index("ix_usage_user_created", "usage_requests", ["user_id", "created_at"])
    op.create_table("usage_provider_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("usage_requests.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("attempt_key", sa.String(80), nullable=False), sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(100), nullable=False), sa.Column("state", sa.String(20), nullable=False),
        sa.Column("max_micro_usd", sa.BigInteger(), nullable=False), sa.Column("actual_micro_usd", sa.BigInteger()),
        sa.Column("receipt_key", sa.String(128)), sa.Column("charged_month", sa.String(7)),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False), sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("request_id", "attempt_key", name="uq_usage_attempt_key"),
        sa.UniqueConstraint("provider", "receipt_key", name="uq_usage_provider_receipt"),
        sa.CheckConstraint("state IN ('authorized','settled') AND max_micro_usd>=0 AND input_tokens>=0 AND output_tokens>=0", name="ck_usage_attempt_limits"),
        sa.CheckConstraint("(state='authorized' AND actual_micro_usd IS NULL AND receipt_key IS NULL AND charged_month IS NULL) OR (state='settled' AND actual_micro_usd IS NOT NULL AND actual_micro_usd>=0 AND receipt_key IS NOT NULL AND charged_month IS NOT NULL)", name="ck_usage_attempt_receipt"))
    op.create_index("ix_usage_attempt_month", "usage_provider_attempts", ["charged_month"])
    op.create_table("usage_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("usage_requests.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_key", sa.String(180), nullable=False), sa.Column("event", sa.String(40), nullable=False),
        sa.Column("question_month", sa.String(7), nullable=False), sa.Column("question_day", sa.String(10), nullable=False),
        sa.Column("cost_month", sa.String(7)), sa.Column("question_delta", sa.Integer(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False), sa.Column("hold_delta_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("verification_ref", sa.String(128)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", "event_key", name="uq_usage_event_key"),
        sa.CheckConstraint("question_delta IN (-1,0,1) AND cost_micro_usd>=0", name="ck_usage_entry_amounts"))
    op.create_index("ix_usage_entries_user_created", "usage_entries", ["user_id", "created_at", "id"])
    op.create_index("ix_usage_entries_user_period", "usage_entries", ["user_id", "question_month", "question_day"])
    op.create_index("ix_usage_entries_cost_month", "usage_entries", ["cost_month"])
    op.create_table("usage_policy_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("before", sa.JSON()), sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    # No default policy: an operator must explicitly set limits before admission.


def downgrade():
    op.drop_table("usage_policy_audit")
    op.drop_table("usage_entries")
    op.drop_table("usage_provider_attempts")
    op.drop_table("usage_requests")
    op.drop_table("usage_policy")
