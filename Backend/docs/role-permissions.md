# Roles & Permissions APIs (Subtask 2.4)

Development authentication is required. Every route requires an active, enrolled
global Admin with `users:manage`; request identity fields and unverified headers
cannot substitute for authentication. Existing role-assignment APIs are unchanged.

| Endpoint | Purpose |
| --- | --- |
| GET /admin/permissions | Permission catalog, offset/limit (maximum 100), ordered by code/id |
| GET /admin/roles/{id} | Approved role identity and scope |
| GET /admin/roles/{id}/permissions | Current mapping, version and allowed/unsupported codes |
| PUT /admin/roles/{id}/permissions | Replace mapping with expected_version and permissions |
| GET /admin/roles/{id}/permissions/audit | Ordered append-only history, offset/limit |

PUT body example: `{"expected_version":1,"permissions":["documents:read"]}`.
The permission must already exist in the central catalog. Empty sets are valid.
Version 1 is the existing baseline; reads do not write policy rows. Actual changes
increment the version and atomically append history and revoke all development
sessions of every holder of that role, across departments. No-op requests preserve
the version, sessions and history. Stale versions return 409; denied changes return
403/422. A holder of the target role cannot edit its policy, including an Admin
who also holds that department role. Another authorized Admin must perform it.

| Role | Allowed configurable permission codes |
| --- | --- |
| Admin | Immutable through these APIs |
| Employee | documents:read |
| Department Manager / Deputy Department Manager | documents:read, documents:upload, documents:update, documents:manage_permissions, audit:read |

No API creates roles/permissions, changes role scope or enables `documents:delete`.
Legacy mappings are preserved by migration and reported as unsupported, rather
than silently removed. An explicit accepted replacement can remove them. This
does not implement document authorization: document/RAG integration remains owned
by the document developer. Existing membership, active-department and archive
checks still apply in the authorization service.

## Setup and isolation

Migration `rbac001` follows `usage001`, adds only `role_policies` and
`role_policy_audit_events` plus an index, and changes no existing role mapping.
Use the separate migration identity and the existing migration URL-file mechanism.
Do not apply migrations or seed fixtures to the original development databases
without a separate backup/activation review. No new environment secrets are needed.

For a freshly migrated isolated development database, the restricted API identity
needs SELECT/INSERT/UPDATE on role_policies, SELECT/INSERT on
role_policy_audit_events, and SELECT/INSERT/DELETE on role_permissions, in addition
to existing user/RBAC reads and session UPDATE. Do not grant UPDATE/DELETE on audit
history, role/permission definition writes, DDL, CREATEDB, CREATEROLE or SUPERUSER.
Provisioning credentials remain separate and are never given to FastAPI.

Policy writers share the user-administration PostgreSQL transaction advisory lock,
recheck the actor after acquiring it, and compare the expected version. This
protects API-mediated concurrency with membership/role/user administration. Direct
SQL by an operator can bypass application policy/version checks: restrict access
to the runtime credential and use reviewed migrations for offline RBAC changes.
The runtime's mapping DML capability remains a risk under application/SQL compromise;
the matrix and immutable Admin gate are application-enforced, not database RLS.

Run unit regressions inside the development image, and isolated PostgreSQL tests:

```powershell
./Backend/tools/test-auth-postgres.ps1 -TestFile tests/test_role_policies_postgres.py
```

The runner creates a new cluster with no published ports, captures owner/ACL
metadata and recovery backups before test grants, then stops and retains it.
It refuses the original development cluster. Legal must remain failed until the
real tenant document migration contract and isolation checks are available.

If Docker is unavailable on Windows, the independent native runner uses installed
PostgreSQL binaries, creates a fresh private cluster and a random loopback port,
backs up the empty cluster, runs one module, then stops and retains the cluster:

```powershell
./Backend/tools/test-auth-postgres-native.ps1
```

It defaults to PostgreSQL 18 and `.local/rbac-venv/Scripts/python.exe`; override
`-PostgresBin` / `-Python` for your installation. Install Backend requirements,
pytest and httpx in that private venv first. No environment-file changes are needed.

## Actual verification (2026-11-04)

- Python 3.13.14 / Windows: **148 non-integration tests passed**, 28 integration
  tests deselected; `pytest -q -m 'not integration' --tb=short -p no:cacheprovider`.
- Fresh native PostgreSQL 18.6 clusters: **28 integration tests passed** in separate
  runs: role policies 5, authentication 2, users 5, departments 7, usage 9.
- Policy tests verify first-write concurrency (one winner and three stale-version
  conflicts), actual advisory-lock waiting followed by fresh authorization,
  immutable Admin API policy, multi-holder session revocation, database-denied
  audit insertion rollback, migration reruns and restricted runtime privileges.
- Unit/API tests cover unauthenticated/non-Admin requests, role matrix and unknown
  codes, self-policy edits, strict bodies, no-op/stale versions, department/archive
  isolation, legacy unsupported deletion and immediate permission revocation.
- One pre-existing Starlette/httpx deprecation warning; no test failure.
- Docker execution was attempted but blocked by Docker Desktop's Windows socket
  initialization errors (`dockerInference`, `engine.sock`). Temporary sockets were
  moved to recoverable backup names via WSL; Docker still did not become ready.
  No factory reset, database deletion, security-disable action or Docker upgrade
  was performed. Docker/PostgreSQL 17 parity remains unverified in this run.
- No connection, migration, grant, worker or seed was run against the original
  HR/Finance/Legal stack. Legal's last verified status is failed; a fresh direct
  status check was unavailable because its Docker engine was down. Actual Legal
  tenant activation remains blocked by the document migration contract.
- Database changes were limited to fresh test clusters: complete migration chain
  through rbac001, synthetic fixtures, and restricted auth_test_api test grants.
  Owner/ACL snapshots, custom dumps and password-free global-role backups remain
  private under ignored `.local/native-auth-tests`; clusters were stopped, retained.

No integration with Entra, documents or RAG, production rollout, push or merge is
included. Cross-route Subtask 2.5 remains separate.
