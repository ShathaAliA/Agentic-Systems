# Subtask 3.2 verification — 2026-11-05

Base: published 4d1ce02 on feature/core-backend. Scope is physical PostgreSQL
isolation and Core privilege hardening. No document/RAG module, central/tenant
migration, existing HR/Finance database or actual Legal registration was modified.

## Reproduced defects and fixes

Real tests on newly created clusters first reproduced two failures: future tables
received implicit runtime CRUD, and a pg_write_all_data member was accepted by the
API pool guard. Follow-up coverage reproduced another omission: a member able to
SET a REPLICATION role was also accepted. The final guard rejects all three cases,
including transitive predefined membership and current database-owner membership.

The grant adapter now requires a reviewed business-object policy, removes the
managed role's schema-scoped default object grants, verifies effective table/
column/sequence permissions, and fails without silently repairing PUBLIC/global
ACLs. Invalid policies and grant verification failures preserve the transaction's
prior managed grants. Required metadata remains SELECT-only.

## Executed checks

Environment: Windows, Python 3.13.14, PowerShell 7 and PostgreSQL 18.6. Command:

```powershell
.local/rbac-venv/Scripts/python.exe Backend/tools/run-core-tests.py --suite all --backend native
```

| Group | Passed |
| --- | ---: |
| Non-integration unit/API and safety suites | 199 |
| Existing seven PostgreSQL groups | 37 |
| New physical database isolation group | 19 |
| Total isolated PostgreSQL | 56 |

The full non-integration run passed 199 cases. After adding the final object/column
GRANT OPTION and unlisted-privilege checks, the PostgreSQL groups were rerun with
`--suite integration --backend native`; the final physical group contains 19 cases.

The physical group tests:

- Successful connection/identity and permitted data operations in each own DB.
- Denied tenant-to-tenant, tenant-to-central/maintenance and central-to-tenant connections.
- Denied schema/table/TEMP DDL, ALTER, CREATE DATABASE/ROLE, SET ROLE, protected
  metadata writes, audit UPDATE/DELETE/TRUNCATE, sequence setval and server-file reads.
- Explicit append-only audit policy, schema/default privilege rejection, and
  table/column GRANT OPTION or unlisted privileges (including MAINTAIN) rejection.
- Replay preserves database OIDs, credentials and synthetic audit rows.
- Missing/invalid policies make no grant change; PUBLIC column leaks roll back
  grant reconciliation without silently removing PUBLIC's grant.
- Global default leaks, incorrect identity/revision and CONNECT overgrants fail.
- API pool rejection of provisioning identity, predefined roles, owner membership,
  direct/transitive role privileges and REPLICATION role membership.
- Excess runtime object ownership/membership is rejected; explicit policy removes
  a deliberately injected old runtime table overgrant.

Twelve new unit cases cover policy validation and rejecting unmanaged targets
before opening a connection. All original unit/API and PostgreSQL suites also
passed. One pre-existing Starlette/httpx deprecation warning remains.

## Database-level changes (isolated test cluster only)

The original empty-cluster runner first produced recovery dumps and password-free
global-role backup. The fixture captures owners, database ACLs, roles/memberships
before creating new identities, again before its cluster CONNECT changes, and
at completion. A private recovery-plan JSON describes rebuilding into a separate
new cluster using backups and versioned fixtures, without destructive restoration.

The physical fixture adds two randomly named dept_<uuid> databases, distinct
NOLOGIN owners and restricted app roles, one separate non-superuser CREATEDB/
CREATEROLE provisioning login, and synthetic privilege-canary roles. It uses the
existing foundation migration plus test-only record/audit/future-probe tables and
sequences, with explicit grants. PUBLIC CONNECT is restricted only in this new
cluster, preserving explicit intended test connections. Transient overgrants,
owner changes and membership probes are restored/reconciled in test finalizers.

Native clusters, private credential files, before/after catalog snapshots and
backups are stopped and retained under ignored .local. No database is deleted;
no test role or backup secret is committed. The reports are redacted/private as
specified by the 3.1 runner. Baseline backup catalog readability is checked; a
company-data restore is not claimed or performed.

## Remaining boundaries

- No real department is activated by this physical probe. The synthetic databases
  are not registered as active Core tenants. The real Legal state remains untouched;
  its last directly verified state is failed, without a fresh original-stack query.
- The document tenant contract remains unavailable. Once business tables exist,
  the current worker deliberately fails runtime_grant_policy_missing until Core
  and the document developer agree and wire a reviewed privilege policy in 3.3.
  No document-specific grants or migrations have been invented in this task.
- This establishes database/role isolation, not complete document audience security,
  OCR/RAG quality or pgvector readiness. Views, functions, extensions, other schemas
  and column-specific grants need a reviewed contract extension.
- Role guards apply to new physical connections; operator privilege changes need
  coordinated pool disposal/reverification. A trusted bootstrap/owner compromise
  remains outside the API runtime guard's boundary.
- Docker/PostgreSQL 17 mode was updated to include the new test group but not
  executed here. Native PostgreSQL 18 is the actually verified environment.
- No original-cluster live script, push, merge, deployment or subsequent task was run.
