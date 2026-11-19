"""Append-only results in the owning tenant, never central accounting."""
from alembic import op
import sqlalchemy as sa

revision = "tenant_0007"
down_revision = "tenant_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("question_results",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_question_results_user_id", "question_results", ["user_id"])


def downgrade():
    raise RuntimeError("Result/audit history must not be deleted automatically")
