# C06 recovery runbook (Core question lifecycle)

Applies to the isolated C06 result store (`c06_*` tables). Synthetic/test use only until the
company decisions at the end are made. Never run any step below against HR, Finance, Legal or
the Central database.

## 1. Backup

Before any migration, grant change or restore, from the store's own cluster:

1. `pg_dump -Fc -d <c06 database> -f <file>` for the store database only.
2. `pg_dumpall --globals-only --no-role-passwords` for roles (the store relies on
   `c06_store_owner` and the runtime login role).
3. `pg_restore --list <file>` to prove the archive is readable. Keep dumps in a private,
   access-restricted directory; they contain question and answer text.

## 2. Restore into a new database

Never restore over a live store. Use an empty database in a cluster where the roles exist:

1. Create the new database and make the limited schema owner own `public`
   (`ALTER SCHEMA public OWNER TO c06_store_owner`).
2. `pg_restore -d <new db> --single-transaction --exit-on-error --no-owner --role=c06_store_owner <file>`.
3. Revoke `CONNECT` from PUBLIC and grant it only to the runtime login role.
4. Point the application at the new database only after the checks in section 3 pass.

`tools/test-auth-postgres.ps1 -TestFile <c06 suite> -RestoreDrill` performs these steps on a
throwaway cluster and verifies the result (see Gate 4.5 verification).

## 3. Post-restore checks

Row counts and content hashes of all five tables equal the source; version is `c06_0004`; the
identity row names the new database; RLS is forced on every table; the immutability trigger and
the source-derivation function exist; the runtime role passes the store's fail-closed checks;
every stored result still verifies its payload hash and source rows; UPDATE/DELETE/TRUNCATE
are refused; another owner sees nothing; new writes work.

## 4. Request recovery (per owner)

Use `QuestionRecovery.report()`; it returns ids and statuses only, never question or answer text.

| Status | Meaning | Action |
| --- | --- | --- |
| `linked` | Completed and in the conversation | None |
| `completed_unlinked` | Result saved, crash before the turn was linked | `QuestionRecovery.relink(conversation_id, request_key)`: idempotent, no dispatch, no new charge. Repeating the original `ask` also links it |
| `not_started` | Intent recorded, request refused or interrupted before admission | None: no charge, no dispatch. The same key can be asked again with the same text |
| `reserved` / `running` | In progress or abandoned | Wait; do not redispatch. Escalate to the accounting owner if it never completes |
| `uncertain` | Provider cost or save outcome unknown | Manual review against provider receipts. No automatic refund, no redispatch |
| `refunded` | Confirmed technical failure | None |
| `integrity_error` | Accounting and stored data disagree (fingerprint, or completed without a result) | Stop. Do not relink or edit; investigate from backups |

## 5. Never

Delete or edit stored rows, refund an `uncertain` request on a guess, dispatch a request again to
"fix" a missing turn, or share dumps or the private recovery directories.

## 6. Retention (open)

The store is append-only by design: nothing in Core deletes or expires conversations, turns,
results or intents, and no job does. Retention length, legal hold, encrypted backups and key
custody, backup frequency and restore targets (RPO/RTO), and an external tamper-evident audit
store are company decisions that are not made yet and must be before any real data.
