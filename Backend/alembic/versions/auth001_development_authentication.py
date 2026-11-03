"""Add development credentials, revocable sessions and shared login limits."""
from alembic import op
import sqlalchemy as sa

revision = "auth001"
down_revision = "p002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("auth_development_credentials",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("version", sa.String(36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("auth_development_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("credential_version", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.create_index("ix_auth_development_sessions_user_id", "auth_development_sessions", ["user_id"])
    op.create_index("ix_auth_development_sessions_expires_at", "auth_development_sessions", ["expires_at"])
    op.create_table("auth_login_windows",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_auth_login_windows_expires_at", "auth_login_windows", ["expires_at"])


def downgrade():
    op.drop_table("auth_login_windows")
    op.drop_table("auth_development_sessions")
    op.drop_table("auth_development_credentials")
