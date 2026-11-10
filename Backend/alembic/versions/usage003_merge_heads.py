"""Reconcile the published result-column branch with the Core RBAC head.

The nullable legacy result_payload column is deliberately unused. No existing
content is copied or deleted by this migration; tenant result storage is separate.
"""
revision = "usage003_merge"
down_revision = ("rbac001", "usage002_result_payload")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError("Central history rollback requires an explicit recovery plan")
