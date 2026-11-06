# Subtask 3.4 verification — 2026-11-05

Core baseline: 3d7db07, branch feature/core-backend. This work changes only container
configuration, test-image selection, environment templates, security-boundary
tests and documentation. No document/RAG implementation or business migrations
are modified. No push, merge or deployment was performed.

## Executed checks

- `docker compose --env-file .env.example -f docker-compose.yml -f compose.provisioning.yml --profile backend --profile provisioning config --quiet`
  passed with a process-local, non-secret POSTGRES_PASSWORD placeholder. No expanded
  configuration or private environment content was printed, and no service started.
- Unified Core suite: private Python environment running
  `python Backend/tools/run-core-tests.py --suite all --backend native`.
- Gitleaks outgoing-history scan and Git whitespace/scope checks.

| Group | Passed | Failed/errors/skipped |
| --- | ---: | --- |
| Non-integration, including two container-boundary checks | 235 | 0 |
| Authentication PostgreSQL | 2 | 0 |
| Departments PostgreSQL | 7 | 0 |
| Users PostgreSQL | 5 | 0 |
| Role policies PostgreSQL | 5 | 0 |
| Usage PostgreSQL | 9 | 0 |
| Core access PostgreSQL | 6 | 0 |
| Core lifecycle PostgreSQL | 3 | 0 |
| Physical isolation PostgreSQL | 19 | 0 |
| Tenant contract PostgreSQL | 7 | 0 |
| **Total** | **298** | **0** |

Redacted logs, JUnit results and summary are retained privately under
`.local/core-tests/67a846d43ded40b2939d8d5b5c889f53/`. Each PostgreSQL group uses
fresh infrastructure with baseline recovery artifacts. These tests never target
the existing HR, Finance or Legal databases. Real Legal was not freshly queried
or modified; its last documented status remains failed. The contract example
remains ready:false. No actual .env files or secrets are included in the commits.

## Blocked verification and remaining risks

Docker CLI reports 29.6.2, but the dockerDesktopLinuxEngine named pipe is absent.
No Linux daemon is available. Runtime/test image builds, image inspection, installed
dependency checks, read-only filesystem smoke tests, secret/subpath UID access and
Docker PostgreSQL integration execution were therefore **not performed**.
Compose parsing and native tests do not establish that these container checks pass.
When the engine is available, build both targets and run the Docker isolated runner;
use only new test infrastructure for runtime smoke checks, never the existing stack.

Current images use version tags rather than immutable image digests, and Alembic
and test dependencies retain existing version ranges. No vulnerability scan or
production supply-chain certification is claimed. Host secret files and external
credential volumes need operator-controlled ACLs/ownership; Compose secrets are
file mounts rather than an encrypted secret store. /health is liveness only.

The two documents-ai blockers and joint real tenant contract acceptance remain
with the document developer/Core review process. Entra, TLS/remote access and
production deployment remain outside this subtask. See core-containers.md for
the setup sequence, identity boundaries and explicit offline operations.
