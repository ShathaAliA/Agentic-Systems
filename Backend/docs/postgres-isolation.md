# Physical PostgreSQL isolation and security — Subtask 3.2

This tests real database/role boundaries on a NEW cluster, independent of HTTP
RBAC. It never operates on the original HR/Finance databases or Legal registration.
No document/RAG model or migration is changed, and no real department is activated.

## Executable environment

Use the private Python environment with Backend/requirements-test.txt. From root:

```powershell
python Backend/tools/run-core-tests.py --suite all --backend native
```

This now includes database_isolation in addition to the seven existing PostgreSQL
groups. Native mode uses Windows/PowerShell and PostgreSQL 18 by default. To run
just the new group with the same fresh-cluster safety and recovery workflow:

```powershell
./Backend/tools/test-auth-postgres-native.ps1 -TestFile tests/test_database_isolation_postgres.py
```

Docker mode has a matching allowlist entry, but was not executed in this run.
Do not replace the fresh runner with an existing database URL or legacy live script.

## Test identities and databases

The existing empty-cluster fixture establishes auth_test_db and its restricted
central API login. The new fixture creates two randomly named dept_<uuid> databases
labelled Synthetic HR and Synthetic Finance, a NOLOGIN owner and separate NOINHERIT
application login for each, plus a separate NOSUPERUSER provisioning login with
CREATEDB/CREATEROLE. Provisioning credentials are never used by API pools. Additional
NOLOGIN/canary roles exist only to test inherited privilege rejection.

Before setup, the runner saves a custom-format baseline central dump, a globals
backup without role passwords, and the fixture captures owners/ACLs. The physical
fixture records another catalog snapshot before its changes and before cluster
CONNECT hardening, plus a recovery plan. Credentials remain in a private store.
The dump + retained cluster + versioned fixtures provide a rebuild path into a
separate new cluster; there is no destructive restore or database deletion.

The fixture removes PUBLIC CONNECT only in that newly created cluster while
retaining explicit connections for its API/provisioning identities. Tenant owners
and app roles remain distinct. Both synthetic databases keep their data on replay.
No central registry entry is promoted to active for these synthetic physical probes.

## Proven Core fixes

1. Previously, ALTER DEFAULT PRIVILEGES automatically gave the runtime CRUD on
   future tables. A real test reproduced this. The adapter now removes that managed
   role's schema-scoped default grants and requires explicit reviewed object grants.
2. Previously, the pool guard accepted a member of pg_write_all_data despite its
   ability to write protected data. A real test reproduced this. The guard now
   rejects direct/transitive PostgreSQL predefined-role memberships, inherited
   administrative role flags (including REPLICATION), and membership of the current database owner.

The connection guard checks new physical connections. Operator role changes must
be coordinated with pool disposal/reverification; this is not continuous auditing
of already-open connections or protection against a trusted bootstrap compromise.

## Explicit internal grant policy interface

`PostgresProvisioner.grant_runtime` and `verify` accept keyword-only
`table_privileges` and `sequence_privileges` dictionaries. They are trusted operator/
migration-contract inputs, never request-body or employee-controlled values.

- Infrastructure identity/revision tables are SELECT-only and cannot be overridden.
- Business tables require an explicit mapping; a missing mapping raises the bounded
  runtime_grant_policy_missing error. An explicit empty mapping denies business access.
- Table operations can only be SELECT/INSERT/UPDATE/DELETE; no implicit TRUNCATE,
  REFERENCES or TRIGGER. An audit policy can allow SELECT/INSERT without UPDATE/DELETE.
- Sequence operations can only be USAGE/SELECT. UPDATE/setval is not granted.
- Unknown objects, protected names, invalid operations and duplicate entries fail.
- Managed lowercase object names are validated and identifiers are quoted.
- Objects omitted from the mapping, including later tables/sequences, have no rights.

Reconciliation removes and reapplies ONLY the managed application's object grants
inside one transaction. Verification checks effective table, column and sequence
privileges, including PUBLIC effects, GRANT OPTION and PostgreSQL privilege types
not present in the allowed policy. It rejects excessive global/default rights;
it does not silently revoke another identity's rights or globally repair PUBLIC
object ACLs. A failure rolls back the managed grant transaction. Explicit policy
replay preserves database OIDs, credentials and stored synthetic audit data.

## Document contract remains a blocker

No business policy has been invented for documents, versions, audits or RAG tables.
Subtask 3.3 now forwards the versioned contract's explicit privilege policy to
grant reconciliation and verification. The real document schema and its reviewed
policy are still unavailable: flipping a ready flag cannot establish readiness.
The document developer and Core must agree the pinned migration and policy.
See document-integration-contract.md for the V1 interface and ownership gates.
Foundation-only direct test migrations do not make the CLI contract ready.

Views, other schemas, function/extension privileges, column-specific grants and real
pgvector/document constraints require further contract review. Table-level runtime
permissions do not implement employee document audience restrictions. Those still
need API/RAG filtering and independent document tests. Legal remains failed until
all applicable schema, grant and isolation checks pass.

See postgres-isolation-verification.md for exact executed tests and remaining limits.
