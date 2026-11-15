"""Add restart/retry metadata to the existing registry (no tenant/document changes)."""
from alembic import op
import sqlalchemy as sa

revision = "p001"
down_revision = "f48bfc74f531"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("department_databases", sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"))
    op.add_column("department_databases", sa.Column("claim_token", sa.String(36)))
    op.add_column("department_databases", sa.Column("last_error_code", sa.String(80)))
    op.add_column("department_databases", sa.Column("last_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("department_databases", sa.Column("next_retry_at", sa.DateTime(timezone=True)))
    op.add_column("department_databases", sa.Column("applied_revision", sa.String(100)))
    op.create_check_constraint("ck_provisioning_attempt_count", "department_databases", "attempt_count >= 0")


def downgrade():
    op.drop_constraint("ck_provisioning_attempt_count", "department_databases", type_="check")
    for column in ["applied_revision", "next_retry_at", "last_attempt_at", "last_error_code", "claim_token", "attempt_count"]:
        op.drop_column("department_databases", column)
