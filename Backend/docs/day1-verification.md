# Day 1 development verification — 2 November 2026

Target: existing local Docker `ai_platform_postgres`, `postgres:17-alpine`,
loopback-only port5435, central DB `ai_platform_db`, seeded development identities.
The Compose labels/port/image, seed checks, owner/ACL snapshots, backup catalogs,
and dump checksums are retained under ignored `.local/backups/`. No remote or
production PostgreSQL endpoint was contacted. No branches were merged and no
databases were dropped.

## Final state / blocker

**Legal remains failed**, both department and registry, with
`last_error_code=tenant_contract_unavailable`, no verified contract, and foundation
revision `tenant_0001`. The document developer has not supplied the required tenant
schema/migrations/ready contract. Foundation readiness alone cannot activate it.
The real router denies Legal even to the seeded global admin while it is failed.

Legal's existing registration was reused (not reseeded):
`2832b26f-2a32-47c9-a1b2-86dd3e220365`, database
`dept_4009f9f9f1bf448c895071bcbeb62d20`. Generated role names and credential reference
are immutable. The old document model directory and `DepartmentBase` are unchanged.

## Database-level changes

| Change | Scope / final effect |
|---|---|
| Central Alembic `p001`, `p002` | Add attempt/claim/retry/error/revision/contract columns and nonnegative-attempt constraint to registry; no document schema changes |
| `day1_registry` | Non-superuser, no CREATEDB/CREATEROLE; central registry/departments SELECT/UPDATE and schema usage |
| `day1_api` | Non-superuser, no CREATEDB/CREATEROLE; central application DML, registry UPDATE withheld |
| `day1_provisioner` | Non-superuser, CREATEDB/CREATEROLE; separate worker identity, not supplied to API |
| Legal owner | NOLOGIN, non-superuser, no CREATEDB/CREATEROLE, owns Legal DB and foundation objects |
| Legal app login | Non-superuser/no CREATEDB/CREATEROLE/bypass-RLS/role memberships; own DB CONNECT; schema usage; metadata SELECT; no DDL or TEMP |
| Legal tenant foundation | `_provisioning_identity` binds department; tenant `alembic_version`; no invented document tables |
| Database CONNECT ACLs | PUBLIC CONNECT revoked on central, postgres, HR, Finance and template1; existing hr_app/finance_app CONNECT explicitly preserved; Legal target denies PUBLIC CONNECT/TEMP by worker |
| HR / Finance | Department and registry remain active; names/references unchanged; owners unchanged; no schema/data/password replacement or forced connection termination |

The initial development test tightened CONNECT ACLs before a full owner/ACL
snapshot/backup was taken. That gap is not retroactively claimed as covered.
After the user's additional instruction, owner/ACL capture and recoverable
HR/Finance dumps were completed before further privilege changes. The final helper
captures **all databases including templates** before running setup, and validates
target identity and backup catalogs first. The recovery plan explicitly distinguishes
captured later ACL state from the unavailable original pre-change ACL state.

HR/Finance schema/row fingerprints and registry values match before/after checks.
Both currently have no public application tables; preservation checks therefore do
not establish correctness on a populated production dataset. Their custom-format
backups were copied out and `pg_restore --list` succeeded; a destructive restore
over them was not attempted. Use a separate recovery DB for an actual restore drill.

## Actual tests

| Check | Result |
|---|---|
| New isolated pytest unit suite | 24 passed; live integration deselected |
| Explicit live development integration | 1 passed; unit cases deselected |
| Existing `test_authorization.py` | 6/6 checks passed |
| Existing unauthorized provisioning script | Employee denied; passed |
| Standalone worker CLI with unavailable contract | Deferred / exit2, Legal remains failed |
| Compose overlay validation | `config --no-interpolate --quiet` passed |
| Runtime admin credential guard | Superuser URL rejected on connection |
| Real role/database DDL denial | Legal runtime denied CREATE ROLE and CREATE DATABASE |
| Real schema/table/TEMP/identity mutation denial | Denied by worker's rolled-back negative checks |
| Cross-database login | Legal login denied HR, Finance, central, postgres and template1 |
| Advisory lock contention | Second attempt returns busy |
| Idempotent resources | Database OID and persisted credential unchanged on replay |
| Required schema / changed contract | Never activate; missing/changed contracts covered in unit and live unavailable-contract checks |
| HR / Finance preservation | Fingerprints and registry entries unchanged; role passwords not altered |

Initial infrastructure verification exercised real role/database creation,
foundation migration failure/retry, and activation at foundation scope. Following
the explicit schema-readiness requirement, the implementation was strengthened:
Legal was moved to failed and subsequent integration verifies fail-closed routing.
That earlier foundation activation is not the final readiness result.

One initial assertion expected the wrong psycopg exception for denied CONNECT;
the denial itself was correct. Pytest's resulting driver traceback exposed a
development Legal credential. It was immediately invalidated by password rotation
with the same credential reference. Driver connection errors now cross a sanitized
boundary, credential representations hide passwords, error codes are allowlisted,
and a regression test checks the formatted traceback. Subsequent live tests passed
using the replacement credential. No actual credentials were committed.

## Not run / still blocked

- Real activation with document migrations cannot be tested until the document
  developer supplies the pinned revision and required-table contract. This is the
  release blocker, not a test claimed as passed by mocking missing document tables.
- The old `test_department_provisioning.py` requests a *new* Legal and is not safe
  to rerun as written against the already registered department. The new integration
  reuses it and verifies the expected failed readiness state instead.
- The old router script's allowed HR/Finance login paths were not executed: their
  original runtime passwords were not available in this checkout. They were not
  reset to make a test pass. Registry preservation and the existing central RBAC
  checks passed; this is not a claim to have authenticated those two logins.
- No production deployment, SSO, document processing or production load test.

## Remaining security / operational risks

- The provisioner has cluster-level CREATEDB/CREATEROLE and must remain isolated;
  do not mount its URL, bootstrap URL or state root into the API. Migrations are
  trusted administrative code, not user-submitted scripts.
- Local credential JSON is a development adapter, not a managed encrypted secret
  store. Preserve the secret state, restrict host ACLs, use a production secret
  manager and reviewed rotation/pool-drain procedure before deployment.
- New database creation must also restrict PUBLIC CONNECT; otherwise it can reopen
  cross-database connectivity. Runtime role guards do not replace database ACLs.
- Version/table checks do not validate every document column, view or immutable
  audit policy. The document developer's contract/permissions must define these.
- PostgreSQL cannot atomically transact CREATE DATABASE with central registry state.
  Owned partial resources are retained for safe retry; unmarked resources cause
  failure. Cleanup is a manual reviewed operation, never automatic DB deletion.
- User IDs still require a trusted authentication context; Day1 does not implement
  authentication. Current health routes do not provide an authentication system.

See [setup and recovery instructions](provisioning.md) and
[audit](provisioning-audit.md). All commits are on `feature/core-backend`.
