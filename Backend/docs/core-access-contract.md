# Core API access contract — Subtask 2.5

This contract covers the current Core application only: authentication,
departments, users/memberships, role policies and usage. It does not establish
that document/RAG routes are protected or that a tenant document schema is ready.

## Policy definitions

- `public`: minimal root/liveness response; no database probe.
- `login`: development login, governed by explicit authentication configuration,
  password verification and shared account/IP/global rate limits. No bypass.
- `self`: validated current identity; own profile, sessions, grants, department
  metadata and usage. User IDs in query parameters or headers cannot select
  another user's records. A creation grant is still required to create a department.
- `department_admin`: validated identity plus GLOBAL `departments:manage`.
  A department-scoped assignment with that code does not satisfy the gate.
- `core_admin`: active, enrolled global Admin with `users:manage`, using the
  existing administrator dependency. Includes database health; it does not use
  provisioning/migration credentials.

The gates intentionally differ. Do not replace department administration with
users:manage, or turn the global checks into department-scoped permissions.
Admin resource mutations retain ownership, last-admin, expected-version, audit
and session-revocation controls in the respective services.

Missing/invalid/expired/revoked credentials or inactive/unknown identities return
401 with a Bearer challenge. A valid identity without permission returns 403.
Disabled development authentication returns 503 for protected operations.
Resource-specific 404/409/422 responses follow authorization. Sensitive responses,
including /health/db, use Cache-Control: no-store. Driver errors are sanitized.

## Explicit operation inventory

`tests/core_access_contract.py` contains the same 43 method/path cases with valid
request templates. The inventory gate inspects effective runtime routes, including
routes hidden from OpenAPI, rejects unclassified or duplicate operations, and
requires the validated-authentication dependency on every protected operation.
Tests do not add a runtime universal authorization bypass or trust a role header.

| Method | Path | Policy |
| --- | --- | --- |
| GET | `/` | public |
| GET | `/health` | public |
| POST | `/auth/dev/login` | login |
| GET | `/health/db` | core_admin |
| GET | `/auth/me` | self |
| POST | `/auth/logout` | self |
| GET | `/me/department-creation-grant` | self |
| POST | `/departments/from-grant` | self |
| GET | `/me/department` | self |
| POST | `/admin/department-creation-grants` | department_admin |
| GET | `/admin/department-creation-grants` | department_admin |
| POST | `/admin/department-creation-grants/{grant_id}/revoke` | department_admin |
| POST | `/admin/departments` | department_admin |
| GET | `/admin/departments` | department_admin |
| GET | `/admin/departments/{department_id}` | department_admin |
| POST | `/admin/departments/{department_id}/archive` | department_admin |
| POST | `/admin/departments/{department_id}/head` | department_admin |
| GET | `/admin/users` | core_admin |
| POST | `/admin/users` | core_admin |
| GET | `/admin/users/{user_id}` | core_admin |
| PATCH | `/admin/users/{user_id}/status` | core_admin |
| GET | `/admin/roles` | core_admin |
| GET | `/admin/users/{user_id}/memberships` | core_admin |
| PUT | `/admin/users/{user_id}/memberships/{department_id}` | core_admin |
| GET | `/admin/users/{user_id}/roles` | core_admin |
| POST | `/admin/users/{user_id}/roles` | core_admin |
| DELETE | `/admin/users/{user_id}/roles/{assignment_id}` | core_admin |
| GET | `/admin/users/{user_id}/audit` | core_admin |
| GET | `/admin/permissions` | core_admin |
| GET | `/admin/roles/{role_id}` | core_admin |
| GET | `/admin/roles/{role_id}/permissions` | core_admin |
| PUT | `/admin/roles/{role_id}/permissions` | core_admin |
| GET | `/admin/roles/{role_id}/permissions/audit` | core_admin |
| GET | `/me/usage` | self |
| GET | `/me/usage/ledger` | self |
| GET | `/me/usage/requests` | self |
| GET | `/admin/usage/policy` | core_admin |
| PUT | `/admin/usage/policy` | core_admin |
| GET | `/admin/usage/policy/history` | core_admin |
| GET | `/admin/usage/summary` | core_admin |
| GET | `/admin/usage/users/{user_id}` | core_admin |
| GET | `/admin/usage/ledger` | core_admin |
| GET | `/admin/usage/unresolved` | core_admin |

The existing `/docs`, `/docs/oauth2-redirect`, `/redoc` and `/openapi.json` are
explicit public development tooling exceptions, checked separately by the gate.
They expose API schemas, not database credentials or application records.
Production documentation exposure policy is a separate deployment decision.

## Scope and lifecycle rules

Department business permission checks require an active user, an active,
unarchived department, and either a matching global permission or an active
membership plus a matching department role. HR access never implies Finance
access. An active Department row without an active registry is not enough to
open a tenant connection. The Core router tests verify refusal before requesting
an engine for wrong departments, failed/archived departments and failed registries.

Admin can inspect provisioning status. A head's own `/me/department` may also show
failed/provisioning metadata for follow-up; this is intentional and grants no
connection to the department database. Legal remains failed pending the real
document migrations and isolation checks; do not use metadata visibility to
infer tenant readiness. Direct service/SQL calls remain a trusted boundary.

## Verification commands

From Backend with the isolated development Python environment:

```powershell
../.local/rbac-venv/Scripts/python.exe -m pytest -q -m 'not integration' --tb=short -p no:cacheprovider
```

From repository root (fresh backed-up cluster, stopped and retained afterward):

```powershell
./Backend/tools/test-auth-postgres-native.ps1 -TestFile tests/test_core_access_postgres.py
# Docker alternative when its engine is working:
./Backend/tools/test-auth-postgres.ps1 -TestFile tests/test_core_access_postgres.py
```

Never run the legacy root-level database scripts as a substitute for this suite.
No new runtime dependencies, secrets, environment settings, database schema or
original-cluster privilege changes are required for Subtask 2.5. The PostgreSQL
fixture grants only the extra central reads needed by the matrix and existing
user-status/audit capabilities in a newly created synthetic cluster.

Document developer integration must use validated identity and authorized tenant
routing, preserve these guards and add independent document-level audience tests.
This work does not implement document permissions, OCR, retrieval, Entra or release.

Actual test results and remaining limitations: [verification](core-access-verification.md).
