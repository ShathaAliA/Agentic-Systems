"""Admin setting for the open-handoff cap, with append-only history."""
from alembic import op
import sqlalchemy as sa

revision = "handoff001_policy"
down_revision = "rbac002_archive"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("handoff_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("open_cap", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("id=1", name="ck_handoff_policy_singleton"),
        sa.CheckConstraint("open_cap BETWEEN 1 AND 50 AND version>0", name="ck_handoff_policy_values"))
    op.create_table("handoff_policy_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("before", sa.Integer(), nullable=True), sa.Column("after", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("handoff_policy_audit")
    op.drop_table("handoff_policy")
