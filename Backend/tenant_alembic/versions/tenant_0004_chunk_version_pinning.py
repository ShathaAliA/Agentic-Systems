"""Pin chunks to document versions; add immutability for audit.

Revision ID: tenant_0004
Revises: tenant_0003
"""
from alembic import op
import sqlalchemy as sa


revision = "tenant_0004"
down_revision = "tenant_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Add version_number to document_chunks (default 1 for existing rows)
    op.add_column(
        "document_chunks",
        sa.Column("version_number", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_document_chunks_doc_version",
        "document_chunks",
        ["document_id", "version_number"],
    )

    # 2) Add version_number to document_audit_logs (nullable — legacy)
    op.add_column(
        "document_audit_logs",
        sa.Column("version_number", sa.Integer, nullable=True),
    )

    # 3) Add version_number to document_revision_logs
    op.add_column(
        "document_revision_logs",
        sa.Column("from_version", sa.Integer, nullable=True),
    )
    op.add_column(
        "document_revision_logs",
        sa.Column("to_version", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError("Tenant rollback is intentionally not automated")