# Core containers and environment — local development only

This is not a production deployment. Development password authentication is
forbidden outside development/test; Microsoft Entra, company data approval and
document integration remain separate release gates. No command below resets data.
Do not run the legacy verify-development tool as a container smoke test: it targets
existing infrastructure and requires its own backup/approval workflow.

## Images

Build from the repository root:

```powershell
docker build --target runtime -t ai-platform-core:dev Backend
docker build --target test -t ai-platform-core:test Backend
```

The default Dockerfile target is runtime. It installs requirements.txt only and
copies explicit application/migration directories, not tests or bootstrap scripts.
The test target adds requirements-test.txt and test sources; its default command
reports the pytest version. Run integration tests using the isolated runner, which
mounts the source and pytest configuration into /repo explicitly:

```powershell
python Backend/tools/run-core-tests.py --suite all --backend docker --image ai-platform-core:test
```

The unit portion of that command runs in the host's private Python environment;
PostgreSQL integration groups run in containers. Native alternative:

```powershell
python Backend/tools/run-core-tests.py --suite all --backend native
```

Both images use UID 10001. Runtime API/worker containers have a read-only root
filesystem, all Linux capabilities dropped, no-new-privileges and a bounded /tmp
tmpfs. Python bytecode writes and automatic dotenv loading are disabled in images.
Source files are not bind-mounted into the regular runtime container.
Test images deliberately contain operator/test code; never use them for API service.
Legacy manual Day 1 verification retains its old image tag but builds the test
target explicitly; it is not part of the automatic Core test runner.

## Environment and secrets

There are two non-secret templates with different purposes:

| File | Consumer | Purpose |
| --- | --- | --- |
| Root .env.example | Docker Compose interpolation | Host secret-file paths, auth limits and external state volume name |
| Backend/.env.example | Native Python runtime | API URL-file reference, auth limits, department resolver host/port |

Copy privately only if needed; never commit the resulting .env files. Compose sets
its environment explicitly and does not import Backend/.env. Offline migration,
enrollment and provisioning references in the Backend template are commented:
they must not be copied into the API environment. JSON URL files have the shape
{"url":"..."}; obtain their actual values from a private operator source, not
examples or command-line arguments. Restrict host ACLs to the operator and required
service identities. Compose file secrets are local file mounts, not an encrypted
secret manager. Native environment variables remain visible to same-user processes.

The base docker-compose.yml still requires POSTGRES_PASSWORD from a private source
for its existing development PostgreSQL service. Do not print expanded Compose
configuration, run shell tracing or expose environment dumps. Do not change this
password to try to rotate an existing PostgreSQL volume: image initialization
variables only initialize an empty volume. Credential rotation requires a separate
reviewed database procedure preserving legitimate connections.

API receives only api_url and read-only tenant runtime credentials. The worker
receives registry_url and provisioner_url, plus writable tenant credential storage.
API must never receive the provisioning, enrollment or migration identity, and its
PostgreSQL role must lack CREATEDB/CREATEROLE/SUPERUSER and inherited equivalents.
The runtime guard remains enabled. The provisioning identity is separate and only
used by the offline worker. Central migrations and enrollment remain explicit
offline operations described in provisioning.md and authentication.md; startup does
not migrate, seed, create an Admin or grant database privileges automatically.

## Preflight before starting services

1. Verify Docker Desktop's Linux engine is available with docker version. A client
   version alone does not establish availability. Build both image targets.
2. Inventory the intended Compose project, existing container/volume identities
   and port use. The base file uses a fixed PostgreSQL container name and an existing
   data volume; a different project name alone is not isolation. Do not start this
   stack against current HR/Finance as a smoke test. Use the unique-cluster test
   runner for verification.
3. Verify that private URL files identify development-only databases and the
   correct limited roles. For any existing-database changes, first capture owners,
   ACLs, legitimate identities and recoverable backups plus a rollback plan.
4. The external provisioning_state volume must already exist, with day1/tenants
   created and owned by UID 10001, directory mode 0700 and credential files 0600.
   The Compose subpath option requires a supporting Compose/Engine version.
   Inspect ownership before any correction; never recursively chown an existing
   state/data volume as a workaround. No volume creation or ownership changes are
   performed by API startup.
5. With private environment configured, validate without printing secrets:

```powershell
docker compose -f docker-compose.yml -f compose.provisioning.yml --profile backend --profile provisioning config --quiet
```

This checks configuration, not secret readability, external volume subpaths, image
build success or database readiness. After the operator has completed preflight,
the backend profile selects only the API and its database dependency. Provisioning
is opt-in and not automatically started with the backend:

```powershell
docker compose -f docker-compose.yml -f compose.provisioning.yml --profile backend up -d backend
```

The API binds only 127.0.0.1:8000. /health is public process liveness, not proof of
database/migration readiness. /health/db requires an authenticated Admin. A healthy
process can still return 503 for a database failure. Keep proxy headers disabled;
remote access, TLS and trusted proxy configuration are not provided by this setup.

Worker execution requires explicit operator selection of a department after real
migrations/policy review; see provisioning.md. Do not run it for Legal now. The
example tenant contract remains ready:false and Legal must remain failed.
Fresh normal initialization contains no departments; HR/Finance are explicit test
fixtures, not production defaults. Never run down -v, volume prune or database
deletion as part of these instructions. Stop only the specific test/service owned
by the operation; retain backup and recovery artifacts.

## Remaining integration gates

documents-ai must restore the privileged-runtime rejection and central migration
history, then provide tenant migrations and agree the V1 contract. No document or
RAG module is changed here. This image is Core-only; its build does not prove the
other branch's OCR/provider dependencies. Dependency vulnerability scanning, pinned
image digests, secret-manager integration, TLS, production identity and operational
backup/restore validation remain production preparation work, not claims made here.
