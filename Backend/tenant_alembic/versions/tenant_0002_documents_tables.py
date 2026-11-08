"""Tenant documents tables.

Revision ID: tenant_0002
Revises: tenant_0001
Create Date: 2026-11-08
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "tenant_0002"
down_revision = "tenant_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("filename", sa.String, nullable=False),
        sa.Column("file_path", sa.String, nullable=False),
        sa.Column("file_type", sa.String, nullable=True),
        sa.Column("extracted_text", sa.Text, nullable=True),
        sa.Column("department_id", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending_review"),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("upload_date", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_documents_department_id", "documents", ["department_id"])
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_version"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])

    op.create_table(
        "document_revision_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("old_text", sa.Text, nullable=True),
        sa.Column("new_text", sa.Text, nullable=False),
        sa.Column("revised_by", sa.String(36), nullable=False),
        sa.Column("revised_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_document_revision_logs_document_id", "document_revision_logs", ["document_id"])

    op.create_table(
        "document_audit_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("performed_by", sa.String(36), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_document_audit_logs_document_id", "document_audit_logs", ["document_id"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("page_number", sa.Integer, nullable=True),
        sa.Column("embedding", Vector(768)),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    raise RuntimeError("Tenant rollback is intentionally not automated")
