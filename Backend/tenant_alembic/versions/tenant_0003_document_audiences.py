"""Document audiences: (department_id, role_id) pairs allowed per document.

Revision ID: tenant_0003
Revises: tenant_0002
"""
from alembic import op
import sqlalchemy as sa


revision = "tenant_0003"
down_revision = "tenant_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_audiences",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("department_id", sa.String(36), nullable=True),  # NULL = all departments
        sa.Column("role_id", sa.String(36), nullable=True),        # NULL = all roles
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.CheckConstraint(
            "(department_id IS NOT NULL) OR (role_id IS NOT NULL)",
            name="ck_audience_not_both_null",
        ),
        sa.UniqueConstraint(
            "document_id", "department_id", "role_id",
            name="uq_audience_doc_dept_role",
        ),
    )
    op.create_index(
        "ix_audience_document_id", "document_audiences", ["document_id"]
    )
    op.create_index(
        "ix_audience_dept_role", "document_audiences", ["department_id", "role_id"]
    )


def downgrade() -> None:
    raise RuntimeError("Tenant rollback is intentionally not automated")