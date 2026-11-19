"""Central department creation reservations, audit and archival metadata."""
from datetime import datetime, timezone
import unicodedata
import uuid

from alembic import op
import sqlalchemy as sa

revision = "dept001"
down_revision = "auth001"
branch_labels = None
depends_on = None


def upgrade():
    # Online data reconciliation is intentional: do not emit unchecked offline SQL.
    bind = op.get_bind()
    existing = bind.execute(sa.text("SELECT id,name FROM departments ORDER BY id")).all()
    reservations = []
    seen = set()
    for identity, name in existing:
        canonical = " ".join(unicodedata.normalize("NFKC", name).split())
        key = canonical.casefold()
        if (not canonical or len(canonical) > 150 or key in seen
                or any(unicodedata.category(c).startswith("C") for c in canonical)):
            raise RuntimeError("Department names require explicit reconciliation before dept001")
        seen.add(key)
        reservations.append(dict(id=str(uuid.uuid4()), name=canonical, normalized_name=key,
                                 department_id=identity, state="consumed", created_at=datetime.now(timezone.utc),
                                 consumed_at=datetime.now(timezone.utc)))
    op.add_column("departments", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("departments", sa.Column("archived_by", sa.String(36)))
    op.create_foreign_key("fk_department_archived_by", "departments", "users", ["archived_by"], ["id"], ondelete="RESTRICT")
    table = op.create_table("department_creation_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("normalized_name", sa.String(450), nullable=False),
        sa.Column("head_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("department_id", sa.String(36), sa.ForeignKey("departments.id", ondelete="RESTRICT"), unique=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('pending','consumed','revoked')", name="ck_creation_grant_state"),
        sa.CheckConstraint("(state='consumed' AND department_id IS NOT NULL) OR (state!='consumed' AND department_id IS NULL)", name="ck_creation_grant_department"),
        sa.CheckConstraint("state!='pending' OR head_user_id IS NOT NULL", name="ck_pending_grant_head"))
    op.create_index("uq_reserved_department_name", "department_creation_grants", ["normalized_name"], unique=True, postgresql_where=sa.text("state != 'revoked'"))
    op.create_index("uq_reserved_department_head", "department_creation_grants", ["head_user_id"], unique=True, postgresql_where=sa.text("state != 'revoked'"))
    if reservations:
        op.bulk_insert(table, reservations)
    op.create_table("department_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("grant_id", sa.String(36), sa.ForeignKey("department_creation_grants.id", ondelete="RESTRICT")),
        sa.Column("department_id", sa.String(36), sa.ForeignKey("departments.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("department_audit_events")
    op.drop_table("department_creation_grants")
    op.drop_constraint("fk_department_archived_by", "departments", type_="foreignkey")
    op.drop_column("departments", "archived_by")
    op.drop_column("departments", "archived_at")
