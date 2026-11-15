# Phase 2A: isolated Core foundation

Scope: steps 1 and 2 only. This environment creates synthetic identities and
two new tenants. It does not use Phase 1 documents, databases, volumes or users.
Document endpoints return 503; no LLM or embedding requests are enabled.

## Requirements and startup

Use the repository's Core Python environment and a working local Linux Docker
engine. Run commands from the repository root. Never combine this Compose file
with another Compose project or supply existing database connection strings.

```powershell
.local/rbac-venv/Scripts/python.exe poc/phase2/control.py init
# Substitute the generated run identifier below.
.local/rbac-venv/Scripts/python.exe poc/phase2/control.py up --run RUN_ID
.local/rbac-venv/Scripts/python.exe poc/phase2/smoke.py --run RUN_ID
.local/rbac-venv/Scripts/python.exe poc/phase2/control.py stop --run RUN_ID
```

Each initialization creates a private `.local/poc-phase2/RUN_ID` directory.
Its passwords, database URLs, backups and reports must stay outside Git.
Do not print `secrets.json` or paste its contents into a chat. The bootstrap
creates employee, head, deputy, other-department, denied-role and separate
administrator accounts with independently generated passwords.

The Phase 2A Dockerfile selects Core dependencies from the backend pins plus
pgvector and the multipart parser required when FastAPI registers its routes.
It excludes OCR, document extraction, model and provider packages.

The database and setup tools use an internal network with no published DB port.
Only the API also joins a separate bridge network to publish its generated
loopback port; that bridge permits outbound connectivity. This is not an
egress-filtering sandbox. No provider credentials are forwarded, provider
packages are excluded, and document/model routes remain blocked.
The Core API binds to a generated
loopback port; `/poc/status` identifies the run and reports disabled document
and model services. The API receives only runtime credentials. Provisioning
and migration credentials are confined to the one-shot tools container.

The controller refuses resource collisions and arbitrary run identifiers.
Before role/ACL changes it captures a database dump, global role definitions
without password hashes, and owners/ACL metadata. Recovery artifacts are
private. Stop preserves the new volume; it never runs `down -v`, deletes a
database, resets Docker or changes another project's resources.

The scoped tenant manifest is generated privately. A tenant becomes active only
after the required migrations, connectivity, grants and isolation checks pass.
The repository's tenant manifest and existing Legal status are unchanged.

`up` runs the SQL/Core checks and actual loopback HTTP smoke checks before
reporting success. Smoke checks revoke every session they create. A failed run
must not be reused with `up`; stop that run and initialize a fresh one. Preserve
its private state for diagnosis. A separate smoke invocation can check a running
successful environment again without redoing migrations.
Startup failures stop only verified-owned resources and retain volumes/backups.
If ownership cannot be verified, inspect the private run's cleanup review flag;
the controller will not stop an unverified project. Optimized Python (-O/-OO) is
deliberately unsupported because acceptance checks must remain enabled.

## Diagnostic fallback

```powershell
.local/rbac-venv/Scripts/python.exe poc/phase2/control.py init
.local/rbac-venv/Scripts/python.exe poc/phase2/control.py native-test --run RUN_ID --identity-only
```

This creates and stops a separate local PostgreSQL cluster. It verifies Core
authentication and SQL permission boundaries without claiming pgvector or
complete tenant readiness. Tenants remain provisioning; positive department
permissions are intentionally denied. `verification.json` explicitly reports
`full_environment_ready: false`. A stopped diagnostic run is retained for
recovery and must not be reused as a fully provisioned Docker run.

## Acceptance gates

- Six real, expiring Core logins; logout revokes the session.
- Missing/forged identity and unauthorized global administration are rejected.
- Employee/head/deputy permissions are limited to their assigned tenant;
  other-department and denied-role accounts cannot cross that boundary.
- Central and tenant runtime identities have no SUPERUSER, CREATEDB,
  CREATEROLE, replication or BYPASSRLS capabilities, no cross-database CONNECT,
  and no schema/role creation rights.
- Actual tenant migrations and pgvector checks pass before readiness is claimed.
- No documents processed, provider SDKs loaded or model requests made.

Humain is a future provider candidate only. Its API compatibility and models
have not been evaluated, and no API key is needed for this phase.
