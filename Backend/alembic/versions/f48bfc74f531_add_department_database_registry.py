
"""add department database registry

Revision ID: f48bfc74f531
Revises: b8e1db57a78c
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f48bfc74f531"
down_revision: Union[str, Sequence[str], None] = "b8e1db57a78c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "department_databases",

        sa.Column(
            "id",
            sa.String(length=36),
            primary_key=True,
            nullable=False,
        ),

        sa.Column(
            "department_id",
            sa.String(length=36),
            nullable=False,
        ),

        sa.Column(
            "database_name",
            sa.String(length=100),
            nullable=False,
        ),

        sa.Column(
            "credential_key",
            sa.String(length=150),
            nullable=False,
        ),

        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            ondelete="CASCADE",
        ),

        sa.UniqueConstraint("department_id"),
        sa.UniqueConstraint("database_name"),

        sa.CheckConstraint(
            "status IN ('provisioning', 'active', 'failed')",
            name="ck_department_databases_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("department_databases")