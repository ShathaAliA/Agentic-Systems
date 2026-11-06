from sqlalchemy import delete, select, update
from app.auth.models import DevelopmentSession
from app.departments.service import DepartmentError
from app.models import Permission, Role, RoleAssignment, RolePermission, utc_now
from app.users.service import approved_role, writer_lock
from app.role_policies.models import RolePolicy, RolePolicyAudit

READ = frozenset({"documents:read"})
DEPARTMENT = READ | {"documents:upload", "documents:update", "documents:manage_permissions", "audit:read"}
ALLOWED = {"Employee": READ, "Department Manager": DEPARTMENT, "Deputy Department Manager": DEPARTMENT}


def snapshot(session, role_id):
    role = approved_role(session, role_id)
    # One statement keeps the mapping and version in the same MVCC snapshot.
    rows = session.execute(select(Permission.code, RolePolicy.version).select_from(Role)
        .outerjoin(RolePolicy, RolePolicy.role_id == Role.id)
        .outerjoin(RolePermission, RolePermission.role_id == Role.id)
        .outerjoin(Permission, Permission.id == RolePermission.permission_id)
        .where(Role.id == role_id).order_by(Permission.code)).all()
    codes = [row[0] for row in rows if row[0] is not None]
    version = rows[0][1] if rows and rows[0][1] is not None else 1
    allowed = ALLOWED.get(role.name, frozenset())
    return {"role_id": role_id, "version": version,
            "editable": role.scope == "department", "permissions": codes,
            "allowed_permissions": sorted(allowed),
            "unsupported_permissions": sorted(set(codes) - allowed) if role.scope == "department" else []}


def replace(session, actor, role_id, expected_version, codes):
    writer_lock(session, actor)
    role = approved_role(session, role_id)
    if role.scope != "department":
        raise DepartmentError(403, "global_policy_immutable")
    if len(codes) != len(set(codes)) or not set(codes) <= ALLOWED.get(role.name, frozenset()):
        raise DepartmentError(422, "permission_mapping_denied")
    permissions = list(session.scalars(select(Permission).where(Permission.code.in_(codes))))
    if len(permissions) != len(codes):
        raise DepartmentError(422, "unknown_permission")
    previous = snapshot(session, role_id)
    if previous["version"] != expected_version:
        raise DepartmentError(409, "stale_role_policy")
    if session.scalar(select(RoleAssignment.id).where(RoleAssignment.role_id == role_id,
        RoleAssignment.user_id == actor).limit(1)):
        raise DepartmentError(403, "self_role_policy_change_denied")
    if previous["permissions"] == sorted(codes):
        return previous
    policy = session.get(RolePolicy, role_id)
    if policy is None:
        policy = RolePolicy(role_id=role_id, version=1)
        session.add(policy)
    policy.version = expected_version + 1
    session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
    session.add_all([RolePermission(role_id=role_id, permission_id=p.id) for p in permissions])
    holders = select(RoleAssignment.user_id).where(RoleAssignment.role_id == role_id)
    session.execute(update(DevelopmentSession).where(DevelopmentSession.user_id.in_(holders),
        DevelopmentSession.revoked_at.is_(None)).values(revoked_at=utc_now()))
    session.add(RolePolicyAudit(role_id=role_id, actor_user_id=actor, version=policy.version,
        before=previous["permissions"], after=sorted(codes)))
    session.flush()
    return snapshot(session, role_id)
