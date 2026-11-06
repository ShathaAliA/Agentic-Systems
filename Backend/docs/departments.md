# Subtask 2.2: department creation grants and archival

See [actual verification results and remaining limits](departments-verification.md).

Works with Subtask 2.1's validated current-user dependency and the existing central
RBAC. No tenant/document/RAG schema is supplied or changed. The API registers work;
only the separate Day 1 provisioning worker creates PostgreSQL databases and roles.

## Ownership and lifecycle

The global `departments:manage` permission authorizes Admin operations. A user with
a department-scoped role, including a department manager, cannot administer grants,
create arbitrary departments or archive any department.

An Admin issues a creation grant bound to an existing active user and a department
name. This creates **no Department row**. The user consumes their own grant with an
empty JSON object; the server supplies the name and owner. Registration, registry
entry, membership, department-scoped `Department Manager` role, grant consumption
and audit event commit in one central transaction. No global role is assigned.
The existing scoped manager role must already be configured; otherwise registration
returns 503 and leaves the grant pending. Role definitions/permissions are not
invented by the HTTP request.

The grant states are `pending`, `consumed`, `revoked`. A consumed grant is the
permanent ownership/name reservation ledger, including direct Admin creations.
An Admin may create any number of unowned departments or specify a head. Assigning
a head always observes the same one-department limit. Consumed grants cannot be
revoked or deleted through the API. Failed provisioning and archival do not restore
the right to create a second department. Pending grants can be revoked; a new grant
can then be issued. Admins resolve a conflicting pending reservation by explicitly
revoking it first; creation does not silently override another user's grant.

Names use Unicode NFKC, collapsed whitespace and casefolded reservation keys. Names
are 1–150 characters after normalization; control/format characters are rejected.
Partial unique indexes reserve a normalized name and head for pending/consumed
rows. PostgreSQL advisory transaction locks serialize operations for each head;
row locks and unique constraints handle consumption/revocation and name races.

Archival is an Admin operation, not physical deletion. It records actor/time,
invalidates a running worker claim and removes verified routing readiness. Original
department/registry provisioning status, credentials, databases, membership history
and audit events remain. Responses show effective status `archived` separately
from `provisioning_status`. RBAC, routing and `/auth/me` exclude archived department
access. A head can still see their own department's status metadata with current
scoped role and active membership, including failed or archived status; this never
opens a tenant connection.

Worker acquisition and completion both check archival under row locks. An in-flight
attempt cannot mark an archived department active or failed. Its PostgreSQL work
may already have created resources before archival; those resources are retained,
not deleted. Archival does not terminate existing database connections or roll back
an already-running user request. Subsequent authorization/routing checks deny access.
A single-department worker run reports `archived` and exits 2; queue polling skips
archived departments. No HTTP route retries provisioning or forces activation.

## HTTP contract

All routes require authentication. Admin means an active validated user with the
global permission above; a caller-supplied actor ID/header is never accepted.

| Method/path | Permission and body |
| --- | --- |
| `POST /admin/department-creation-grants` | Admin; `{head_user_id, name}`; 201 |
| `GET /admin/department-creation-grants` | Admin; optional state, offset, limit |
| `POST /admin/department-creation-grants/{id}/revoke` | Admin; pending grant; repeat revocation is idempotent |
| `GET /me/department-creation-grant` | Own latest grant only |
| `POST /departments/from-grant` | Own pending grant; body `{}`; 202 |
| `POST /admin/departments` | Admin; `{name, head_user_id?}`; 202 |
| `GET /admin/departments` | Admin; optional state, offset, limit |
| `GET /admin/departments/{id}` | Admin; safe status/details |
| `POST /admin/departments/{id}/archive` | Admin; repeat archival is idempotent |
| `POST /admin/departments/{id}/head` | Admin; `{head_user_id}`; first ownership binding/reconciliation only |
| `GET /me/department` | Own scoped manager role and active membership; safe metadata |

Lists default to 50, maximum 100 entries, with stable ordering. State filters are
validated. Request models reject extra fields. Errors: 401 invalid credentials;
403 forbidden; 404 absent visible resource; 409 conflicting reservation/ownership;
422 invalid body/name; 503 unavailable database or required role configuration.
Responses omit database names, credential references, worker claims, DSNs and raw
driver errors. Failure codes are allowlisted. Protected metadata responses use
`Cache-Control: no-store`. These routes do not expose audit modification or deletion.

## Migration and deployment sequence

Do not apply this automatically to the existing development/company cluster.
Before changing database privileges, verify the target is development-only, capture
owners/ACLs and legitimate connection identities, and make recoverable database and
global backups. Keep migration/enrollment/provisioning DSNs outside the API process.

1. Stop old API/worker versions before rollout: an old worker does not implement
   the archive fence. Apply central revision `dept001` using the separate central
   migration identity (`CENTRAL_MIGRATION_DATABASE_URL_FILE` in the offline shell):

   ```sh
   python -m alembic upgrade dept001
   ```

   This online migration adds central tables/indexes and archival columns. It
   reserves existing department names without changing their names/status or
   guessing head identities. Ambiguous/invalid names abort before changes and
   require explicit operator reconciliation. No departments/users/roles are seeded.
2. Review existing and default grants; existing table-level UPDATE/DELETE grants
   override column-level restrictions. For a restricted API role, retain central
   RBAC SELECT grants and the Subtask 2.1 auth grants, then add only:

   ```sql
   -- psql variable api_role identifies the operator-reviewed restricted API role.
   GRANT SELECT ON department_databases TO :"api_role";
   GRANT INSERT ON departments, department_databases, department_memberships, role_assignments TO :"api_role";
   GRANT UPDATE(archived_at, archived_by) ON departments TO :"api_role";
   GRANT UPDATE(status) ON department_memberships TO :"api_role";
   GRANT UPDATE(claim_token, next_retry_at, verified_contract) ON department_databases TO :"api_role";
   GRANT SELECT, INSERT ON department_creation_grants TO :"api_role";
   GRANT UPDATE(head_user_id, state, department_id, consumed_at, revoked_at) ON department_creation_grants TO :"api_role";
   GRANT SELECT, INSERT ON department_audit_events TO :"api_role";
   ```

   Do not blindly revoke legitimate existing permissions. Plan any excessive-grant
   cleanup from the captured ACL snapshot. The API needs no registry-status UPDATE,
   audit UPDATE/DELETE, department DELETE, DDL or elevated PostgreSQL attributes.
   The worker retains its separate existing identity; it does not need auth secrets.
3. Prepare the initial Admin account/global permission and scoped manager role via
   the trusted identity/RBAC bootstrap process, independently of department fixtures.
   Do **not** use the demo seed in production. A fresh migrated database has zero
   departments. Enable development authentication only under Subtask 2.1's rules.
4. For existing departments, use the authenticated Admin head-binding route to
   reconcile an unowned ledger entry. It validates that existing manager assignments
   do not conflict. Until reconciled, existing head assignments block new creation
   grants. Binding cannot replace an existing owner or silently repair revoked roles.
5. Rebuild/restart the new API and worker versions together, verify permissions and
   routing, and keep Legal failed until the actual document migration contract and
   all Day 1 verification checks are available. No activation is added by this task.

Development fixtures are optional and explicit, from `Backend/`:

```sh
# Only in an offline development shell with the reviewed fixture-writing identity.
# Requires APP_ENV=development. Never part of image startup or production setup.
python seed.py --development-fixtures
```

This retains the existing HR/Finance fixture content; no password is predefined.
The fixture entrypoint rejects production and missing opt-in. Do not rerun it on
the current development cluster as part of this subtask. Fixture cleanup is a later,
separately reviewed archival/retention task, not database deletion here.

## Tests and recovery

```sh
python -m pytest -q -m "not integration" --tb=short
```

From repository root with PowerShell 7.2+ and the development test image:

```powershell
./Backend/tools/test-auth-postgres.ps1
./Backend/tools/test-auth-postgres.ps1 -TestFile tests/test_departments_postgres.py
```

Both create independent backed-up PostgreSQL test containers on internal networks,
publish no ports, and preserve stopped containers/private recovery files. Supply
`-Docker <docker.exe-path>` when needed. Day 1 live provisioning tests remain
opt-in because they can change development cluster ACLs.

For rollback, stop API and worker traffic first and preserve the owner/ACL snapshots,
database dumps and secret references. Restore into a separately named recovery
database and validate before cutover; never drop or overwrite HR/Finance as a
shortcut. Downgrading `dept001` destroys the new ownership/audit/archive metadata
and removes the access fence, so it must not be automatic. Do not run an old worker
against archived departments. The original department databases are never dropped.

Not included: changing department names/owners after binding, restore/unarchive,
physical deletion, full user/role administration, Entra, document/RAG work, audit
export UI, or automatic activation of the normal development stack. Database
superusers remain a trusted administrative boundary; API permission checks do not
defend against a compromised PostgreSQL administrator.
