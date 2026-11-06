"""Provisioning identity only; document migrations extend this revision."""
from alembic import op
import sqlalchemy as sa

revision = "tenant_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "_provisioning_identity",
        sa.Column("singleton", sa.Integer, primary_key=True),
        sa.Column("department_id", sa.String(36), nullable=False),
        sa.CheckConstraint("singleton = 1", name="ck_provisioning_singleton"),
    )


def downgrade():
    raise RuntimeError("Tenant rollback is intentionally not automated by provisioning")
