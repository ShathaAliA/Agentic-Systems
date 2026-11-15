# C06 Gate 2 — isolated PostgreSQL result adapter

Approved scope: a candidate external result store on `feature/core-backend`,
synthetic data only, no public routes or document/RAG integration. Separate
database per D01, not Central tables or a department schema. Conversation history
and immutable sent-message APIs remain Gate 3 work, not removed requirements.

## Implementation

- `app/conversation_store/schema.py`: strict version-1 answer/source DTO aligned
  with approved RC2 (including typed page/slide/sheet/image/section locations).
  Boolean document IDs, missing versions, no evidence, forged fields and mismatched
  U1 request/outcome are refused. This is not a rewrite of the older Core draft
  document API or any documents-ai model.
- `PostgresResultStore` implements C04, bound to authenticated user and expected
  database. Configuration is injected explicitly; it reads no .env/DSN by itself.
  No source authorizer means no delivery, before connection. The real source
  adapter is absent: tests explicitly inject synthetic decisions. Nothing is
  wired into `/ask`, FastAPI startup or existing tenant routing.
- Each checkout refuses privileged/inherited system roles and database ownership,
  checks the store identity, and binds a transaction-local owner context. SQL uses
  explicit public schema qualification; values use bound parameters. Reads check
  owner, strict schema, outcome, payload digest and normalized source index before
  a fresh authorization decision. Application failures expose generic safe codes.
- Result plus source rows commit atomically before C04 finishes U1. PostgreSQL
  uniqueness arbitrates concurrent writers; conflicting/identical duplicate saves
  return conflict rather than overwrite. Normal U1 replay reads, never saves again.
- UPDATE/DELETE/TRUNCATE triggers enforce append-only history; source inserts must
  be in the original result transaction, preventing late additions. RLS protects
  reads/inserts with the bound owner context. No mutable second answer copy exists.

## Independent migrations and privilege boundary

`result_store_alembic.ini` / `result_store_alembic/` have independent head
`c06_0001` and version table `c06_store_version`. The environment requires an
explicit approved connection and expected database, never central credentials.
No historical Central/tenant revision is changed or stamped. Downgrade refuses
automatic destruction. Application tables are `c06_store_identity`, `c06_results`
and `c06_result_sources`; no cross-database foreign keys exist.

Test setup grants the fresh `c06_test_api` role only CONNECT, public schema USAGE,
identity SELECT and result/source INSERT/SELECT. No TEMP, CREATE, UPDATE, DELETE,
TRUNCATE, ownership, CREATEDB, CREATEROLE, SUPERUSER, replication or RLS bypass.
Owner-scoped migration access is never passed to the adapter. RLS relies on a
trusted service choosing the authenticated user: shared runtime credential theft
or arbitrary SQL execution could change session context. This is not a claim
that RLS protects against a compromised application or database superuser.

The digest detects accidental corruption; it is not a cryptographic signature
against a privileged attacker. Database administrators can alter triggers/schema;
application Admin has no such path here. Encryption, managed keys, external audit
retention and production backup/restore remain release gates before real data.

## Actual database-level test changes

The native C03 runner created only a fresh random-port cluster and database
`c06_result_test`. It captured a readable database dump and global role backup
before migrations/privilege changes. The fixture saved owners/ACLs before/after,
created the independent tables/functions/policies, created a new limited role,
revoked PUBLIC CONNECT on that **new cluster's** c06_result_test/postgres/template1,
and granted only explicit test access. Owners stayed unchanged. No original
department/central database or container was connected to or modified.

The test server was confirmed stopped; private recovery artifacts were retained
under ignored `.local/native-auth-tests/`. They include generated secrets and
must never be committed/shared. Earlier iteration clusters are also stopped and
retained; migrations were tested anew, not reapplied to existing project databases.
Backup readability was checked; a complete restore drill was not executed.

## Commands and evidence

From repository root, with the isolated Python environment and PowerShell 7.2+:

```powershell
python Backend/tools/run-core-tests.py --suite unit
./Backend/tools/test-auth-postgres-native.ps1 -Python '<absolute test Python executable>' -TestFile tests/test_c06_result_store_postgres.py
```

The allowlisted native/Docker runners select `c06_result_test` only for this new
test module; old groups keep `auth_test_db`. The Core orchestrator includes the
new isolated group, so future hosted verification will run it. Test-image Docker
support is configured but was not executed locally in Gate 2.

Final test counts and scoped review are recorded in the implementation log.
Coverage: strict malformed payloads/configuration, independent migrations and
idempotent upgrade, no Central tables, reopen/replay, source revocation, owner
RLS, direct SQL privilege denials, owner-trigger immutability, sealed sources,
atomic rollback on source failure, concurrent unique writes, callback mutation,
corrupt rows/hash/index, lost ack, controlled/abrupt U1 closure and no_answer.
The U1 ledger in this suite is separate in-memory SQLite, not a real Central DB.

## Remaining gates

No real source authorization, document integration, provider/LLM request,
production credential configuration, restore drill, encryption/key-management
implementation or conversation endpoint is claimed. Agree the single-authority
handoff for partner tenant question_results before joint delivery; no dual write
or migration of partner data. Legal remains failed and is not a test target.
No Gate 3 implementation, push, merge or existing HR/Finance change is authorized.
Local review: selected provider-key/private-key/credential-bearing URL/session-token
patterns found zero matches in implementation additions. No actual .env, dumps,
private credentials or recovery state are included. This is a scoped pattern and
manual review, not a complete dependency audit or production certification.
