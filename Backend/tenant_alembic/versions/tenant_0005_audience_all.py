"""Allow (NULL, NULL) audience — all departments + all roles.

Revision ID: tenant_0005
Revises: tenant_0004
"""
from alembic import op


revision = "tenant_0005"
down_revision = "tenant_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_audience_not_both_null",
        "document_audiences",
        type_="check",
    )


def downgrade() -> None:
    raise RuntimeError("Tenant rollback is intentionally not automated")