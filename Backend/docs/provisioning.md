# Department provisioning (Day 1)

## Identities and process boundary

The API registers a request using `request_department_creation` and central RBAC.
It does not import or run the provisioning worker. Caller identity must come from
trusted authentication, not a request-body admin ID. Authentication is still a
separate pending feature.

| Identity | Allowed scope |
|---|---|
| PostgreSQL bootstrap/migration administrator | Central migrations and deliberate operator setup only; never mount into API |
| Registry worker login | CONNECT central; USAGE public; SELECT/UPDATE departments and department_databases |
| Provisioner login | CREATEDB/CREATEROLE, no superuser; owns/administers only managed roles and can SET tenant owners |
| API central login | Central application DML; no DDL, CREATEDB, CREATEROLE, superuser or bypass-RLS; registry UPDATE withheld |
| Tenant owner | NOLOGIN; owns its one database and migrated objects |
| Tenant runtime | CONNECT own database, schema USAGE, business-table DML and sequence use; metadata SELECT only; no ownership, membership, TEMP or DDL |

Runtime connection pools reject administrative credentials. Roles use immutable
`dept_<32 hex>_owner` and `_app` identities derived from the registry, not mutable
department names. New worker provisioning never operates on legacy `hr_db` or
`finance_db`. Existing active departments are not recreated or converted.

PostgreSQL grants CONNECT to PUBLIC by default. The worker **checks** that a new
runtime cannot CONNECT to any other connectable database (including template1) and fails if isolation is
not configured. It never changes another database's ACL to pass this check.
An operator must inventory legitimate connections, explicitly grant their access,
back up owner/ACL state and data, then restrict PUBLIC CONNECT as appropriate for
the development cluster. Adding databases later can reopen PUBLIC connectivity;
apply the same policy to every new database. Existing connections are not forcibly
terminated. Templates are included when they allow connections; template0 normally
has connections disabled by PostgreSQL itself.

## Tenant schema contract: pending document developer

`app/department_models/*` is unchanged. There are no document models in this branch
to migrate. `tenant_0001` creates only `_provisioning_identity` (department binding),
not a replacement document schema. Central Alembic is separate from tenant Alembic.

The document developer must:

1. Supply reviewed migrations under `tenant_alembic/versions/`, descending from
   `tenant_0001`, importing/using their own document schema without `create_all()`.
2. Agree which tables are mandatory and the explicit immutable target revision.
3. Provide a deployment-local contract JSON with `ready: true`, that revision in
   `required_revision` (not `head`), and the mandatory public table names in
   `required_tables`. Include `_provisioning_identity` alongside document tables.
4. Agree table/sequence privileges. Current runtime grants are CRUD on public
   business tables; immutable/audit tables may need narrower policies in their
   migrations before adding them. Views, other schemas, required columns and
   extensions such as pgvector require an explicit contract extension/review.

`tenant-contract.example.json` is intentionally unavailable. The worker sets
`failed / tenant_contract_unavailable` without provisioning when it is used.
No foundation-only bypass is exposed by the CLI. A previously active managed
department is reprocessed if its contract hash changes, preventing a foundation
stamp from satisfying a later document contract. Stop routing/quiesce the tenant
before deploying a changed contract, run the worker, then resume only when active.
Long-running polling workers load the contract at startup; restart them on change.

Migrations run on a supplied connection under SET LOCAL ROLE of the NOLOGIN owner,
in a transaction. The worker verifies target revision, required-table existence,
binding identity, login connectivity, role flags/membership/ownership, DDL/TEMP
denials and cross-database CONNECT denials before activation. No automatic `stamp`,
document-model overwrite, database deletion, or rollback of tenant data occurs.

## Configuration and credentials

URLs may be supplied with `NAME` or `NAME_FILE`; file mode is preferred. URL files
contain a JSON object with key `url`. Keep all contents outside Git and restrict
host ACLs (Linux 0600 files/0700 directory; Windows owner-only ACLs). Never print
them or run `docker inspect` environment dumps in reports.

| Process | Settings |
|---|---|
| API | DATABASE_URL_FILE, DEPARTMENT_CREDENTIALS_DIR, DB_HOST, DB_PORT |
| Central migrations | CENTRAL_MIGRATION_DATABASE_URL_FILE |
| Worker | PROVISIONING_REGISTRY_URL_FILE, PROVISIONING_ADMIN_URL_FILE, DEPARTMENT_CREDENTIALS_DIR, TENANT_CONTRACT_FILE |
| Optional retry settings | PROVISIONING_MAX_ATTEMPTS (default 3) |

The registry stores only `credential_key`, never passwords or DSNs. For development,
the worker writes one atomic no-overwrite credential file per reference. The API
reads that directory only; it receives neither owner nor administrative passwords.
Existing HR/Finance `HR_DB_USER/PASSWORD` and `FINANCE_DB_USER/PASSWORD` environment
references continue working. Production needs a secrets-manager adapter and a
reviewed rotation/revocation procedure; local JSON is not an encrypted vault.

`compose.provisioning.yml` provides optional `backend` and `provisioning` profiles.
The API mounts only the **tenant credential subdirectory** read-only, not the
development state root containing administrative URLs. Requires Docker/Compose
support for volume subpaths. Do not expose the provisioner URL or mounted directory
to API containers. The development bootstrap superuser is never an API URL.

## Development setup and tests

The existing Compose PostgreSQL uses `postgres:17-alpine`, local port5435 and the
existing named data volume. Do not recreate its volume or run `down -v`.

`tools/verify-development.ps1` refuses unexpected image/container/port bindings or
missing seeded users, captures owners/ACLs, creates PostgreSQL custom-format
HR/Finance backups and archive lists, builds the test image, and runs explicit
development integration tests. It reads the existing bootstrap password directly
into an ignored local URL file without printing it. This is an operator-only
development script, not a production worker. Backups and URL files are under
`.local/` (ignored). Protect and retain them; backups may contain sensitive data.

The integration test requires `RUN_DEVELOPMENT_PROVISIONING=1`, `DEV_BACKUP_ACK=1`,
`DEV_ADMIN_URL_FILE`, and `DEV_STATE_DIR`. It uses an existing Legal registration,
does not reseed it, and never drops databases. It snapshots HR/Finance table rows
and column definitions before/after and compares their registry entries. It
captures owner/ACL state before grants. The deliberately unavailable document
contract leaves Legal failed; a separate infrastructure fixture exercises real
role/database setup, foundation migrations, replay, permissions and isolation.
Successful activation with a real document schema remains blocked on that developer.

The dev state volume `ai_platform_day1_dev_state` holds recoverable generated
credentials under `day1/`. Export `api.json`, `registry.json`, and `provisioner.json`
from that directory into `.local/` for Compose secrets; use a short-lived helper
container and `docker cp`, never `cat` into terminal output. Preserve the volume:
recreating a secret store is not a safe way to recover existing database passwords.

Use the original Compose project name (from the preflight) to avoid creating a
second PostgreSQL alongside the existing named container. Supply the existing
POSTGRES_PASSWORD through the operator's private environment when rendering Compose.
Example after a real tenant contract is supplied:

```sh
docker compose -p <existing-project> -f docker-compose.yml -f compose.provisioning.yml --profile provisioning run --rm provisioning-worker --department-id <registered-id>
```

Without Compose, from `Backend/` and a worker-only secret environment:

```sh
python -m app.provisioning.worker --department-id <registered-id>
python -m app.provisioning.worker --department-id <registered-id> --retry-failed
python -m app.provisioning.worker --poll 5
```

Transient infrastructure failures back off exponentially and stop at the configured
attempt limit. Schema/identity/permission conflicts remain failed until explicit
operator retry after repair. `--retry-failed` bypasses delay, not the attempt limit;
increase the configured limit deliberately if additional audited attempts are needed.
Errors are bounded allowlisted codes, never raw driver messages. A
single-department CLI invocation exits2 when busy/deferred,1 on failure, and0 only
when that invocation succeeds. An idle queue pass may exit0 without any activation.
The JSON status must always be checked. A PostgreSQL
advisory lock serializes attempts; claim tokens fence stale status writers. Crashes
can leave owned roles/databases; reruns verify markers and resume rather than delete
or adopt unknown objects. A per-database role owner and marker must match exactly.

## Recovery / rollback plan (no database deletion)

1. Stop the worker and pause routing to affected tenants. Keep Legal failed while
   its document contract is unavailable.
2. Preserve state volume, secret files, owner/ACL snapshots and dump checksums.
   `pg_restore --list` validates the custom archive catalog; this task did not
   overwrite HR/Finance to test a restore.
3. Restore a backup first into a separately named recovery development database,
   using `pg_restore --no-owner --no-privileges` and an operator-selected owner.
   Compare schema/data fingerprints before any manual cutover. Never drop/replace
   the existing HR/Finance databases as a rollback shortcut.
4. Reapply owner/ACL state from the captured snapshot only after reviewing grants
   and legitimate connection identities. Later snapshots do not establish what
   ACLs existed before the first Day1 setup. Broadly granting CONNECT to PUBLIC
   would undo isolation; do not apply it blindly as a rollback.
5. If credentials must rotate, change the database password and secret-store
   value coherently, drain/dispose old pools, and reverify. Never rotate existing
   HR/Finance application passwords as part of new department provisioning.
