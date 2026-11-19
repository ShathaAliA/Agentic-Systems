"""Add central policy versions and append-only role policy history."""
from alembic import op
import sqlalchemy as sa

revision = "rbac001"
down_revision = "usage001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("role_policies",
        sa.Column("role_id", sa.String(36), sa.ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_role_policy_version"))
    op.create_table("role_policy_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("role_id", sa.String(36), sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False), sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_role_policy_audit_events_role_id", "role_policy_audit_events", ["role_id"])
    # Existing mappings are intentionally retained. Version 1 represents the
    # pre-API baseline, including roles created after this migration.


def downgrade():
    op.drop_table("role_policy_audit_events")
    op.drop_table("role_policies")
