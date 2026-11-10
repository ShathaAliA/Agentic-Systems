"""Explicit API policy inventory. A new method/path requires review and a test case."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    policy: str
    body: dict | None = field(default=None)


OPERATIONS = [
    Operation("GET", "/", "public"),
    Operation("GET", "/health", "public"),
    Operation("POST", "/auth/dev/login", "login", {"email": "unknown@example.invalid", "password": "synthetic-invalid"}),
    Operation("GET", "/health/db", "core_admin"),
    Operation("GET", "/auth/me", "self"),
    Operation("POST", "/auth/logout", "self"),
    Operation("GET", "/me/department-creation-grant", "self"),
    Operation("POST", "/departments/from-grant", "self", {}),
    Operation("GET", "/me/department", "self"),
    Operation("POST", "/admin/department-creation-grants", "department_admin", {"head_user_id": "outsider", "name": "Synthetic"}),
    Operation("GET", "/admin/department-creation-grants", "department_admin"),
    Operation("POST", "/admin/department-creation-grants/{grant_id}/revoke", "department_admin"),
    Operation("POST", "/admin/departments", "department_admin", {"name": "Synthetic"}),
    Operation("GET", "/admin/departments", "department_admin"),
    Operation("GET", "/admin/departments/{department_id}", "department_admin"),
    Operation("POST", "/admin/departments/{department_id}/archive", "department_admin"),
    Operation("POST", "/admin/departments/{department_id}/head", "department_admin", {"head_user_id": "outsider"}),
    Operation("GET", "/admin/users", "core_admin"),
    Operation("POST", "/admin/users", "core_admin", {"name": "Synthetic", "email": "synthetic@example.invalid"}),
    Operation("GET", "/admin/users/{user_id}", "core_admin"),
    Operation("PATCH", "/admin/users/{user_id}/status", "core_admin", {"status": "inactive"}),
    Operation("GET", "/admin/roles", "core_admin"),
    Operation("GET", "/admin/users/{user_id}/memberships", "core_admin"),
    Operation("PUT", "/admin/users/{user_id}/memberships/{department_id}", "core_admin", {"status": "active"}),
    Operation("GET", "/admin/users/{user_id}/roles", "core_admin"),
    Operation("POST", "/admin/users/{user_id}/roles", "core_admin", {"role_id": "employee-user", "department_id": "finance"}),
    Operation("DELETE", "/admin/users/{user_id}/roles/{assignment_id}", "core_admin"),
    Operation("GET", "/admin/users/{user_id}/audit", "core_admin"),
    Operation("GET", "/admin/permissions", "core_admin"),
    Operation("GET", "/admin/roles/{role_id}", "core_admin"),
    Operation("GET", "/admin/roles/{role_id}/permissions", "core_admin"),
    Operation("PUT", "/admin/roles/{role_id}/permissions", "core_admin", {"expected_version": 1, "permissions": []}),
    Operation("GET", "/admin/roles/{role_id}/permissions/audit", "core_admin"),
    Operation("GET", "/me/usage", "self"),
    Operation("GET", "/me/usage/ledger", "self"),
    Operation("GET", "/me/usage/requests", "self"),
    Operation("GET", "/admin/usage/policy", "core_admin"),
    Operation("PUT", "/admin/usage/policy", "core_admin", {"enabled": True, "timezone": "UTC", "monthly_questions": 10, "daily_questions": 5, "rate_count": 2, "rate_seconds": 60, "monthly_budget_micro_usd": 1000, "expected_version": 0}),
    Operation("GET", "/admin/usage/policy/history", "core_admin"),
    Operation("GET", "/admin/usage/summary", "core_admin"),
    Operation("GET", "/admin/usage/users/{user_id}", "core_admin"),
    Operation("GET", "/admin/usage/ledger", "core_admin"),
    Operation("GET", "/admin/usage/unresolved", "core_admin"),
]
# Documents remain tenant-scoped; these entries also join the invalid-identity matrix.
OPERATIONS += [Operation(method, "/api/documents" + path, "document") for method, path in [
    ("POST", "/upload"), ("POST", "/{document_id}/approve"), ("POST", "/{document_id}/revise"),
    ("POST", "/ask"), ("GET", "/"), ("GET", "/download/{document_id}"),
    ("DELETE", "/{document_id}"), ("GET", "/{document_id}"), ("PATCH", "/{document_id}"),
    ("POST", "/{document_id}/version"), ("POST", "/{document_id}/permissions"),
    ("GET", "/{document_id}/permissions"), ("DELETE", "/{document_id}/permissions/{audience_id}")]]
TOOLING = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
PROTECTED = [op for op in OPERATIONS if op.policy not in {"public", "login"}]
ADMIN = [op for op in OPERATIONS if op.policy in {"core_admin", "department_admin"}]


def path_for(op):
    return op.path.format(user_id="outsider", department_id="finance", grant_id="missing",
                          role_id="employee-user", assignment_id="missing", document_id=1, audience_id=1)


def request(client, op, headers):
    return client.request(op.method, path_for(op), headers=headers, json=op.body)
