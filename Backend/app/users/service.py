from datetime import datetime, timezone
import re
import unicodedata

from sqlalchemy import select, text, update
from app.auth.models import DevelopmentCredential, DevelopmentSession
from app.departments.models import CreationGrant
from app.departments.service import DepartmentError
from app.models import (Department, DepartmentMembership, Permission, Role,
                        RoleAssignment, RolePermission, User)
from app.users.models import UserAudit

ROLE_SCOPES = {"Admin": "global", "Department Manager": "department",
               "Deputy Department Manager": "department", "Employee": "department"}


def admin_ids(session):
    # Current provider is development-password only. An unenrolled account cannot
    # be the recovery administrator. Entra must supply its own readiness policy.
    return set(session.scalars(select(User.id).join(DevelopmentCredential, DevelopmentCredential.user_id == User.id)
        .join(RoleAssignment, RoleAssignment.user_id == User.id)
        .join(Role, Role.id == RoleAssignment.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(User.status == "active", Role.name == "Admin", Role.scope == "global",
               RoleAssignment.department_id.is_(None), Permission.code == "users:manage")))


def require_admin(session, actor):
    # Re-query after waiting for the writer lock; do not trust a cached role/user.
    if actor not in admin_ids(session):
        raise DepartmentError(403, "user_administration_denied")


def writer_lock(session, actor):
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('user-administration',0))"))
    require_admin(session, actor)


def target(session, identity):
    user = session.scalar(select(User).where(User.id == identity).execution_options(populate_existing=True))
    if user is None:
        raise DepartmentError(404, "user_not_found")
    return user


def audit(session, actor, user_id, action, before=None, after=None):
    session.add(UserAudit(actor_user_id=actor, target_user_id=user_id, action=action,
                          before=before, after=after))
    session.flush()


def revoke_sessions(session, user_id):
    session.execute(update(DevelopmentSession).where(DevelopmentSession.user_id == user_id,
        DevelopmentSession.revoked_at.is_(None)).values(revoked_at=datetime.now(timezone.utc)))


def normalize_email(value):
    email = value.strip().lower()
    if len(email) > 255 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or any(unicodedata.category(c).startswith("C") for c in email):
        raise DepartmentError(422, "invalid_email")
    return email


def create_user(session, actor, name, email):
    writer_lock(session, actor)
    name = name.strip()
    if not name or len(name) > 150 or any(unicodedata.category(c).startswith("C") for c in name):
        raise DepartmentError(422, "invalid_user_name")
    email = normalize_email(email)
    from sqlalchemy import func
    if session.scalar(select(User.id).where(func.lower(func.trim(User.email)) == email)):
        raise DepartmentError(409, "email_already_registered")
    user = User(name=name, email=email, status="active")
    session.add(user)
    session.flush()
    audit(session, actor, user.id, "user_created", after={"status": "active"})
    return user


def set_status(session, actor, user_id, status):
    writer_lock(session, actor)
    user = target(session, user_id)
    if status not in {"active", "inactive"}:
        raise DepartmentError(422, "invalid_user_status")
    if user.status == status:
        return user
    admins = admin_ids(session)
    if status == "inactive" and user_id in admins and len(admins) == 1:
        raise DepartmentError(409, "last_active_admin_required")
    previous = user.status
    user.status = status
    revoke_sessions(session, user_id)
    audit(session, actor, user_id, "user_status_changed", {"status": previous}, {"status": status})
    return user


def department(session, department_id):
    row = session.get(Department, department_id)
    if row is None:
        raise DepartmentError(404, "department_not_found")
    if row.archived_at is not None or row.status != "active":
        raise DepartmentError(409, "department_not_active")
    return row


def set_membership(session, actor, user_id, department_id, status):
    writer_lock(session, actor)
    user = target(session, user_id)
    if status not in {"active", "inactive"}:
        raise DepartmentError(422, "invalid_membership_status")
    row = session.scalar(select(DepartmentMembership).where(DepartmentMembership.user_id == user_id,
        DepartmentMembership.department_id == department_id))
    if row is not None and row.status == status:
        return row
    if status == "active":
        if actor == user_id:
            raise DepartmentError(403, "self_membership_activation_denied")
        if user.status != "active":
            raise DepartmentError(409, "user_not_active")
        department(session, department_id)
    elif row is None:
        raise DepartmentError(404, "membership_not_found")
    previous = {"status": row.status} if row else None
    if row is None:
        row = DepartmentMembership(user_id=user_id, department_id=department_id, status=status)
        session.add(row)
    else:
        row.status = status
    revoke_sessions(session, user_id)
    audit(session, actor, user_id, "membership_changed", previous,
          {"department_id": department_id, "status": status})
    return row


def approved_role(session, role_id):
    role = session.get(Role, role_id)
    if role is None or ROLE_SCOPES.get(role.name) != role.scope:
        raise DepartmentError(422, "role_not_approved")
    return role


def grant_role(session, actor, user_id, role_id, department_id):
    writer_lock(session, actor)
    user = target(session, user_id)
    if actor == user_id:
        raise DepartmentError(403, "self_role_assignment_denied")
    if user.status != "active":
        raise DepartmentError(409, "user_not_active")
    role = approved_role(session, role_id)
    if role.scope == "global":
        if department_id is not None:
            raise DepartmentError(422, "global_role_has_no_department")
        if session.get(DevelopmentCredential, user_id) is None:
            raise DepartmentError(409, "admin_login_identity_required")
    else:
        if department_id is None:
            raise DepartmentError(422, "department_role_requires_department")
        department(session, department_id)
        membership = session.scalar(select(DepartmentMembership.id).where(
            DepartmentMembership.user_id == user_id, DepartmentMembership.department_id == department_id,
            DepartmentMembership.status == "active"))
        if membership is None:
            raise DepartmentError(409, "active_membership_required")
        if role.name == "Department Manager":
            another = session.scalar(select(RoleAssignment.id).where(RoleAssignment.user_id == user_id,
                RoleAssignment.role_id == role_id, RoleAssignment.department_id != department_id).limit(1))
            if another:
                raise DepartmentError(409, "existing_head_requires_reconciliation")
            owner = session.scalar(select(CreationGrant).where(CreationGrant.department_id == department_id,
                CreationGrant.head_user_id == user_id, CreationGrant.state == "consumed"))
            if owner is None:
                raise DepartmentError(409, "use_department_ownership_workflow")
    row = session.scalar(select(RoleAssignment).where(RoleAssignment.user_id == user_id,
        RoleAssignment.role_id == role_id, RoleAssignment.department_id == department_id))
    if row is None:
        row = RoleAssignment(user_id=user_id, role_id=role_id, department_id=department_id)
        session.add(row)
        revoke_sessions(session, user_id)
        audit(session, actor, user_id, "role_assigned", after={"role_id": role_id, "department_id": department_id})
    return row


def revoke_role(session, actor, user_id, assignment_id):
    writer_lock(session, actor)
    target(session, user_id)
    row = session.scalar(select(RoleAssignment).where(RoleAssignment.id == assignment_id,
        RoleAssignment.user_id == user_id))
    if row is None:
        raise DepartmentError(404, "role_assignment_not_found")
    role = approved_role(session, row.role_id)
    admins = admin_ids(session)
    if role.name == "Admin" and row.department_id is None and user_id in admins and len(admins) == 1:
        raise DepartmentError(409, "last_active_admin_required")
    previous = {"assignment_id": row.id, "role_id": row.role_id, "department_id": row.department_id}
    session.delete(row)
    revoke_sessions(session, user_id)
    audit(session, actor, user_id, "role_revoked", before=previous)
