"""Synthetic contract probes only; never a production document migration."""
from alembic import op
import sqlalchemy as sa
revision = "contract_test_0002"
down_revision = "tenant_0001"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("contract_records", "contract_events"):
        op.create_table(name, sa.Column("id", sa.Integer(), primary_key=True),
                        sa.Column("payload", sa.Text(), nullable=False))


def downgrade():
    raise RuntimeError("Synthetic fixture is retained; no automatic destructive rollback")
