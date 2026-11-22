# Human handoff — operations runbook

Scope: deploying, operating and recovering the handoff feature (`app/handoffs`, C06 migration `c06_0005`, Central
migration `handoff001_policy`). The contract and design decisions are in [handoff-contract.md](handoff-contract.md).
Legal status remains `failed`; this runbook is for non-production environments until that changes.

## Deploy order

1. **Back up** the C06 result store and Central (as for every C06 migration). Keep the backup until step 6 passes.
2. **Central migration** `handoff001_policy` (adds `handoff_policy` and `handoff_policy_audit`): normal Central
   migration role. Forward-only.
3. **C06 migration** `c06_0005` with the limited schema-owner role (never a superuser). It creates five insert-only tables with
   forced row-level security and triggers. The runtime login role needs `SELECT, INSERT` on the five tables and
   nothing else; the store refuses to start below revision `c06_0005` or if the runtime role owns any table.
4. **Permission rows**: run `seed.py` (or an equivalent administrator step) so `handoffs:request`, `handoffs:handle`
   and `handoffs:audit` exist in Central. Existing roles are not changed by the migration.
5. **Map roles** through the role-policy API: `handoffs:request` to Employee, `handoffs:handle` to Department
   Manager and Deputy Department Manager. `handoffs:audit` is granted to the Admin role only (global scope).
   Revoking a mapping takes effect on the next request; nothing is cached.
6. **Verify**: with a test employee and a head, create a handoff from an unanswered turn, claim, reply, read, close,
   and confirm the Admin audit read shows it and the access log gained a row.

## Settings an Admin can change

| Setting | Where | Default | Notes |
| --- | --- | --- | --- |
| Open handoffs per employee | `PUT /api/admin/handoffs/policy` (`open_cap` 1–50, `expected_version`) | 5 | History in `handoff_policy_audit`; applies to the next creation |
| Who may request / handle | Role-policy API | Employee / Manager, Deputy | Department-scoped; a global role never makes someone a handler |
| Who may audit | `handoffs:audit` on a global role | Admin | Read-only, every read logged |

## Monitoring and routine checks

- `409 handoff_rule_violation` means the database refused a transition or message (closed thread, wrong assignee,
  concurrent claim lost). Occasional occurrences are normal; a sustained rate suggests a client bug.
- `429 handoff_open_limit` and `429 handoff_credit_exhausted` are user-facing limits, not faults.
- `503 result_store_*` codes are the same C06 store conditions as for questions (identity, privilege, sealing,
  revision). Follow the C06 runbook.
- The bell counter endpoint is cheap and meant for polling; a reverse proxy rate limit is the protection against abusive
  polling (application-level limits are out of scope).
- Audit review: read `GET /api/admin/handoffs/access-log` periodically. Rows cannot be changed or deleted by the application.

## Backup, restore and recovery

The five tables are part of the C06 store, so the existing backup and the restore drill cover them
(`tools/test-auth-postgres.ps1 -RestoreDrill` includes the new tables). Handoff history is immutable: there is no
"undo". A wrong summary or message is answered with another message, never edited. Downgrading `c06_0005` is
deliberately refused; recovery means restoring a backup under an explicit plan.

## Known limits

- Messages have no idempotency key; a client retry after a lost response can post twice.
- No retention, deletion or encryption policy is decided yet (same open item as the rest of C06).
- Summaries are written by the employee; personal data in them is the employee's and the handler's responsibility.
- Notifications are in-app only (feed and counter). SSE, email and Teams are later work.
- Converting a human answer into approved knowledge (H6) depends on the Documents flow and is not built.
- PostgreSQL 18.x is untested; the suites ran on 17.x.
