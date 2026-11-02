# Subtask 2.1: development authentication

This is an explicitly enabled **non-production** password identity provider. It
uses existing central users and RBAC, with no built-in admin, registration endpoint,
header identity override, or universal test password. Microsoft Entra is pending.
No document/RAG models, tenant migrations, or department provisioning behavior are
changed. No Subtask 2.2 business routes are introduced.

See [executed tests and database-change report](authentication-verification.md).
Password hashing uses the high-level
[argon2-cffi API](https://argon2-cffi.readthedocs.io/en/stable/api.html).

## Design and trust boundaries

- `app/auth/config.py`: disabled by default. Enabling `development_password` requires
  explicit `APP_ENV=development` or `test`; other modes and production use fail
  configuration validation. The root/health endpoints remain public.
- `app/auth/enroll.py`: offline enrollment/rotation for an already-existing, unique,
  active user. Passwords are prompted with `getpass`, 12–128 characters, then hashed
  with Argon2id (64 MiB, three iterations, random salt). The API cannot enroll users
  or change these hashes. No password arrives through command-line arguments.
- `POST /auth/dev/login`: JSON `email` and `password` only. Login resolves email
  case-insensitively; ambiguous case variants, unknown/inactive users and invalid
  passwords receive the same 401 response. Unknown users perform a dummy hash
  verification. A DB-backed fixed-window limiter counts all parsed attempts by
  account, peer IP and globally, across processes. It returns 429/Retry-After.
  Account defaults: 5/minute; IP: 20/minute; global: 100/minute. These limits also
  bound valid login attempts. Forwarded headers are not read by this module.
- Successful login returns a fresh 256-bit random opaque bearer token, `expires_in`
  (default 900 seconds, allowed 60–3600), `authentication_provider`, and
  `production_ready: false`. Only its SHA-256 digest is stored in PostgreSQL. No
  signing key is needed for server-side sessions; no self-asserted JWT is accepted.
- `GET /auth/me`: validated current user ID/name/email/status and current effective
  role scopes. No hashes, session IDs, tenant credentials or database references.
  Send `Authorization: Bearer <returned token>`; cookies, query parameters,
  `X-User-ID`, and body claims cannot establish identity.
- Every request checks expiry, revocation, current user status, and current password
  credential version. Password rotation invalidates older sessions, including one
  issued concurrently against the old credential. Roles and memberships are loaded
  from current central records rather than embedded in tokens.
- `POST /auth/logout`: revoke the authenticated session, return 204. Replaying it
  receives 401; other sessions remain valid. Rotation revokes all of a user's
  sessions. Expiry is absolute, without sliding refresh or refresh tokens.
- Responses under `/auth/` use `Cache-Control: no-store`; invalid request responses
  omit submitted inputs, and database failures return a generic 503.

The reusable `get_current_user` dependency lives in `app/auth/dependencies.py`.
Future routers must additionally use `require_department_permission(code)` or
`require_global_permission(code)`. The department dependency uses the path parameter
`department_id` and the existing authorization service. Department grants require
active membership and active department; global grants retain their existing
cross-department semantics. Unknown permissions and absent assignments deny access.
Authentication alone does not authorize a business operation. Calls to the existing
database router must receive `user.id` from this dependency, never a caller ID.

## Local setup (operator action, not performed on the Day 1 cluster)

1. Install `requirements-test.txt` in a private Python environment or build the
   backend Docker image. Copy `.env.example` to an ignored `Backend/.env`. Do not
   put secrets in the example, shell history, screenshots, logs or Git.
2. Before modifying an existing cluster: verify it is development-only, inventory
   current owners/ACLs and legitimate connection identities, capture recoverable
   database/global backups, and retain a rollback plan. The isolated test tool below
   does this on a new test database and never targets Day 1's HR/Finance.
3. Using the **separate central migration identity**, apply central Alembic revision
   `auth001`, which adds three tables and indexes. This is not a tenant migration:

   ```sh
   # Supply CENTRAL_MIGRATION_DATABASE_URL_FILE in this offline shell only.
   python -m alembic upgrade auth001
   ```

   Alembic reads the process environment directly; source its secret path explicitly.
   Do not mount the migration or provisioning DSN into the API container.
4. Configure `DATABASE_URL_FILE` with an existing least-privilege central application
   identity. A private JSON file has one `url` key containing its PostgreSQL DSN;
   apply restrictive OS file permissions. Keep the API free of CREATEDB,
   CREATEROLE, SUPERUSER, replication/bypass-RLS privileges, and privileged role
   memberships. Day 1's runtime guard remains in force on application connections.
   The following are **additional auth grants**, for an operator-reviewed API role,
   after the backup/ACL preflight. They do not change legitimate HR/Finance grants:

   ```sql
   -- psql variable api_role must name the existing restricted central API role.
   GRANT SELECT ON auth_development_credentials TO :"api_role";
   GRANT SELECT, INSERT, UPDATE ON auth_development_sessions, auth_login_windows TO :"api_role";
   ```

   Retain existing SELECT access to users, roles, permissions, role assignments,
   role permissions, departments and department memberships. Do not grant the API
   ownership, DDL, password-credential writes, or auth-table DELETE privileges.
   Review existing grants/default grants too; adding these statements does not
   revoke pre-existing excessive access.
5. Use a **separate offline enrollment identity** with SELECT on `users` and
   `auth_development_credentials`, INSERT/UPDATE on `auth_development_credentials`,
   and SELECT/UPDATE on `auth_development_sessions`, plus central CONNECT/schema
   USAGE. It needs no PostgreSQL administrative role attributes. Supply its DSN as
   `AUTH_BOOTSTRAP_DATABASE_URL_FILE` in the offline shell only. Never mount it into
   FastAPI. Enable dev auth explicitly, then enroll an existing test user:

   ```sh
   # APP_ENV=development and AUTH_MODE=development_password in this offline shell.
   python -m app.auth.enroll --email <existing-test-user-email>
   ```

6. For FastAPI set `APP_ENV=development`, `AUTH_MODE=development_password` and the
   **API-only** DSN file. Start on loopback without trusting forwarded headers:

   ```sh
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
   ```

   The optional Compose backend profile defaults auth to disabled, publishes only
   `127.0.0.1:8000`, and mounts only the API DSN and tenant credential subpath.
   Set `AUTH_MODE=development_password` in its private operator environment to opt
   in after migrations/grants/enrollment. Rebuild the backend image before use.
   This change does not start services automatically.

Keep returned bearer tokens in client memory; do not persist them in browser local
storage or log Authorization headers/request bodies. Local HTTP is for loopback
development only. For other environments use HTTPS and the approved identity
provider; this implementation does not offer MFA, identity recovery, or production
SSO. A bearer token can be replayed until expiry/revocation if stolen.

## Automated verification

From `Backend/`:

```sh
python -m pytest -q -m "not integration" --tb=short
```

From the repository root, with Docker and PowerShell 7.2+:

```powershell
docker build -t ai-platform-day2:dev Backend
./Backend/tools/test-auth-postgres.ps1
```

Supply `-Docker <absolute-docker.exe-path>` when Docker is not on PATH. The script
creates a uniquely named PostgreSQL 17 container on an internal network, publishes
no ports, saves backups plus owners/ACLs to ignored `.local/auth-tests/<run>/`,
verifies the backup catalog, and runs the opt-in PostgreSQL tests. It stops and
preserves the container, network, and recovery files for inspection; it does not
delete databases, existing containers, or the HR/Finance volume. Synthetic passwords
are generated per test. Restrict the private test-state directory to the operator.

Tests exercise valid and rejected authentication, spoofed identities and safe
responses. Additional checks cover credential rotation, current RBAC assignments,
limiter persistence and concurrency, migrations, and denial of privileged database
operations by a restricted API role. SQLite is used only through explicit
test-engine injection; runtime remains PostgreSQL.

## Maintenance, rollback and Entra handoff

An offline maintenance identity can periodically delete expired `auth_login_windows`
and expired/revoked `auth_development_sessions` according to the agreed audit
retention policy. FastAPI has no DELETE permission. Without maintenance these tables
grow; no automatic retention job or login audit-event store is included in 2.1.
Rate windows can permit bursts at a boundary; account limits can temporarily lock
out a known account. Edge/network protections and production capacity limits remain
deployment work. Parsing-size controls belong at the deployment boundary as well.

Rollback on an existing cluster starts by disabling auth and stopping API traffic;
retain snapshots/backups. Review and restore only the new auth grants. Central
`alembic downgrade p002` removes **all auth credentials/sessions/counters** and is
destructive to that new state; do not run it blindly. It does not drop a database or
alter tenant data. Restore into a separate recovery database and validate before any
manual cutover. Day 1 provisioning tables/revisions are preserved.

Future Entra support must use a separate validator with verified signature, issuer,
audience, expiry and tenant policy, then map immutable issuer/subject to central
users and explicit internal roles. It must never fall back to development passwords
or map trusted identity from a request header/email claim alone. Re-run RBAC and
isolation tests after integration. Legal stays failed until the document developer
supplies the required migration contract and Day 1 checks pass.
