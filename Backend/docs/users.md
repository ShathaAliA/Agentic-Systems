# Subtask 2.3: central user, membership and role administration

This module builds on authenticated central users and the Subtask 2.2 ownership
ledger. It does not import document/RAG services or open tenant databases. Safe
responses omit credentials, session digests, connection references and secrets.

## Access policy

User administration requires an active user with the canonical global `Admin` role
and its global `users:manage` permission. Department managers, deputies, employees
and other custom role names cannot use these APIs. The actor always comes from
validated authentication; user IDs in paths/bodies identify the target only.

The approved role catalog is fixed: global `Admin`; department-scoped
`Department Manager`, `Deputy Department Manager` and `Employee`. APIs cannot
create role definitions, change scopes or edit role permissions. Migration
`users001` adds the deputy role with **no automatic permissions**. Its reviewed
department permissions must be configured through the trusted RBAC setup process;
the API does not guess a document or human-support policy. Existing roles are
preserved. A pre-existing deputy role with the wrong scope aborts the migration.

Every write is serialized by a PostgreSQL transaction advisory lock. Authorization
is checked again after acquiring it, so an account disabled while waiting cannot
continue a previously authorized write. User changes, session invalidation and
audit insertion commit together. Failure of audit insertion rolls everything back.

An Admin cannot assign roles to themselves or activate their own membership. They
may remove their own roles or disable themselves only when the last-Admin guard
permits it. The guard preserves at least one active global Admin with
`users:manage` and an enrolled login identity. Currently authentication supports
development credentials only: unenrolled accounts do not count as recovery Admins
and cannot be granted the Admin role through this API. Future Entra integration
must supply its own trusted identity-readiness policy instead of counting local
credentials; Microsoft tokens are not implemented here.

New users are active central records with no roles, memberships or credentials.
Enrollment still uses Subtask 2.1's offline secret-protected CLI. No default
credential, universal Admin or password-setting HTTP endpoint is added. Email is
trimmed/lowercased on creation. A unique database index on `lower(trim(email))`
prevents case/space aliases, including concurrent requests. Ambiguous existing
emails abort migration rather than being merged or rewritten.

Membership activation requires an active user and active, unarchived department.
Deactivation preserves the membership row and role history. Department roles
require an active membership in the exact department; global roles must have no
department. Adding a manager role additionally requires an existing consumed
ownership ledger entry for that user and department, with no conflicting manager
assignment elsewhere. First ownership binding remains the Subtask 2.2 workflow.
Revoking a manager role does not release the permanent ownership/name reservation.

Changed user status, membership status, newly assigned roles and revoked roles
invalidate the target's existing development sessions in the same transaction.
Reactivation does not resurrect old bearer tokens. Idempotent no-op status,
membership and role assignments do not create duplicate audit events or repeated
session invalidations. A repeated role assignment returns the existing assignment;
revoking an already-absent assignment returns 404. Login must be repeated after a
real privilege change.

## HTTP routes

All paths below require the user-administration policy above. Lists default to
50 entries and cap at 100; offsets are non-negative and ordering is stable.

| Method/path | Input/result |
| --- | --- |
| `GET /admin/users` | Paginated safe users; optional active/inactive status filter |
| `POST /admin/users` | `{name,email}`; 201, no credentials or roles |
| `GET /admin/users/{id}` | Safe profile |
| `PATCH /admin/users/{id}/status` | `{status: active or inactive}` |
| `GET /admin/roles` | Approved, correctly scoped role catalog |
| `GET /admin/users/{id}/memberships` | Paginated current membership rows |
| `PUT /admin/users/{id}/memberships/{department_id}` | Activate/deactivate via `{status}` |
| `GET /admin/users/{id}/roles` | Paginated current role assignments |
| `POST /admin/users/{id}/roles` | `{role_id,department_id?}`; 201 |
| `DELETE /admin/users/{id}/roles/{assignment_id}` | Revoke assignment; 204 |
| `GET /admin/users/{id}/audit` | Paginated append-only change history |

Bodies reject extra fields. Missing/invalid authentication returns 401; forbidden
operations 403; missing targets 404; conflicts/last-Admin/ownership protections
409; invalid role scope/body 422; unavailable database 503. Errors do not return
submitted secret data or SQL parameters. Responses use `Cache-Control: no-store`.
Role revocation removes the current assignment while preserving its prior IDs and
scope in immutable audit events. Users, audit records and memberships have no
physical-deletion API. Names/emails cannot be edited through these endpoints.

## Operator setup (not applied to the original development cluster)

Before changing privileges, verify the target is development-only, inventory
owners/ACLs and legitimate identities, and retain recoverable database/global
backups. Stop traffic during migration and use the separate central migration
identity, never FastAPI credentials:

```sh
python -m alembic upgrade users001
```

This follows `dept001`. It adds the audit table, identity uniqueness index and
deputy role, but no users, departments or login credentials. Duplicate email or
wrong-scope deputy preflight errors require explicit operator reconciliation.
The fresh production database remains empty of departments; demo seeding remains
explicit development-only work. Do not run demo seeding on the existing cluster.

Keep the earlier RBAC SELECT/auth grants and, after reviewing any excessive
pre-existing/default grants, add only the new application privileges required:

```sql
-- psql api_role names the reviewed restricted API identity.
GRANT INSERT ON users, department_memberships, role_assignments TO :"api_role";
GRANT UPDATE(status) ON users, department_memberships TO :"api_role";
GRANT DELETE ON role_assignments TO :"api_role";
GRANT SELECT ON department_creation_grants TO :"api_role";
GRANT SELECT, INSERT ON user_audit_events TO :"api_role";
```

The API needs SELECT on central RBAC/users/memberships/assignments and existing
development credentials; session INSERT/SELECT and UPDATE for revocation are
already required by authentication. Do not grant audit UPDATE/DELETE, user DELETE,
role-definition writes, credential writes, schema ownership, CREATEDB, CREATEROLE
or SUPERUSER. Existing broad table grants override column-level restrictions;
review them from backups rather than blindly revoking legitimate access. No new
secrets or `.env` settings are required. Offline enrollment/provisioning identities
remain separate and must not be mounted into FastAPI.

Initial Admin bootstrap and emergency recovery are trusted offline operator tasks.
The HTTP API cannot bootstrap its own first Admin. Enroll a second authorized Admin
before retiring the first. Future identity providers must replace the current local
credential readiness and session invalidation adapters, retaining internal RBAC.

## Verification and recovery

```sh
python -m pytest -q -m "not integration" --tb=short
```

From the repository root, PowerShell 7.2+:

```powershell
./Backend/tools/test-auth-postgres.ps1 -TestFile tests/test_users_postgres.py
./Backend/tools/test-auth-postgres.ps1
./Backend/tools/test-auth-postgres.ps1 -TestFile tests/test_departments_postgres.py
```

Supply `-Docker <docker.exe-path>` when needed. The tool uses independent backed-up
PostgreSQL containers on internal networks without published ports; stopped test
containers and private recovery files are preserved. It waits for TCP readiness
so it cannot mistake the image's temporary initialization server for the final one.

Rollback starts with stopped API traffic, retained backups and captured grants.
Restore into a separate recovery database and verify before any manual cutover.
Downgrading `users001` destroys new audit history and removes email uniqueness;
never run it automatically. The deputy role is retained on downgrade to avoid
destroying approved assignments. No existing department database is deleted.

Not included: document/RAG integration, Microsoft identity, arbitrary role or
permission editors, profile identity changes, physical account deletion, tenant
activation or changing Legal. The original development stack remains unactivated
until separately authorized migration/grant review and rollout. Database owners
and offline operators remain a trusted boundary; this is not tamper-proof against
PostgreSQL superusers. See [actual test results](users-verification.md).
