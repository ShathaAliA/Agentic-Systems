"""Central transactions only. These functions flush; the caller commits or rolls back."""
from datetime import datetime, timezone
import unicodedata
import uuid

from sqlalchemy import select, text

from app.departments.models import CreationGrant, DepartmentAudit
from app.models import (Department, DepartmentDatabase, DepartmentMembership,
                        Permission, Role, RoleAssignment, RolePermission, User)


class DepartmentError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def normalize_name(value):
    name = " ".join(unicodedata.normalize("NFKC", value).split())
    if not name or len(name) > 150 or any(unicodedata.category(c).startswith("C") for c in name):
        raise DepartmentError(422, "invalid_department_name")
    return name, name.casefold()


def is_admin(session, user_id):
    user = session.get(User, user_id)
    if user is None or user.status != "active":
        return False
    return session.scalar(select(Permission.id).join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id).join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .where(RoleAssignment.user_id == user_id, RoleAssignment.department_id.is_(None),
               Role.scope == "global", Permission.code == "departments:manage").limit(1)) is not None


def require_admin(session, user_id):
    if not is_admin(session, user_id):
        raise DepartmentError(403, "permission_denied")


def head_lock(session, user_id):
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                        {"key": "department-head:" + user_id})


def check_head(session, user_id, *, allow_grant=None):
    head_lock(session, user_id)
    user = session.scalar(select(User).where(User.id == user_id).execution_options(populate_existing=True))
    if user is None or user.status != "active":
        raise DepartmentError(409, "head_must_be_active")
    reservation = session.scalar(select(CreationGrant).where(
        CreationGrant.head_user_id == user_id, CreationGrant.state != "revoked"))
    if reservation is not None and reservation.id != allow_grant:
        raise DepartmentError(409, "head_already_reserved_or_owner")
    legacy = session.scalar(select(RoleAssignment.id).join(Role, Role.id == RoleAssignment.role_id)
        .where(RoleAssignment.user_id == user_id, RoleAssignment.department_id.is_not(None),
               Role.name == "Department Manager", Role.scope == "department").limit(1))
    if legacy is not None:
        raise DepartmentError(409, "existing_head_requires_reconciliation")


def audit(session, actor, action, grant=None, department=None):
    session.add(DepartmentAudit(actor_user_id=actor, action=action,
                                grant_id=grant, department_id=department))
    session.flush()


def ensure_name_free(session, key):
    if session.scalar(select(CreationGrant.id).where(CreationGrant.normalized_name == key,
                                                    CreationGrant.state != "revoked")):
        raise DepartmentError(409, "department_name_reserved")
    # Protect legacy records introduced outside the ledger, e.g. explicit dev fixtures.
    if any(normalize_name(name)[1] == key for name in session.scalars(select(Department.name))):
        raise DepartmentError(409, "department_name_exists")


def issue_grant(session, actor, head_user_id, name):
    require_admin(session, actor)
    name, key = normalize_name(name)
    check_head(session, head_user_id)
    ensure_name_free(session, key)
    grant = CreationGrant(name=name, normalized_name=key, head_user_id=head_user_id, created_by=actor)
    session.add(grant)
    session.flush()
    audit(session, actor, "grant_issued", grant.id)
    return grant


def revoke_grant(session, actor, grant_id):
    require_admin(session, actor)
    grant = session.get(CreationGrant, grant_id)
    if grant is None:
        raise DepartmentError(404, "grant_not_found")
    if grant.head_user_id:
        head_lock(session, grant.head_user_id)
    grant = session.scalar(select(CreationGrant).where(CreationGrant.id == grant_id)
                           .with_for_update().execution_options(populate_existing=True))
    if grant.state == "consumed":
        raise DepartmentError(409, "grant_already_consumed")
    if grant.state != "revoked":
        grant.state, grant.revoked_at = "revoked", datetime.now(timezone.utc)
        audit(session, actor, "grant_revoked", grant.id)
    return grant


def register(session, actor, grant):
    role = None
    if grant.head_user_id:
        role = session.scalar(select(Role).where(Role.name == "Department Manager", Role.scope == "department"))
        if role is None:
            raise DepartmentError(503, "department_head_role_not_configured")
    department = Department(name=grant.name, status="provisioning")
    session.add(department)
    session.flush()
    key = uuid.uuid4().hex
    session.add(DepartmentDatabase(department_id=department.id, database_name="dept_" + key,
                                   credential_key="DEPT_" + key.upper(), status="provisioning"))
    grant.state, grant.department_id = "consumed", department.id
    grant.consumed_at = datetime.now(timezone.utc)
    session.add(grant)
    if role is not None:
        session.add(DepartmentMembership(user_id=grant.head_user_id, department_id=department.id))
        session.add(RoleAssignment(user_id=grant.head_user_id, role_id=role.id, department_id=department.id))
    session.flush()
    audit(session, actor, "department_registered", grant.id, department.id)
    return department


def consume_grant(session, actor):
    head_lock(session, actor)
    grant = session.scalar(select(CreationGrant).where(CreationGrant.head_user_id == actor,
        CreationGrant.state != "revoked").with_for_update().execution_options(populate_existing=True))
    if grant is None:
        raise DepartmentError(403, "creation_grant_required")
    if grant.state != "pending":
        raise DepartmentError(409, "creation_grant_already_consumed")
    check_head(session, actor, allow_grant=grant.id)
    return register(session, actor, grant)


def admin_create(session, actor, name, head_user_id=None):
    require_admin(session, actor)
    name, key = normalize_name(name)
    if head_user_id:
        check_head(session, head_user_id)
    ensure_name_free(session, key)
    grant = CreationGrant(name=name, normalized_name=key, head_user_id=head_user_id, created_by=actor)
    # An unowned admin creation goes directly to consumed within the same transaction.
    if head_user_id is None:
        grant.state = "consumed"
        # register() assigns the department before this object is flushed.
        with session.no_autoflush:
            return register(session, actor, grant)
    session.add(grant)
    session.flush()
    return register(session, actor, grant)


def archive(session, actor, department_id):
    require_admin(session, actor)
    # Same lock order as worker finish: registry then department.
    registry = session.scalar(select(DepartmentDatabase).where(
        DepartmentDatabase.department_id == department_id).with_for_update())
    department = session.scalar(select(Department).where(Department.id == department_id)
                               .with_for_update().execution_options(populate_existing=True))
    if department is None:
        raise DepartmentError(404, "department_not_found")
    if department.archived_at is None:
        department.archived_at, department.archived_by = datetime.now(timezone.utc), actor
        if registry:
            registry.claim_token = None  # Fence any already-running provisioning attempt.
            registry.next_retry_at = None
            registry.verified_contract = None
        audit(session, actor, "department_archived", department=department.id)
    return department


def bind_head(session, actor, department_id, head_user_id):
    """Explicit admin reconciliation/assignment for an existing unowned department."""
    require_admin(session, actor)
    head_lock(session, head_user_id)
    user = session.get(User, head_user_id)
    if user is None or user.status != "active":
        raise DepartmentError(409, "head_must_be_active")
    session.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id == department_id).with_for_update())
    department = session.scalar(select(Department).where(Department.id == department_id).with_for_update())
    if department is None:
        raise DepartmentError(404, "department_not_found")
    if department.archived_at is not None:
        raise DepartmentError(409, "department_archived")
    grant = session.scalar(select(CreationGrant).where(CreationGrant.department_id == department_id).with_for_update())
    if grant is None:
        raise DepartmentError(409, "department_ledger_requires_migration")
    if grant.head_user_id == head_user_id:
        return department  # Idempotent reconciliation, never a role-repair bypass.
    if grant.head_user_id is not None:
        raise DepartmentError(409, "department_already_owned")
    if session.scalar(select(CreationGrant.id).where(CreationGrant.head_user_id == head_user_id,
                                                    CreationGrant.state != "revoked")):
        raise DepartmentError(409, "head_already_reserved_or_owner")
    role = session.scalar(select(Role).where(Role.name == "Department Manager", Role.scope == "department"))
    if role is None:
        raise DepartmentError(503, "department_head_role_not_configured")
    existing = session.execute(select(RoleAssignment.user_id, RoleAssignment.department_id)
        .where(RoleAssignment.role_id == role.id, RoleAssignment.department_id.is_not(None),
               (RoleAssignment.user_id == head_user_id) | (RoleAssignment.department_id == department_id))).all()
    if any(owner != head_user_id or target != department_id for owner, target in existing):
        raise DepartmentError(409, "existing_head_requires_reconciliation")
    membership = session.scalar(select(DepartmentMembership).where(
        DepartmentMembership.user_id == head_user_id, DepartmentMembership.department_id == department_id))
    if membership is None:
        session.add(DepartmentMembership(user_id=head_user_id, department_id=department_id))
    else:
        membership.status = "active"
    if not existing:
        session.add(RoleAssignment(user_id=head_user_id, role_id=role.id, department_id=department_id))
    grant.head_user_id = head_user_id
    audit(session, actor, "department_head_bound", grant.id, department_id)
    return department
