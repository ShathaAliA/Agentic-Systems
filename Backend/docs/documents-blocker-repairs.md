# documents-ai blocker repairs — local verification

Base: documents-ai b45a4b2469158da2e98f2dd449033a91337cf4fb.
Working tree: Agentic-Systems-Documents. No merge or publication is authorized.
Legal remains administratively failed: its live row was not read or changed.
Existing HR/Finance/Central databases, containers, secrets and volumes are excluded.

## Changes

- Missing document audiences deny access. Department AND role match within each
  rule; rules are ORed. Explicit null/null means all/all within an independently
  authorized tenant. This does not implement shared cross-tenant knowledge.
- A correlated audience EXISTS is applied before vector ORDER BY/LIMIT. Delivery
  rechecks active identity, tenant scope, audience, current approved document and
  version, exact source name/quote/location and the bound conversation ID.
- Source DTOs use C06 Answer/Source. Existing chunk schemas cannot distinguish
  physical locations from legacy page-1 fallbacks or corrected text: the adapter
  deliberately cites `reviewed_text` / `chunk:<id>` without inventing pages/cells.
  Physical page/slide/cell provenance still needs an additive metadata migration.
- `/ask` accepts a strict JSON Question. It uses `execute_with_result_store`, never
  tenant `question_results`. Missing integration returns 503 before reservation or
  provider dispatch. There are no environment-selected fallback credentials.
- Publish/revise/archive/metadata/audience commands use JSON with expected_version;
  file replacement remains multipart with expected_version. Publishing replaces
  audiences explicitly in the same transaction, requires pending review and the
  matching version row, and serializes same-tenant publication before row locks.
- Physical document deletion is not exposed. POST /{id}/archive uses
  documents:archive. The permission is catalogued additively; no existing role
  receives an automatic grant. Admin must explicitly assign approved policies.
- ResponseSecurityMiddleware is actually installed; import-time database loading
  and raw SQL error printing are removed. Router errors log static events.
- Unsafe fixed-port/drop test fixtures now use the guarded fresh-cluster fixture.
- Docker test runner accepts an allowlisted PostgresImage; documents-ai push CI
  is enabled and OCR prerequisites are added. Hosted CI and the full runtime image remain unverified. A separate test-only
  image was built successfully in the isolated WSL engine described below.

## One authority and migration compatibility

Historical migrations are preserved, including usage002_result_payload,
usage003_merge and tenant_0007. No stamp, purge, copy or historical rewrite.

New Central revisions: usage004_fence -> rbac002_archive. The payload trigger
rejects new non-null business payloads and payload changes, while allowing
metadata/accounting updates on legacy rows. Legacy Central content is retained;
its accessibility/retention requires a separate approved migration and backup
review before production. The ORM does not map this legacy column.

New tenant revision tenant_0008 freezes legacy question_results against
INSERT/UPDATE/DELETE/TRUNCATE. Runtime manifest grants it no privileges; the
operator-owned table is retained for audit. Existing installations need a reviewed
migration and grant reconciliation: editing the manifest does not change live ACLs.
The real manifest stays ready:false, pinned to tenant_0008. No tenant activation.

## Gate 4 handoff (not implemented by these repairs)

Core must inject document_result_store_factory(user_id, conversation_id,
department_id). Before returning a restricted PostgresResultStore, the trusted
factory must validate the active conversation owner and scope from the Core
conversation service, not from request assertions. Bind DocumentSourceAuthorizer
with the same identities, the trusted Central engine and authorized tenant-session
factory. Its callback is mandatory on every delivery/replay. Use separate runtime
and migration identities and the independent Core result database. Never dual-write
or return legacy tenant results. This branch does not contain Core Gate 3 yet; no
factory is configured and real ask stays disabled. No provider request was made.

## Verification

Completed before final changes: 464 offline tests, 0 failures, 0 skipped;
10 real extraction/OCR checks included; targeted HTTP/security group 109 passed.
After JSON/version guard changes: targeted group 83 passed.
Fresh PostgreSQL evidence: document compatibility/locking 2 passed; legacy
least-privilege 1 passed; C06 result-store 16 passed; DB isolation 19 passed.
Final full-suite and expanded freeze/concurrency reruns are recorded below.

Each native run uses a fresh GUID cluster, random loopback port, generated private
credentials, before-backups and owner/ACL evidence. Only its own cluster is stopped;
recovery files remain under ignored .local/native-auth-tests. No existing database
is a test target. SQL assertions are actual PostgreSQL, not a fake SQL engine.

### Reproduce

From the repository root with an existing disposable Python environment:

    python Backend/tools/run-core-tests.py --suite unit
    ./Backend/tools/test-auth-postgres-native.ps1 -Python <absolute-python> -TestFile tests/test_document_repairs_postgres.py
    ./Backend/tools/test-auth-postgres-native.ps1 -Python <absolute-python> -TestFile tests/test_document_results_postgres.py

On an available safe local Docker engine, build the test image then use a new
internal-network cluster only (never an existing Compose environment):

    docker build --target test -t ai-platform-core:test Backend
    ./Backend/tools/test-auth-postgres.ps1 -TestFile tests/test_documents_postgres.py -PostgresImage pgvector/pgvector:pg17
    ./Backend/tools/test-auth-postgres.ps1 -TestFile tests/test_document_provisioning_postgres.py -PostgresImage pgvector/pgvector:pg17

The original native pgvector blocker was resolved using a separate Docker daemon
in a new owned WSL distro. Docker Desktop was never started. See the current
pgvector evidence below; this does not authorize Legal activation or real delivery.

## Final checkpoint

- Final full offline run: **466 passed, 0 failed, 0 errors, 0 skipped**;
  integration/live cases are deselected, not falsely counted as verified.
  Redacted report: `.local/core-tests/de7d4219877f459c8064ef55e7817ba8/summary.json`.
- Final expanded PostgreSQL checks: compatibility/publication 2 passed
  (`62524474c25445408ac0b9135602fc5a`); immutable legacy tenant results 1 passed
  (`f06049e6767e4882b1009416a858da75`). Both new clusters stopped successfully.
- C06 PostgreSQL result store: 16 passed (`e9d6959233914067a457296650fe41a1`).
- PostgreSQL isolation: 19 passed (`98cc9380d0e04162b5478fc4b6ea1d5e`).
  **38 distinct PostgreSQL cases** across these four suites; repeat runs are not
  added to inflate the total. All clusters stopped and retained for recovery.
- YAML parsing, documents-ai push trigger and PowerShell parser checks passed.
- Edited/new-source secret scan found no introduced credential literals, private
  keys or real .env files. Two DSN pattern matches were unchanged variable-based
  templates for generated private test credentials; no values were printed.
- Historical migration files unchanged. Core worktree remains clean at 2f69ee4.
- Remaining gates after the later pgvector run: full runtime Docker build/hosted CI,
  trusted Core Gate 3 conversation binding, precise physical-source provenance,
  and separately reviewed handling of retained Central legacy payloads.
- At that checkpoint no commit had been created. Current local commits are
  prepared after the pgvector run; no push, merge, provider call, live database
  migration or Legal status change is authorized. Gate 4 has not started.

## Isolated pgvector follow-up — 2026-11-16

All **26 pgvector cases passed**, across five fresh clusters:

| Suite | Passed |
| --- | ---: |
| documents (schema, audiences, versions, extraction storage) | 21 |
| document_provisioning (full tenant_0008 contract + least privilege) | 1 |
| upload_retrieval_simple | 2 |
| upload_rollback | 1 |
| document_pipeline (authenticated real PDF HTTP lifecycle) | 1 |

The HTTP lifecycle uses actual development sessions/RBAC, restricted PostgreSQL
application credentials, real PDF detection/extraction, reviewer-only GET review,
Arabic correction with before/after audit, explicit manual publication, pgvector
ranking, current-version filtering, forbidden-nearest-chunk exclusion before top-K,
original-file access denial, cross-department denial and post-archive exclusion.
Original bytes are checked by hash. Vectors are deterministic fixtures, not a
real embedding-model quality measurement. No LLM or embedding API was called.

The reviewer-only `GET /api/documents/{id}/review` route returns extracted text,
version and status under documents:update, without an original-file path.

### Engine isolation and recovery evidence

- New WSL distro: ai-documents-6cb009232cd7, imported from the official Alpine
  minirootfs over HTTPS after verifying its published SHA256. The distro is not
  made the default. Its disk lives under ignored private `.local` state.
- Separate daemon: /run/documents-test-docker.sock, data root
  /var/lib/documents-test-docker. Initially empty, no Docker Desktop data disk or
  existing volume was mounted. No TCP Docker socket, port publishing, privileged
  client, host Docker socket mount or database deletion.
- Separate internal network and GUID-labelled PostgreSQL container for each suite;
  private generated password files, pre-migration dumps/globals and owner/ACL
  evidence. Non-root test clients have read-only roots, dropped capabilities and
  no-new-privileges. Code is mounted read-only.
- pgvector image: pgvector/pgvector@sha256:b513c48566e4777cd3370451400000c78eda6a08131d68f4c31d4a76f27bd8fd.
- Test-only Python dependency image: ai-documents-contract:test, built from the
  Core versions and lightweight extraction/test packages; model libraries excluded.
  This is not proof of the full production Dockerfile build.
- All five test PostgreSQL containers were verified exited. The owned daemon then
  shut down gracefully; only the owned distro was terminated. Its disk, stopped
  containers and backups are retained. docker-desktop stayed stopped throughout.
- Safe XML/log copies and summary are under
  `.local/ai-documents-6cb009232cd7/reports` and `docker-summary.json`.
  The private setup/orchestration scripts and pinned test-image requirements are
  retained beside them; none belongs in Git. No real database was connected.

The daemon intentionally disabled IPv4 forwarding and the default bridge. Docker
emitted its forwarding warning, but communication on each explicitly created
internal network worked and all SQL/HTTP assertions passed.

The CI pgvector matrix now includes all five suites using the existing guarded
PowerShell runner. It has not been executed remotely because publication is not
authorized. General setup references: [Docker daemon isolation options](https://docs.docker.com/reference/cli/dockerd/),
[Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/),
[WSL import commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).

### Final offline regression after review-route/CI additions

466 passed, 0 failures/errors/skips, 131 integration/live cases deselected.
Current report: `.local/core-tests/9d64ee4fc52a45c19428691ff33d3db1/summary.json`.
No unit or pgvector regression remains failing in the executed suites. The 38
previous native PostgreSQL checks are additional evidence; they were not repeated
unnecessarily. No live provider, embedding-quality or production-image acceptance
is implied. Local commits are split by migrations, runtime boundaries, offline
contracts, PostgreSQL pipeline tests, CI/tooling and documentation. No publication.
