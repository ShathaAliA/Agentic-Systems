"""Register archive permission; preserve all existing role policies unchanged."""
from alembic import op
import sqlalchemy as sa
revision = "rbac002_archive"
down_revision = "usage004_fence"
branch_labels = None
depends_on = None

def upgrade():
    op.execute(sa.text("INSERT INTO permissions (id, code, description) SELECT 'documents-archive-permission', 'documents:archive', 'Archive documents without deleting originals' WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code='documents:archive')"))

def downgrade():
    raise RuntimeError("Permission rollback requires an explicit recovery plan")
