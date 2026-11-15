# C05 — Core integration readiness review (2026-11-15)

**Scope:** Contract audit and synthetic Core-side contract tests only on
`feature/core-backend`. No document/RAG implementation, provider calls,
database migration, tenant provisioning, branch merge or deployment. HR and
Finance live databases must not be connected to or changed. Legal remains
**failed**; its live state was not queried, and
`tenant-contract.example.json` remains `ready:false`.

## Read-only baseline and evidence

| Item | Observed |
| --- | --- |
| Core branch | `554bce50` at review start; C05 changes are additive |
| documents-ai snapshot | `d4e749b` (read-only; no cherry-pick or import) |
| Core central / tenant heads | `rbac001` / `tenant_0001` |
| documents-ai central / tenant heads | `usage003_merge` / `tenant_0007` |
| Core V1 manifest | `ready:false`, no required revision or grants |
| documents-ai manifest | `ready:false`, proposes `tenant_0007` and business grants |
| Core U1 | Server-side question reservation/settlement, idempotent request, external-result protocol; no durable result implementation |
| Core HTTP | Auth/admin/usage only; no document routes registered |
| Hosted baseline | GitHub Actions run 43704404210: unit/container and PostgreSQL isolation jobs passed at `554bce50` |

The partner branch now includes tenant document migrations, audiences,
version-pinned chunks and a tenant `question_results` table. Its current
`runtime_security.py` rejects privileged connections, correcting the
older review's warning-only finding. These are **code observations**, not
joint acceptance or proof of end-to-end tenant provisioning.

## Contract ownership and compatibility

| Boundary | Core responsibility | Partner responsibility | C05 verdict |
| --- | --- | --- | --- |
| Identity | Validated session and fresh active user | Never trust request user/role headers | Core contract present; partner integration unapproved |
| Department access | Central `has_permission`, active registry before tenant engine | Call authorized tenant routing and enforce document audience | Separate ownership; cross-branch integration pending |
| Tenant V1 | Strict loader, pinned revision, explicit table/sequence privileges, fail closed | Supply reviewed business migrations and grants | **Blocked** while `ready:false`; do not activate Legal |
| Central migrations | Preserve established `b8e1db57a78c` lineage and `rbac001` | Reconcile extra `usage002_result_payload` and `usage003_merge` without rewriting applied history | **Decision required**; do not copy/stamp/upgrade |
| Document DTO | Strict draft request/answer models, no caller identity/cost | Approve final API and source/version/audience semantics | Proposal only |
| U1 accounting | Reserve/settle, idempotency, uncertain outcomes; never store answer in Central | Trusted provider receipt and authorized durable answer storage | Core boundary tested; real integration pending |
| Result delivery | `ExternalResultStore` protocol, save-before-finish, owner/request/fingerprint/outcome checks | Real durable store, source/version/audience recheck on every delivery | **Blocked** for real persistence |
| Security | Core response no-store/error sanitization and privilege rejection | Preserve equivalent policy on all document routes, including failures | Needs end-to-end review |

## Updated partner observations (not changes)

- `documents-ai` at `d4e749b` has `tenant_0002` through `tenant_0007`,
  an explicit unready tenant manifest and a fail-closed privileged-role guard.
  Previous notes saying its central history was entirely replaced or its
  runtime guard only warns are **stale** for this snapshot.
- The partner central graph adds `usage002_result_payload` and merges it
  with `rbac001` at `usage003_merge`. Core's C04 deliberately excludes
  business payload from Central. This is a **migration-history and data
  retention decision**, not permission to delete or rewrite partner history.
- Partner tenant `question_results` is stored in the owning tenant and has
  `request_id`, `user_id`, and JSON payload. Core C04 expects an
  authoritative immutable result record including a server fingerprint and
  accounting outcome, with explicit authorized load semantics. The concrete
  cross-branch adapter and complete integrity proof are **not established**.
- The partner's `result_store.load` rechecks document status, version,
  department and audience on replay. The code observed does not establish
  complete cross-branch consistency or concurrency/rollback guarantees.
- The partner audience service treats a document with no audience rows as
  visible within authorized department scope. Confirm whether this legacy
  default is acceptable; do not silently treat absence of an audience as
  explicit publication authorization.
- The partner HTTP router contains document processing and provider paths
  not present in Core's 44-operation access inventory. Joint route policy,
  sanitized errors, immutable source references and original-file access
  need dedicated review. A passing Core-only CI run does not cover them.

## C05 tests and limits

`Backend/tests/test_c05_integration_contract.py` tests only the Core
checkout: absence of mounted document routes and Central document tables,
unready manifest, invalid ready contracts, Core migration graph,
strict draft DTOs, U1 fingerprint scope, synthetic HR/Finance/failed-Legal
RBAC, idempotent external-result replay, revocation, and unauthorized
department rejection before result-store access.

Tests use synthetic SQLite fixtures and a fake in-memory result store.
They do **not** import `documents-ai`, access real HR/Finance/Legal,
call providers, create tenants, run Alembic migrations or prove a durable
store. The synthetic Legal row remains failed.

Run from repository root in a disposable Python environment:

```powershell
python -m pytest -q Backend/tests/test_c05_integration_contract.py -p no:cacheprovider --tb=short
python Backend/tools/run-core-tests.py --suite unit
```

Hosted Core CI, if triggered by publication, must be checked separately.
No tests should be run against an existing application database. Never
use `alembic upgrade`, `stamp`, `docker compose up`, provisioning
workers, or real document routes as a C05 acceptance shortcut.

## C05 acceptance and handoff

1. Core-only contract tests and existing unit suite pass, without changes
   to application behavior or historical migration files.
2. Contract/DTO/tenant-policy drift is documented, with partner-owned
   decisions clearly separated from Core-owned checks.
3. Legal remains failed and the example tenant manifest remains unready.
4. No existing HR/Finance/Legal database or resource is used.
5. No merge, cherry-pick, PR state change, activation or production claim.

**Next gate (outside C05):** jointly approve the tenant revision/grants,
Central migration-history reconciliation, source audience/version policy,
result record integrity, persistence and provider receipt semantics before
any real integration or C06 implementation.
