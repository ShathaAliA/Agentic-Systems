"""Add handoff_knowledge (C06) — links closed handoff to Documents knowledge.

Revision ID: c06_0006
Revises: c06_0005
"""
from alembic import op
import sqlalchemy as sa

revision = "c06_0006"
down_revision = "c06_0005"


def upgrade():
    op.create_table(
        "handoff_knowledge",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("handoff_id", sa.String(36), nullable=False),
        sa.Column("request_key", sa.String(80), nullable=False),
        sa.Column("document_id", sa.Integer, nullable=False),
        sa.Column("document_version_id", sa.Integer, nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("handoff_id", "request_key", name="uq_handoff_knowledge_key"),
    )
    op.create_index("ix_handoff_knowledge_handoff", "handoff_knowledge", ["handoff_id"])

    # RLS
    op.execute("ALTER TABLE handoff_knowledge ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY handoff_knowledge_isolation ON handoff_knowledge
        USING (handoff_id IN (
            SELECT handoff_id FROM handoff_sessions
            WHERE department_id = current_setting('app.department_id', true)
        ))
    """)


def downgrade():
    op.execute("DROP POLICY IF EXISTS handoff_knowledge_isolation ON handoff_knowledge")
    op.drop_index("ix_handoff_knowledge_handoff", table_name="handoff_knowledge")
    op.drop_table("handoff_knowledge")