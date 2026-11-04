"""User audit, canonical email uniqueness, and a scoped deputy role."""
import uuid
from alembic import op
import sqlalchemy as sa

revision = "users001"
down_revision = "dept001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    duplicates = bind.execute(sa.text("SELECT lower(trim(email)) FROM users GROUP BY lower(trim(email)) HAVING count(*)>1")).first()
    if duplicates:
        raise RuntimeError("Existing email identities require reconciliation before users001")
    deputy = bind.execute(sa.text("SELECT scope FROM roles WHERE name='Deputy Department Manager'")).first()
    if deputy and deputy[0] != "department":
        raise RuntimeError("Deputy role scope requires explicit reconciliation")
    op.create_index("uq_users_normalized_email", "users", [sa.text("lower(trim(email))")], unique=True)
    op.create_table("user_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("before", sa.JSON()), sa.Column("after", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    if not deputy:
        bind.execute(sa.text("INSERT INTO roles(id,name,scope,description) VALUES (:id,'Deputy Department Manager','department','Department deputy; permissions require explicit policy configuration')"), {"id": str(uuid.uuid4())})


def downgrade():
    # Retain deputy role and existing assignments; do not destroy approved RBAC.
    op.drop_table("user_audit_events")
    op.drop_index("uq_users_normalized_email", table_name="users")
