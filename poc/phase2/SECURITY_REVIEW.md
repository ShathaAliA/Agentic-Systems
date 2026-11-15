# Security review — Phase 2A steps 1 and 2

Reviewed 2026-11-11 on PoC, against baseline 6b5c2db. Scope is only the new
isolated foundation, synthetic identities, Core wrapper and test tooling.
No production approval, provider integration or document-processing acceptance
is implied. No existing database was queried or changed during this review.

## Findings resolved before local commits

- Optimized Python could remove assertion-based setup/acceptance guards. Phase
  2A now rejects optimized Python at import; empty-cluster, backup and contract
  checks also use explicit exceptions before modifying database privileges.
- A parsed runtime URL could carry psycopg query parameters overriding its
  apparent host/port. Runtime URLs now require the exact driver, host, port,
  central database and application role, with no query overrides.
- Windows junctions and duplicate tenant identifiers could weaken local target
  validation. Junctions are rejected alongside symlinks; tenant database names
  and IDs must be distinct.
- Startup failure could leave the new API/database running. Failure now records
  an unverified failed run and stops only a project with verified database/run
  ownership. If ownership or cleanup cannot be verified it records a manual
  review flag, retains artifacts and does not stop unverified resources.
- Generated Docker token/buildx state must not enter Git. Child configuration
  stays in the private run directory and root .docker is additionally ignored.

## Validation actually executed after hardening

- 11 focused regression cases passed, including optimized-Python rejection,
  connection overrides, junction rejection and owned/unowned cleanup paths.
- Fresh Docker run 309925602b1449428d160f2b5c69f603 passed all 13 Core/SQL/vector
  groups. Real tenant migrations preceded activation of two synthetic tenants.
- Actual loopback HTTP passed six authenticated identities and eight role/
  department cases; all sessions created by that HTTP test were revoked.
- Database runtime identities were checked for absence of elevated capabilities,
  cross-database CONNECT and schema/role creation rights. Both tenant runtime
  identities executed a pgvector distance calculation successfully.
- The test project was stopped and its volume/backups retained. No file ingestion,
  provider SDK or model request was used. No next step was started.

Private reports and generated passwords remain under .local/poc-phase2, excluded
from commits. Only explicitly named source/test/documentation files are staged.
The secret scan covers new sources and the outgoing commit patches; exact
results are recorded in the review handoff.

## Remaining limits

- Development-password authentication and synthetic credentials are local-test
  tools. HTTP is bound to loopback and is not a production deployment design.
- Only the API has a bridge in addition to the internal database network; it has
  outbound connectivity. Document/model routes are blocked, provider credentials
  are not forwarded and provider packages are absent. This is not an egress ACL.
- Generated local state assumes a trusted local OS account. This is not a
  boundary against an administrator or an attacker controlling the Docker daemon.
- The pgvector database image is digest-pinned. The Python base image tag and
  some dependency ranges remain mutable, so builds are not a complete immutable
  supply-chain lock. That remains an explicit limitation of this development PoC.
- Docker can recreate stale IPC sockets after an unclean shutdown, and available
  subnet pools are a host prerequisite. This code does not reset Docker or remove
  other projects' networks to solve host failures.
- A failed database startup may prevent safe ownership verification for cleanup;
  the retained run is flagged for review rather than deleting any resource.

Legal's failed state is unchanged. Phase 1, HR, Finance, original central
databases, document/RAG source and the repository tenant manifest are untouched.
