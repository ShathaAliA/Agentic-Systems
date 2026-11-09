from unittest.mock import patch
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models import Department, DepartmentMembership, Permission, RoleAssignment, RolePermission, utc_now
from app.role_policies import service
from app.role_policies.models import RolePolicy, RolePolicyAudit
from app.services.authorization import has_permission
from test_authentication import login_headers
from test_users import users


def put(client, headers, role="employee-user", codes=None, version=1):
    return client.put(f"/admin/roles/{role}/permissions", headers=headers,
        json={"expected_version": version, "permissions": codes or []})


@pytest.mark.parametrize("path", ["/admin/permissions", "/admin/roles/employee-user",
    "/admin/roles/employee-user/permissions", "/admin/roles/employee-user/permissions/audit"])
def test_read_boundaries(users, path):
    c, _, h, _, _ = users
    assert c.get(path, headers={"X-User-ID": "admin"}).status_code == 401
    assert c.get(path, headers=h["employee"]).status_code == 403
    response = c.get(path, headers=h["admin"])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("role,codes,expected", [("administrator", [], 403),
    ("reader", [], 422), ("missing", [], 422), ("employee-user", ["users:manage"], 422),
    ("employee-user", ["documents:upload"], 422), ("manager-user", ["documents:delete"], 422),
    ("manager-user", ["unknown"], 422), ("employee-user", ["documents:read", "documents:read"], 422)])
def test_reject_unsafe_mappings(users, role, codes, expected):
    c, e, h, _, _ = users
    assert put(c, h["admin"], role, codes).status_code == expected
    with Session(e) as s:
        assert s.scalar(select(func.count()).select_from(RolePolicyAudit)) == 0


def test_version_noop_audit_and_all_holder_sessions(users):
    c, e, h, _, password = users
    with Session(e) as s:
        s.add_all([RoleAssignment(user_id="employee", role_id="employee-user", department_id="hr"),
            RoleAssignment(user_id="outsider", role_id="employee-user", department_id="finance")])
        s.commit()
    assert put(c, h["employee"]).status_code == 403
    assert put(c, {}).status_code == 401
    assert put(c, h["admin"], codes=["documents:read"]).json()["version"] == 1
    assert c.get("/auth/me", headers=h["employee"]).status_code == 200
    result = put(c, h["admin"])
    assert result.status_code == 200 and result.json()["version"] == 2
    for name in ("employee", "outsider"):
        assert c.get("/auth/me", headers=h[name]).status_code == 401
    assert c.get("/auth/me", headers=h["admin"]).status_code == 200
    assert put(c, h["admin"], codes=["documents:read"]).status_code == 409
    events = c.get("/admin/roles/employee-user/permissions/audit", headers=h["admin"]).json()
    assert len(events) == 1 and events[0]["before"] == ["documents:read"] and events[0]["after"] == []


def test_self_policy_and_unknown_existing_permission(users):
    c, e, h, _, _ = users
    with Session(e) as s:
        s.add(RoleAssignment(user_id="admin", role_id="employee-user", department_id="hr"))
        s.commit()
    assert put(c, h["admin"]).status_code == 403
    # An allowed code must also exist in the registered permission catalog.
    assert put(c, h["admin"], "manager-user", ["documents:upload"]).status_code == 422


def test_audit_failure_rolls_back_mapping_version_sessions(users):
    c, e, h, _, _ = users
    with Session(e) as s:
        s.add(RoleAssignment(user_id="employee", role_id="employee-user", department_id="hr"))
        s.commit()
    with patch.object(service, "RolePolicyAudit", side_effect=RuntimeError("synthetic audit failure")):
        with pytest.raises(RuntimeError):
            put(c, h["admin"])
    with Session(e) as s:
        assert s.get(RolePolicy, "employee-user") is None
        assert service.snapshot(s, "employee-user")["permissions"] == ["documents:read"]
    assert c.get("/auth/me", headers=h["employee"]).status_code == 200


def test_department_membership_archive_and_legacy_delete(users):
    c, e, h, deputy, _ = users
    with Session(e) as s:
        s.add(Permission(id="delete-legacy", code="documents:delete"))
        s.add(RolePermission(role_id="manager-user", permission_id="delete-legacy"))
        s.add(RoleAssignment(user_id="employee", role_id=deputy, department_id="hr"))
        s.commit()
        assert has_permission(s, "employee", "documents:read", "hr")
        assert not has_permission(s, "employee", "documents:read", "finance")
        s.get(Department, "hr").archived_at = utc_now()
        s.commit()
        assert not has_permission(s, "employee", "documents:read", "hr")
    value = c.get("/admin/roles/manager-user/permissions", headers=h["admin"]).json()
    assert value["unsupported_permissions"] == ["documents:delete"]
    assert put(c, h["admin"], "manager-user").status_code == 200


def test_pagination_strict_input_and_safe_swagger(users):
    c, _, h, _, _ = users
    first = c.get("/admin/permissions?limit=1", headers=h["admin"]).json()
    assert len(first) == 1
    assert c.get("/admin/permissions?limit=101", headers=h["admin"]).status_code == 422
    assert c.put("/admin/roles/employee-user/permissions", headers=h["admin"],
        json={"expected_version": 1, "permissions": [], "scope": "global"}).status_code == 422
    assert "/admin/roles/{role_id}/permissions" in c.get("/openapi.json").json()["paths"]


def test_revocation_effective_without_broadening_department_access(users):
    c, e, h, deputy, _ = users
    with Session(e) as s:
        s.add(Permission(id="upload", code="documents:upload"))
        s.add(RoleAssignment(user_id="employee", role_id=deputy, department_id="hr"))
        s.commit()
    assert put(c, h["admin"], deputy, ["documents:read", "documents:upload"]).status_code == 200
    with Session(e) as s:
        assert has_permission(s, "employee", "documents:upload", "hr")
        assert not has_permission(s, "employee", "documents:upload", "finance")
    assert put(c, h["admin"], deputy, ["documents:read"], version=2).status_code == 200
    with Session(e) as s:
        assert not has_permission(s, "employee", "documents:upload", "hr")
