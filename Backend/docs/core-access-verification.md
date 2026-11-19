# Subtask 2.5 verification — 2026-11-05

Scope: Core Backend on feature/core-backend, based on 923d26e. No document/RAG
module changes, external-provider calls, tenant activation or original-stack
migrations/grants were performed. Local review commits only; no push or merge.

## Change and compatibility

`GET /health/db` now requires the existing enrolled global Admin/users:manage
gate and executes SELECT 1 through the same guarded central session dependency
used by the API. It returns only a generic status and no-store headers; SQL errors
produce a sanitized 503. The public `/health` liveness response is unchanged.
Any caller previously using anonymous /health/db must account for its new policy.
Do not give monitoring processes provisioning credentials to access this endpoint.

The test inventory covers 43 API method/path pairs and four explicit development
tooling paths. Included and hidden routes are inspected at runtime, and every
protected operation must depend on validated authentication. Adding a route or
method requires updating its access policy and its request/authorization tests.
See core-access-contract.md for the complete policy matrix and commands.

## Actual results

Environment: Windows, Python 3.13.14, PostgreSQL 18.6, private ignored Python venv.

| Suite | Result |
| --- | --- |
| Complete non-integration suite | 175 passed, 35 integration cases deselected |
| New cross-router PostgreSQL matrix | 6 passed |
| PostgreSQL authentication regression | 2 passed |
| PostgreSQL users regression | 5 passed |
| PostgreSQL departments regression | 7 passed |
| PostgreSQL usage regression | 9 passed |
| PostgreSQL role policies regression | 5 passed |
| Total PostgreSQL tests executed | 34 passed |

The new unit/API module contributes 27 cases covering absent/forged/expired/
revoked/rotated credentials, unknown/inactive users, disabled authentication,
employee/head/deputy denials, malicious scoped management permissions, personal
resource isolation, sanitized health failures and permission withdrawal. SQL
instrumentation verifies invalid identities do not cause any INSERT/UPDATE/DELETE.

Lifecycle checks cover inactive memberships, removed role assignments, failed/
provisioning/archived departments, HR-versus-Finance scope and the head's explicit
failed-department metadata exception. Core router probes assert refusal before
requesting a tenant engine, using only an injected synthetic central database.

PostgreSQL tests use real restricted runtime credentials and verify that user
status changes revoke access across routers, fresh requests see membership and
department changes, and failed Legal fixture metadata does not grant tenant
access. The existing PostgreSQL concurrency/rollback suites also passed unchanged.
The cross-router fixture needed SELECT on department_databases for legitimate
department metadata reads; this grant was applied only to its newly created test
cluster, not to an existing development database.

One existing Starlette/httpx deprecation warning was present. Whitespace and Python
AST checks passed. No new schema, dependencies or environment secrets are needed.

## Boundaries and pending verification

- Every PostgreSQL module ran on a different fresh cluster with a random loopback
  port, private recovery dumps and owner/ACL snapshots before test grants. All test
  clusters were stopped and retained under ignored .local/native-auth-tests.
- The opt-in original-cluster provisioning integration was deliberately not run:
  it can change database-level grants and is unnecessary for this Core change.
- The original HR/Finance data and Legal provisioning state were not modified.
  Legal's last directly verified state remains failed; this run does not claim a
  fresh original-stack status check while Docker is unavailable.
- Docker/PostgreSQL 17 parity was not re-established. Native PostgreSQL 18 was the
  executable integration environment; the Docker runner accepts the new module
  for use after the engine is repaired.
- These tests establish Core authorization, not complete document-level audience
  enforcement or physical tenant document-schema isolation. Document migrations
  and end-to-end document tests remain required before Legal activation.
- API checks do not protect against a compromised trusted SQL/runtime identity;
  the previously documented direct-SQL and future identity-provider boundaries
  remain. Public developer documentation is not a production exposure decision.

No subsequent task, merge, deployment or publication is included.
