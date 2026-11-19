# C06 Gate 3 — owner conversations and immutable result-linked history

Approved Core-only scope, 2026-11-16. No document/RAG implementation or provider
dispatch, no global Admin conversation inspection and no original database use.

## HTTP contract

| Method/path | Behavior |
|---|---|
| POST /api/conversations | Create owner metadata; replay same request_key/scope returns the same conversation; changed scope conflicts |
| GET /api/conversations | Owner-only keyset pagination, limit 1–100; optional owned UUID after cursor |
| GET /api/conversations/{id} | Safe owner metadata; no answer/question/source content |
| GET /api/conversations/{id}/messages | Sequence pagination, limit 1–100; after is a nonnegative bigint cursor; fresh authorization for content |

POST body (synthetic IDs here; use actual authorized department identifiers):

```json
{"request_key":"create-demo-001","mode":"department","department_ids":["22222222-2222-4222-8222-222222222222"]}
```

Mode `department` requires exactly one department; `orchestrator` permits 1–20
unique departments. This records scope only, not automatic routing. Creation
requires current documents:read for every department and does not consume a
question credit. Unknown fields, including owner/role/question claims, are rejected.
Identity always comes from validated current-user dependencies; X-User-ID and
query claims do not select an owner. Inactive/expired/revoked/unknown identities
retain Core authentication behavior. All four operations are in the 48-operation
access inventory and receive no-store even on errors.

Another user's resource/cursor is indistinguishable from a missing resource.
Admin can use its own conversations, not inspect others implicitly. No new
global permission or Admin override is granted. There is no POST/PATCH/DELETE
message endpoint, conversation deletion or user-editable owner field.

## Configuration boundary

`create_app(conversation_store_factory=...)` accepts a trusted factory taking
the already-validated user ID and returning a user-bound `PostgresResultStore`.
It is not an HTTP option. With no factory the routes return sanitized 503;
they never discover or fall back to an existing tenant/Central DSN. Gate 3 adds
no runtime .env settings, credentials, automatic migrations or server startup.
The factory must use a separately migrated D01 store and restricted connection,
with SQL parameter hiding and a real trusted source authorizer before live content
can be enabled. Tests inject synthetic factories only. No deploy-ready document
authorizer is provided in this batch.

## Storage and internal append

New independent migration `c06_0002` adds `c06_conversations` and `c06_turns` to
the separate store. Historical c06_0001/Central/tenant migrations are unchanged.
Both tables use owner RLS, append-only mutation triggers and no public grants.
The test runtime receives SELECT/INSERT only; no UPDATE, DELETE or new role flags.
Conversations have immutable owner/scope and creation-key uniqueness. Turns hold
the question once, a U1 request/fingerprint and a reference to the existing
authoritative result, not a second answer. Composite foreign keys require the
conversation and result to have the same owner; one request can link only once.

`ConversationService.append_completed` is an internal integration function, not
an exposed question-submission path. It requires active owner/current scope,
completed U1 request with matching owner/fingerprint/outcome, an authorized result
and matching conversation/source scope. It stores the exact validated question.
Advisory transaction locking serializes sequence allocation without granting
UPDATE. A duplicate request returns its existing turn; a mismatched link refuses.
No processing callback, provider, generation or accounting mutation is invoked.

A failure to insert a turn leaves the result/accounting intact. Retrying the
internal link with the verified original command is idempotent and free. There
is no automatic backfill/reconciliation worker and no reconstruction of the
original question from its hash. Gate 4's future submission workflow must address
durable intake/recovery before provider dispatch; this slice is not that workflow.

## Fail-closed historical views

Each returned turn checks current department/role access and the completed U1
record, then calls Gate 2's fresh source/version/audience authorizer and verifies
the conversation and result scope. Scope is checked again before releasing text.
Only a valid result permits question/response display. If revoked, unresolved,
missing, corrupt or lacking a validator, both text fields are null and the view
contains only turn ID/sequence/time plus access_revoked/verification_unavailable.
It does not reveal source IDs, names, quotes or old text in the placeholder.
Stored history is not rewritten or deleted. Pagination includes placeholders,
so hidden text does not silently remove/reorder an owner's turns.

The source implementation remains external and required. RLS still relies on
trusted service identity binding; it is not protection against a compromised
shared SQL credential or superuser. Runtime SQL does not copy business content
into Central. Only isolated synthetic SQLite Central fixtures (memory and
test-file connections for concurrency) were used in this work.

## Verification and database safety

```powershell
python Backend/tools/run-core-tests.py --suite unit
./Backend/tools/test-auth-postgres-native.ps1 -Python '<test Python executable>' -TestFile tests/test_c06_conversations_postgres.py
./Backend/tools/test-auth-postgres-native.ps1 -Python '<test Python executable>' -TestFile tests/test_c06_result_store_postgres.py
```

C03 runners allowlist the new group and allocate only a fresh c06_result_test
cluster with random loopback port, private credentials, pre-change dumps and
owner/ACL snapshots. Confirmed-stopped data/recovery artifacts are retained.
The new fixture upgrades c06_0001 with an existing synthetic result to c06_0002,
repeats the upgrade and asserts the original row is unchanged. No historical
revision is rewritten or stamped.

Final counts are recorded in the implementation log. Coverage includes real
HTTP owner isolation/Admin denial, creation replay/conflict, no debit on metadata,
invalid cursors, stable list/turn pagination, concurrent duplicate and distinct
links, failed-link rollback, uncertain requests, source/scope revocation, absent
validator, immutable SQL history and continued Gate 2 integrity/security tests.

A repeated run exposed a flaky concurrency fixture: multiple threads shared the
in-memory SQLite Central fixture's single StaticPool connection. The final Gate 3
fixture clones only that synthetic dataset into a temporary SQLite file with
independent pooled connections, including synthetic auth-session digests. It never
clones a development database. Concurrent duplicate/distinct linking is retried
to check stable sequencing, without weakening active-user checks.

No existing HR, Finance, Legal or Central database/container/volume was accessed.
Legal remains failed by project policy; its live state was not queried or changed.
No Docker execution, hosted CI, LLM, embedding, document processing, full restore
drill, production encryption or real source-service integration is claimed here.
Keep Gate 2's production/security limitations in force. Gate 4 and any push/merge
require separate authorization; local reviewable commits only.
Local scoped review found no blocking issue or selected credential pattern in
implementation additions; actual .env, backups and private state are excluded.
This is not a full dependency audit or production security certification.
