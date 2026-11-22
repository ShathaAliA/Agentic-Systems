# C06 Gate 4.1 — independent sealed sources

Scope: feature/core-backend only. Local implementation and fresh PostgreSQL 18.6
verification. No documents-ai changes, provider calls, push, merge or live database
operations. Legal remains administratively failed; its live state was not queried.
HR/Finance/Central development databases, containers and credentials are excluded.

## Defect independently reproduced

The historical c06_0001 source trigger compares a caller-writable writer_txid to
txid_current(). A restricted INSERT-only role could insert a result with a future
counter, commit, consume the intervening transaction and then append a source when
the counter matched. The new test reproduced this on PostgreSQL 18.6 in a separate
fresh c06_legacy_* database. It intentionally leaves that synthetic legacy proof
retained and stops its owning cluster. The old read-integrity comparison would
reject the resulting source/payload mismatch; this is not proof of content leakage.
No SQL script from the external PG16 report was executed.

## Additive design

Published c06_0001 and c06_0002 files are unchanged and now hash-guarded by a unit
test. c06_0003 changes no historical result/source/conversation rows or columns.
writer_txid remains historical metadata and is no longer a source-writing authority.
No xmin-based or transaction-counter-based security decision remains in the seal.

- AFTER INSERT on c06_results derives the entire normalized source index from the
  immutable payload in the same transaction. The adapter writes the parent only.
- Runtime has SELECT on c06_result_sources, no table/column INSERT. It cannot call
  the derivation function directly. No UPDATE/DELETE/TRUNCATE/DDL grant is added.
- The trigger function is SECURITY DEFINER owned by the limited existing schema
  owner, not a superuser, database bootstrap/provisioner or bypass-RLS role. It has
  fixed pg_catalog search_path, row_security=on, qualified relations and no dynamic
  SQL. PUBLIC function execution is revoked. Its owner is not granted to runtime.
- The historical child-trigger function name is retained, but its body now permits
  only the nested derivation trigger and exact ordinal/payload source equality.
  Ordinary direct inserts are rejected even with a legacy INSERT grant and a
  matching writer_txid. Parent uniqueness prevents replay from deriving new rows.
- Runtime checks schema head, source INSERT privileges, schema CREATE, table-owner
  memberships and definer security/owner attributes on every checkout. Old schemas
  and unsafe legacy grants fail closed; no compatibility fallback bypasses sealing.
- Migration requires a non-elevated existing table owner. In one transaction and
  under DDL locks, FORCE RLS is temporarily removed for that owner to inspect ALL
  historical source rows, then restored before commit. Mismatches abort migration;
  rollback retains the old revision, original data and FORCE RLS. No automatic repair.

The identity DTO/version remains 1; the independent migration head is c06_0003.
Central and tenant migration graphs, RBAC behavior, public API routes, source
providers and document/RAG models are unchanged. Gate 4.2 has not started.

## Grants and future deployment boundary

Only new test clusters were configured. The schema owner is a generated-test
NOLOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION, NOBYPASSRLS role with
necessary schema/table ownership. Bootstrap credentials never become API credentials.
Runtime receives SELECT on identity/version/source metadata and SELECT/INSERT on
results/conversations/turns. Forced RLS and immutable mutation triggers remain.

A future real upgrade needs its own backup, owners/ACL snapshot, ownership and grant
review. Do not execute this migration as a superuser or silently transfer ownership
of live tables. Existing runtime source INSERT grants must be removed explicitly by
the approved operator; migration code does not guess application role names or revoke
legitimate live connections. Until configured correctly, application access is denied.
This work does not approve any deployment, real-data use or Legal activation.

## Focused restore verification

A pg_dump custom archive of synthetic C06 data was actually restored into a newly
created empty c06_restored_* database in the SAME newly generated test cluster.
pg_restore used --single-transaction --exit-on-error --no-owner and the limited
schema-owner role. Public schema ownership was configured only on this new target;
source DB ownership and historical rows were not changed. No --clean, --create,
--disable-triggers or deletion was used. Existing fresh-cluster roles were reused;
this is not cross-cluster role/secret rotation or the complete Gate 4.5 DR drill.

The test compares every result/source row, verifies FORCE RLS, reads through the
actual adapter (canonical payload hash, source index and explicit synthetic callback),
rejects direct post-restore append, then saves/reloads a fresh result successfully.
The historical writer_txid values are preserved verbatim and are irrelevant to sealing.
The earlier deterministic counter-collision test independently checks that the
new child trigger rejects insertion even when the old counter condition is true.

## Actual local results

| Check | Result |
| --- | --- |
| Legacy weakness proof on pinned c06_0002 | Reproduced with restricted runtime |
| New Gate 4.1 PostgreSQL suite | 8 passed, zero failures/errors/skips |
| Existing Gate 2 PostgreSQL regression | 16 passed |
| Existing Gate 3 owner/concurrency/history regression | 13 passed |
| Full sanitized offline Core suite | 410 passed, zero failures/errors/skips |
| Historical c06_0001/0002 migration hashes | Unchanged |

The eight cases cover exact/empty source derivation, legacy-grant fail-closed behavior,
matching-counter rejection, ordinary owner DML denial, child-failure atomic rollback,
elevated migration-owner rejection, full focused restore and failed-upgrade RLS rollback.
37 distinct PostgreSQL test cases passed across the three final suites; exploratory
repeats are not added to that total. Tests use synthetic source authorization only.

Private retained evidence (ignored, never commit/share credential files or dumps):
- Unit: .local/core-tests/ae88f3427ec646cdb92295bd63f7b275/summary.json
- Sealing: .local/native-auth-tests/46822f63e4ee483280332bd485b37995/results.xml
- Gate 2: .local/native-auth-tests/0ab090eedc9840b38f4ede70fb07ca2c/results.xml
- Gate 3: .local/native-auth-tests/d2cff4ced29749f99daa6264ee04363f/results.xml

All new clusters were backed up before mutations, verified stopped and retained.
No existing DB/container/volume was a test target. PostgreSQL 17, Docker execution,
hosted CI, real source integration, global Admin history, full retention/encryption
policy and broader restore/recovery procedures are not claimed in this slice.
The restore case requires the native guarded runner; a Docker invocation without
native tools reports it skipped and cannot count as a complete isolated-suite pass.

## Reproduce safely

From the repository root with a disposable Python environment:

    python Backend/tools/run-core-tests.py --suite unit
    ./Backend/tools/test-auth-postgres-native.ps1 -Python <absolute-test-python> -TestFile tests/test_c06_sealing_postgres.py
    ./Backend/tools/test-auth-postgres-native.ps1 -Python <absolute-test-python> -TestFile tests/test_c06_result_store_postgres.py
    ./Backend/tools/test-auth-postgres-native.ps1 -Python <absolute-test-python> -TestFile tests/test_c06_conversations_postgres.py

The runner resolves the selected PostgreSQL bin directory, exports it as a test-only
AUTH_TEST_POSTGRES_BIN and restores the caller environment afterward. No URL/secret
is accepted in the test command. Fresh-cluster guards, allowlists and private-state
ownership validation stay enabled. Shared-SQL-credential compromise and schema-owner/
superuser DDL remain outside the immutability guarantee; securing those identities
and production encryption remain release requirements.
