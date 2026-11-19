# Subtask 2.1 verification — 2026-11-02

Branch: `feature/core-backend`. Scope: authentication foundation only.

## Executed checks

| Check | Actual result |
| --- | --- |
| `pytest -q -m 'not integration' --tb=short` | **63 passed**, 3 integration tests deselected, 15.97 seconds |
| `tools/test-auth-postgres.ps1` | **2 passed**, 5.68 seconds; isolated PostgreSQL 17.10 |
| Python compilation with temporary bytecode directory | Passed |
| `git diff --check` | Passed; Windows line-ending normalization notices only |
| Remote branch refresh before committing | No divergence from `origin/feature/core-backend` |

The local suite includes all 33 Day 1 non-integration regressions plus 30 auth
checks. Integration used `ai-auth-test-10e97db9071d`, a new PostgreSQL container on
an internal network with no published ports. Backups, a readable dump catalog and
the initial owner/ACL snapshot were captured before privilege changes. Private
artifacts remain in ignored `.local/auth-tests/10e97db9071d/`; the container was
stopped and preserved. An earlier successful test run is preserved separately.

Integration covered applying `p002` then `auth001`, rerunning `auth001`, preserving
existing synthetic central users/departments/failed registry entries, login and
logout, global and department RBAC, failed-department denial, credential-version
revocation, and concurrent login counters. Of 15 simultaneous account attempts,
exactly 5 passed the limiter and 10 received 429.

The synthetic API role had no SUPERUSER, CREATEDB, CREATEROLE, REPLICATION or
BYPASSRLS attributes. Password-credential UPDATE/DELETE, session DELETE, public
schema DDL, users UPDATE and privileged catalog reads were rejected with SQLSTATE
42501. The Day 1 runtime guard rejected the test administrative identity.

One non-failing warning: the installed Starlette release deprecates its HTTPX
TestClient integration in favor of HTTPX2. HTTPX 0.28.1 ran these tests successfully.
The test-client dependency should be revisited with a controlled dependency update.

## Database-level changes

**Existing Day 1 development cluster: no DDL, DML, grants, ownership changes, or
password rotations.** Only read-only inspections were performed. Central remains
at `p002`, so auth is not activated there. HR and Finance remain active, owned by
`ai_user`, with their respective restricted app CONNECT grants intact. Both still
have zero public application tables, matching the recorded Day 1 baseline. Legal
remains failed with `tenant_contract_unavailable` in department and registry state.
Document schema delivery is still required before its provisioning can complete.

**New isolated test cluster only:** central migration chain applied through
`auth001`; three additive auth tables and their indexes created. Synthetic users,
RBAC and session/limiter records inserted. A dedicated restricted test API role
was created, PUBLIC CONNECT and schema CREATE restricted in that test database,
and narrowly scoped SELECT / session-and-counter INSERT/UPDATE grants assigned.
No database was dropped and no HR/Finance connection was redirected.

## Limits and pending operator work

- Apply the additive migration and review existing/default grants before opting
  into authentication on the normal development stack. Use the documented separate
  migration/enrollment identities; their secret files must not reach FastAPI.
- This verifies HTTP handlers using TestClient with both SQLite and real
  PostgreSQL. No service was deployed and no browser/Entra flow was tested.
- The live Day 1 provisioning integration was **not rerun** because it can modify
  cluster ACLs; its 33 non-integration regressions passed. Legal was only inspected.
- Development passwords lack production SSO/MFA/recovery. Entra is deliberately
  unimplemented. Unsupported identity-provider modes fail closed.
- Fixed login windows permit boundary bursts and temporary account lockout.
  Production edge/body-size limits and capacity testing remain deployment work.
- Offline retention of auth sessions/counters and an auth event audit store are
  not automated in this subtask. Maintain them under the agreed retention policy.

See [authentication setup and rollback](authentication.md). No Subtask 2.2,
automatic push, merge, deployment, or document/RAG changes are part of this task.
