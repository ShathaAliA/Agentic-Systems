# C06 Gate 4.5 — recovery, restore and retention verification

Core only, feature/core-backend. No documents-ai, public route, provider/LLM, or live
HR/Finance/Legal/Central database. Legal remains `failed`. See the
[runbook](c06-recovery-runbook.md).

## Delivered

- `QuestionRecovery` (`app/conversation_store/recovery.py`): owner-scoped, read-only `report()`
  classifying each recorded question (`linked`, `completed_unlinked`, `not_started`, `reserved`,
  `running`, `uncertain`, `refunded`, `integrity_error`) with ids only; and `relink()`, an
  idempotent repair of a completed result that never calls a processor or touches accounting.
  A global all-owner view needs audited Admin access and is deliberately not provided.
- `tools/test-auth-postgres.ps1 -RestoreDrill`: dumps the finished throwaway C06 database,
  restores it into a NEW database in the same throwaway cluster and runs
  `tests/test_c06_restore_verify_postgres.py` against the restored copy.
- Recovery runbook with backup, restore, post-restore checks, per-state actions and the open
  retention decisions.

## Actual results — 2026-11-19 (Docker runner, PostgreSQL 17.10)

| Suite | Passed | Failed / skipped |
| --- | ---: | --- |
| Recovery report / re-link | 7 | 0 / 0 |
| Restore drill verification (restored database) | 5 | 0 / 0 |

The drill verified: table row counts and content hashes equal the source for all five tables
(and non-empty), version `c06_0004`, identity naming the new database, forced RLS on every
table, the immutability trigger and derivation function, the runtime role passing the store's
fail-closed checks, every stored result verifying its hash and source rows, UPDATE/DELETE/
TRUNCATE refused, another owner seeing nothing, and new writes working after restore.

## Limits

- PostgreSQL 17.10, not 18.6; the restore went through the container's own `pg_dump`/`pg_restore`.
  The native-only restore test (Gate 4.1) is still skipped on Docker and was not re-run.
- The restore drill covers the C06 store only; Central accounting is not part of it, so
  `QuestionRecovery` was tested against synthetic Central fixtures, not a restored one.
- Earlier suites (4.3/4.4 and regressions) were not re-run after these additive changes.
- No retention, deletion, encryption-at-rest, key custody or external audit store exists;
  these remain open decisions (runbook section 6).
- Hosted CI and the sanitized offline runner were not run.
