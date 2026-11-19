# C06 Gate 4.2 — trusted conversation/source binding

Core-only implementation on feature/core-backend. No tenant schema changes,
document/RAG imports, LLM calls, public question submission or U1 coordinator.
Legal was not queried or activated. Existing HR/Finance/Central databases and all
existing containers were excluded. documents-ai remains unchanged.

## Integration contract

Core supplies ConversationResultStoreFactory(metadata_factory, central_engine,
provider). The metadata_factory(user_id) contract is unchanged: it returns an
explicitly configured restricted PostgresResultStore. The wrapper builds a fresh
adapter rather than mutating the original or a shared cached authorizer.

- metadata_for(user_id) supports existing metadata/history routes. For each result,
  its validated conversation ID is resolved to an owned immutable Core conversation.
- factory(user_id, conversation_id, department_id) is the three-argument Documents
  result-store shim. It validates the owner and stored single-department mode/scope
  before returning. The asserted department cannot expand scope. It refuses an
  orchestrator conversation, even one containing only a single department.
- bind_conversation returns a frozen owner/conversation/department tuple. Current
  active identity, documents:read RBAC and active/non-archived departments are
  checked through Core's existing service. Unknown/foreign conversations return
  the same 404. Global Admin does not gain another owner's history.
- source_authorizer_provider is optional explicit server injection in create_app.
  No provider is installed by default. Missing providers disable content delivery;
  metadata APIs do not need source verification and remain usable.

The provider implements:

    authorizer_for(user_id, conversation_id, department_ids: tuple[str, ...])
        -> callable(user_id, answer_payload) -> bool

The tuple is derived from stored metadata, never request headers/query assertions.
Both cited sources and unanswered_departments must be within that stored scope.
The three-argument shim additionally requires the payload conversation to match
the explicitly bound conversation. The provider is resolved anew on every delivery,
including replay and no_answer; there is no positive authorization cache.

Documents owns the eventual provider implementation. It must construct its existing
DocumentSourceAuthorizer using authorized tenant sessions and the server-bound
owner/conversation/single department, and verify current document audience, approved
current version and exact citation data. Core does not substitute documents:read
for this document-specific decision. No provider implementation was copied here.
Instantiation and joint validation remain a later integration gate.

Global Admin's existing department-wide RBAC is preserved, including its own
conversation creation without department membership. This does NOT make document
audience checks optional or resolve the separate Admin RAG audience policy.

Existing orchestrator metadata/history can pass its full immutable tuple to an
explicit capable provider. This is not a cross-tenant broker or multi-department
question execution implementation. The default remains unavailable.

## Failure and content handling

| Boundary | Outcome |
| --- | --- |
| Direct bind: unknown/foreign conversation | 404 conversation_not_found |
| Direct bind: inactive account/revoked scope/mismatched department | 403 |
| Provider returns literal True | Recheck current Core access, then allow |
| False/None/non-boolean truthy return or explicit 403 | 403 question_source_access_revoked |
| Missing provider, invalid callback, timeout or other exception | 503 source_authorizer_unavailable |
| Missing/foreign conversation discovered during stored-result verification | 503, no content |
| Store factory throws or returns wrong adapter/owner | 503, sanitized configuration error |

The low-level PostgresResultStore also sanitizes arbitrary source callback failures.
Exceptions never expose provider text/SQL/credentials through these errors.
Payload copies protect delivered records and direct shim .authorize callers from
callback mutation. Canonical hash/source-index validation still precedes callbacks.

Existing history behavior masks a failed result's question AND answer/sources as
verification_unavailable (503) or access_revoked (403), preserving pagination and
the other verifiable messages. No stored record is modified or deleted.

Core rechecks active identity/RBAC after provider IO. This is not a distributed
atomic authorization snapshot: a later revocation can race a completed response.
Providers must set bounded database/network timeouts themselves. A synchronous
callback cannot safely be force-interrupted by this wrapper. TimeoutError handling
is tested; preemptive cancellation or a network timeout implementation is not claimed.

## Verification and boundaries

Tests use real PostgreSQL 18.6 for the sealed result store and synthetic temporary
SQLite Central fixtures for identity/RBAC/accounting. All PostgreSQL runs use fresh
private clusters, random loopback ports, pre-mutation backups and confirmed shutdown.
No project database connection strings are used. These tests prove the Core binding
contract; they do not prove real tenant audience/citation authorization.

The new suite covers explicit decision values, provider construction/callback errors,
missing provider, timeouts, owner/Admin isolation, cross-conversation and cross-scope
denials, account/membership/role/department changes, revocation during provider IO,
no_answer, callback copies and actual authenticated HTTP per-message masking.
Offline tests also characterize immutable tuple derivation and single-department
shim refusal for orchestrator metadata. Existing API tests still reject submission,
editing/deletion and unauthenticated or incorrectly bound stores.

Run from the repository root with a disposable Python environment:

    python Backend/tools/run-core-tests.py --suite unit
    ./Backend/tools/test-auth-postgres-native.ps1 -Python <absolute-test-python> -TestFile tests/test_c06_authorization_postgres.py

The new integration group is registered in the aggregate runner and both exact
native/Docker allowlists. Docker execution, PostgreSQL 17 and hosted CI were not
run in this local batch. No image, workflow or migration change is required.

Public intake, U1 lifecycle, durable question recovery, actual Documents provider
wiring, broader recovery/retention and production readiness remain outside 4.2.

## Actual local results — 2026-11-17

| Suite | Passed | Failed/errors/skipped |
| --- | ---: | --- |
| Final full sanitized offline Core | 423 | 0 / 0 / 0 |
| New source-binding PostgreSQL | 24 | 0 / 0 / 0 |
| Existing result-store PostgreSQL | 16 | 0 / 0 / 0 |
| Existing conversation/concurrency PostgreSQL | 13 | 0 / 0 / 0 |
| Existing seal/restore PostgreSQL | 8 | 0 / 0 / 0 |

61 distinct PostgreSQL cases passed; exploratory reruns are not added. The existing
Starlette/httpx deprecation warning remains. The first restricted offline invocation
stalled without a result and was interrupted; the final sanitized runner completed
successfully. No failed/missing run is counted as passing.

Private ignored evidence (do not commit/share credentials or backups):
- Offline: .local/core-tests/a2976da3776e4e7c818eabb36ea5062f/summary.json
- Binding: .local/native-auth-tests/fba8403ec8dc41a2b8f31e29b979fe10/results.xml
- Storage: .local/native-auth-tests/b12325ee800142bcb5e27ca28721cb96/results.xml
- History: .local/native-auth-tests/739798192cbb4794ac3116d509d35bf9/results.xml
- Seal/restore: .local/native-auth-tests/2d5624a712134885882f0760f7000e00/results.xml

All fresh cluster runners confirmed shutdown and retained their private recovery
directories. No data deletion or live database privilege change was performed.
