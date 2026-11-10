"""Add usage_requests.result_payload (JSON) for idempotent replay.

Revision ID: usage002_result_payload
Revises: usage001_credits_budget
"""
from alembic import op
import sqlalchemy as sa


revision = "usage002_result_payload"
down_revision = "usage001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_requests",
        sa.Column("result_payload", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("usage_requests", "result_payload")