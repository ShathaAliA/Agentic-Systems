# Subtask 2.3 verification — 2026-11-04

Branch: `feature/core-backend`, based on published `59a834b`. Local implementation
only; no push, merge, deployment or document/RAG modifications.

## Executed checks

| Check | Actual result |
| --- | --- |
| Full non-integration suite | **107 passed**, 15 integration tests deselected; 40.69 seconds |
| Isolated PostgreSQL user-administration suite | **5 passed**; 7.76 seconds |
| Authentication PostgreSQL regression suite | **2 passed**; 7.11 seconds |
| Department PostgreSQL regression suite | **7 passed**; 10.38 seconds |
| Compilation and Git diff whitespace checks | Passed |

The 107-test suite includes all 91 earlier non-integration tests plus 16 user
administration checks. It covers missing/invalid credentials, forged actor headers,
non-Admin denial, safe profiles, identity uniqueness, extra-field rejection,
disable/reactivate session replay, self-promotion, last usable Admin, unenrolled
Admin records, role/dept scopes, multi-department memberships, ownership retention,
audit rollback and migration preflight without changing ambiguous legacy data.

Real PostgreSQL tests used a restricted API role and verified concurrent canonical
email creation, exact department memberships and deputy assignment, denied manager
ownership bypass, immediate token revocation, and rollback of user status plus
sessions when audit INSERT is denied. Two Admins concurrently disabling themselves
produce one success and one 409; independently, concurrent self-removal of Admin
roles produces one 204 and one 409. At least one usable Admin remains in both cases.

The API role was denied user DELETE, email UPDATE, role/permission definition writes,
audit UPDATE/DELETE and credential writes with SQLSTATE 42501. No privileged
PostgreSQL attributes were granted to the API. Test roles and grants exist only in
new isolated PostgreSQL 17.10 containers, with backups and owner/ACL snapshots taken
before changes. Migration `users001` applied there, preserving existing users,
departments and RBAC while adding audit, the unique email index and an unprivileged
department deputy role.

Private artifacts/stopped containers are retained in ignored local state: user run
`d36c509f8d44`, auth run `672effffcd76`, department run `fe9f8d53b385`. An earlier
department test setup failed before pytest while PostgreSQL was shutting down its
initialization server. The harness now waits for TCP readiness; the subsequent
department suite passed. No existing development database was involved in that
setup failure. Backup readability was checked; a full restore drill was not run.

One existing non-failing warning remains: Starlette deprecates the HTTPX TestClient
adapter in favor of HTTPX2. No unrelated dependency update was performed.

## Original development cluster

Started the existing PostgreSQL container solely for read-only inspection. Results:

- HR and Finance remain `active` in both department and registry.
- Legal remains `failed / tenant_contract_unavailable` in both records.
- Central migration remains `p002`; no new migrations or users/roles were applied.
- Central, HR and Finance owners remain `ai_user`; recorded restricted CONNECT
  grants to application/registry/provisioning identities are unchanged.

No user/status/membership/role write, worker operation, credential rotation,
database reset/deletion or ACL change was made in this original cluster. Day 1's
live mutating provisioning test was not rerun; its non-integration regressions passed.

## Remaining activation and security boundaries

Follow [user administration setup](users.md) before enabling the normal stack:
backups, reviewed central migration `users001`, restricted grants, trusted initial
Admin bootstrap/enrollment and reviewed deputy permissions. Existing broad/default
grants must be reviewed separately; the implementation does not silently revoke
legitimate access. The deputy role starts with no permissions rather than inventing
the document team's policy.

The current last-Admin readiness and session invalidation policy uses development
credentials. Entra must replace these provider-specific checks while retaining the
internal authorization and availability invariants. Offline DB operators remain a
trusted recovery boundary; no protection against a malicious superuser is claimed.

No physical user deletion, arbitrary policy editor, profile identity edits, document
integration, Legal activation, production SSO or production capacity assessment was
performed. Real deployment and original-stack activation require separate authorization.
