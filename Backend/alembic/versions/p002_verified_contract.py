"""Fence activation against changes to the required tenant schema contract."""
from alembic import op
import sqlalchemy as sa

revision = "p002"
down_revision = "p001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("department_databases", sa.Column("verified_contract", sa.String(64)))


def downgrade():
    op.drop_column("department_databases", "verified_contract")
