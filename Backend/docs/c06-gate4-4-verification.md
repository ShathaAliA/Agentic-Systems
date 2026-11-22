# C06 Gate 4.4 — isolation, permission and secret-handling verification

Scope: the internal question lifecycle added in 4.3, exercised end to end on a fresh
isolated PostgreSQL. Core only; no documents-ai, public route, provider/LLM, or live
HR/Finance/Legal/Central database. Legal remains `failed`.

## Coverage (`tests/test_c06_security_postgres.py`, 20 cases)

| Area | What is proven |
| --- | --- |
| Lost access | Inactive user, membership, role, failed or archived department each get 403 with no intent, charge, turn or dispatch (5 cases) |
| Revocation during processing | Result is never delivered; no turn is linked; after restoring access the replay links it without a second dispatch or debit |
| Owner isolation | The same request key is independent per owner; Admin cannot use another owner's conversation (404, nothing recorded) but can use its own |
| Database enforcement | RLS hides intents/turns/conversations/results from another owner; a wrong-owner insert and a cross-owner conversation reference are both rejected |
| Privileges | Runtime role holds exactly SELECT+INSERT (results sources: SELECT only); no UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER; cannot create/drop tables, disable RLS or drop the immutability trigger |
| Hostile content | SQL/HTML text is stored and returned verbatim, tables stay intact, and the text never appears in the Central database dump |
| Secret handling | A provider exception carrying a secret leaves the request `uncertain`; the secret is absent from the result, Central and the store; replay does not redispatch |
| Scope integrity | A result citing a department outside the conversation is saved but never linked or shown, and the charge is kept (no silent refund) |
| Input validation | Blank/oversized question and malformed request keys give 422 with no effect (6 cases) |
| Identifiers | A malformed conversation id gives 422 with no effect |
| Quota/policy | Exhausted quota and disabled policy block new dispatch; replay of a completed request still works |

## Finding fixed in this batch

A malformed conversation identifier reached SQL and surfaced as `503 result_store_unavailable`,
masking the real cause. `QuestionLifecycleService.ask` now validates the UUID first and
returns 422 `invalid_conversation_id`. All other 4.4 cases passed against the 4.3 code unchanged.

## Actual results — 2026-11-19 (Docker runner, PostgreSQL 17.10)

| Suite | Passed | Failed / skipped |
| --- | ---: | --- |
| New security suite | 20 | 0 / 0 |
| Lifecycle (4.3) | 11 | 0 / 0 |

Earlier regression results from the 4.3 run (16 / 13 / 24 / 7+1 skipped) were not repeated
after this small change, which touches only `lifecycle.py` and the new tests.
Native PostgreSQL 18.6, hosted CI and the sanitized offline runner were not run.

## Known limits (not closed by 4.4)

- Access is checked before dispatch and again before delivery; it is not one atomic
  snapshot, so a revocation can race a request already being processed (documented in 4.2).
- A question refused by quota/policy after its intent was recorded keeps that intent row.
  This is by design for auditability but was not asserted by a test.
- Synchronous callbacks cannot be force-interrupted; providers must bound their own I/O.
- Admin-audited global history access, retention, backup/restore: 4.5 and later.
