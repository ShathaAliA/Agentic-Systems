# Subtask 2.2 verification — 2026-11-03

Branch: `feature/core-backend`, based on published `8c2b408`. Implementation is
prepared locally for review; no push, merge, deployment or subsequent subtask.

## Actual checks

| Command/check | Result |
| --- | --- |
| `pytest -q -m 'not integration' --tb=short` | **91 passed**, 10 deselected; 31.95 seconds |
| Isolated PostgreSQL department suite | **7 passed**; 8.34 seconds |
| Isolated PostgreSQL authentication regression suite | **2 passed**; 5.73 seconds |
| Python compilation and Git diff whitespace checks | Passed |

The 91-test suite includes all 63 previous non-integration tests and 28 new checks.
Coverage includes scoped authentication/authorization, forged body identities,
pending and revoked grants, one owned department, Admin direct creation and explicit
head binding, safe responses, transaction rollback when audit insertion fails,
archived department access denial, worker completion fences on success/failure,
legacy-name migration preflight and explicit idempotent development fixtures.

PostgreSQL department tests used a dedicated restricted API identity. They verified:

- Four concurrent consumption requests create exactly one department and one scoped
  manager assignment, with the other requests receiving 409.
- Concurrent identical-name reservations and competing grants for the same head
  admit only one reservation. Consume-versus-revoke races resolve consistently.
- Removing audit INSERT permission forces a complete registration rollback.
- Archival while the worker is paused in verification fences its completion and
  prevents later activation/retry. PostgreSQL row/advisory locking was real; the
  resource adapter and tenant migration were synthetic. This does not establish
  real document-schema readiness.
- The API identity can bind a head and archive using narrowly scoped column grants;
  it cannot edit grant names, change provisioning status to active, delete history,
  or update/delete audit events (SQLSTATE 42501).

Both suites migrated through `dept001` on separate PostgreSQL 17.10 containers,
after capturing owner/ACL snapshots and database/global backups. The migration
preserved the synthetic pre-existing failed department and reserved its name without
inventing an owner. Fresh central migrations started with zero departments.
Private artifacts and stopped test containers are retained under ignored local
state, including department run `0d9552fe2da1` and auth run `7f20a5b48b61`.

One non-failing warning remains: Starlette deprecates its HTTPX TestClient adapter
in favor of HTTPX2. A controlled test-dependency update is outside this subtask.

## Original development cluster

The existing PostgreSQL container was started for read-only verification. No
migration, worker run, registration, archive, owner/password change or privilege
change was applied to it. Read-only results:

- HR and Finance: department and registry remain `active`.
- Legal: department and registry remain `failed`, with
  `tenant_contract_unavailable`.
- Central migration remains `p002`; authentication and department API activation
  have not been applied to this stack.
- HR/Finance owners remain `ai_user`, and their restricted app CONNECT grants are
  intact. The central owner and API/registry/provisioner CONNECT grants are unchanged.

Day 1's opt-in live provisioning test was **not** executed because it can modify
cluster ACLs. Its non-integration regressions passed. No existing database was
dropped, reset, overwritten or redirected. No document/RAG module was changed.

## Rollout limits and remaining work

Follow [setup and rollout](departments.md) before normal-stack activation: capture
backups/ACLs, stop old API and worker versions, migrate centrally with a separate
identity, review excessive existing grants, configure scoped manager RBAC and apply
restricted application grants. Reconcile existing head ownership explicitly. The
normal API role is never a PostgreSQL provisioning identity.

Ownership is permanent for this API: failed provisioning and archival retain the
head/name reservation. Replacement owners, unarchive, physical deletion and fixture
cleanup are deliberately excluded. Existing in-flight requests/connections are not
killed during archival; later authorization/routing denies access. A provisioning
attempt already running may leave retained resources, but cannot restore readiness.

No production identity-provider/browser flow, full restore drill, or production
capacity/security assessment was executed. Entra, real document tenant schemas,
retention automation and later project features remain separate work. The existing
seed file's line endings were normalized while adding its explicit development guard.
