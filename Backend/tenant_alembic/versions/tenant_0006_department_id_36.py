"""Unify department_id to varchar(36) (matches Core).

Revision ID: tenant_0006
Revises: tenant_0005
"""
from alembic import op
import sqlalchemy as sa


revision = "tenant_0006"
down_revision = "tenant_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    bad = conn.execute(sa.text(
        "SELECT COUNT(*) FROM documents "
        "WHERE department_id IS NOT NULL AND length(department_id) > 36"
    )).scalar()
    if bad:
        raise RuntimeError(
            f"Cannot shrink department_id: {bad} rows exceed 36 chars"
        )

    op.alter_column(
        "documents",
        "department_id",
        existing_type=sa.String(50),
        type_=sa.String(36),
        existing_nullable=True,
    )


def downgrade() -> None:
    raise RuntimeError("Tenant rollback is intentionally not automated")