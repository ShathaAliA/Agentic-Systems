"""Independent synthetic root used to reject non-tenant revision ancestry."""
from alembic import op
import sqlalchemy as sa
revision = "unrelated_test_root"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("unrelated_probe", sa.Column("id", sa.Integer(), primary_key=True))


def downgrade():
    raise RuntimeError("No automatic destructive rollback")
