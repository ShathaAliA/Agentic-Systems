# C06 Gate 4.3 — verification (internal question lifecycle)

Scope as in [the plan](c06-gate4-3-plan.md), decision D1 approved (additive `c06_0004`).
Core only, feature/core-backend. No documents-ai change, no public route, no provider
or LLM call, no live HR/Finance/Legal/Central database. Legal remains `failed`.

## What was implemented

- `c06_0004`: insert-only `c06_question_intents` (owner/request-key unique, question,
  fingerprint and server-resolved context revision; RLS, no UPDATE/DELETE/TRUNCATE).
- Runtime store now requires `c06_0004` and checks the new table is not owned by the
  runtime role.
- `QuestionLifecycleService` (`app/conversation_store/lifecycle.py`): records the intent,
  runs `execute_with_result_store`, links the turn with `append_completed`. Replays reuse
  the stored context revision, so the fingerprint stays stable after the turn exists.
  A completed-but-unlinked request is linked on replay without calling `process` again.
  Orchestrator conversations, a missing/invalid quote provider and a payload not bound to
  the conversation are refused; the last leaves the request `uncertain` (no refund).

## Actual results — 2026-11-19

| Suite | Passed | Failed / skipped |
| --- | ---: | --- |
| New lifecycle PostgreSQL | 11 | 0 / 0 |
| Result store PostgreSQL (regression) | 16 | 0 / 0 |
| Conversations PostgreSQL (regression) | 13 | 0 / 0 |
| Authorization/binding PostgreSQL (regression) | 24 | 0 / 0 |
| Seal/restore PostgreSQL (regression) | 7 | 0 / 1 skipped |
| Offline Core, direct pytest | 409 | 1 failed, 150 skipped |

Limits of this evidence:

- PostgreSQL ran through the Docker runner on **PostgreSQL 17.10**, not 18.6 as in
  Gates 4.1/4.2. Native 18.6 was not available on this machine.
- The seal/restore skip is the native-only restore drill, which needs the native
  `pg_dump`/`pg_restore` guard. Its previous 8/8 result is therefore not repeated here.
- The offline run used `pytest tests` directly because the sanitized runner could not
  create its private report directory. The one failure
  (`test_default_collection_uses_safe_suite_from_either_directory`) is environmental:
  both `backend/` and `Backend/` exist on this case-insensitive checkout, so `conftest`
  registers twice. It is unrelated to this change, but it was not re-run on a clean checkout.
- Not run: hosted CI, PostgreSQL 18.6 native, upgrade of an existing `c06_0003`
  store with prior rows to `c06_0004`.

Test design note: the result store is module-scoped and immutable, so lifecycle tests count
rows per conversation, never globally.

## Environment notes

PowerShell 7.7.0 portable was placed in the ignored `.local/pwsh7` (official SHA-256
verified). The runner picked two `docker` files on Windows; pass
`-Docker <full path>\docker.exe`. Docker's address pools were exhausted by 21 leftover
`ai-auth-network-*` networks from earlier runs; these were removed. Recovery directories
under `.local/auth-tests` were kept.

## Still open after 4.3

Documents provider wiring and public intake (outside Core), upgrade-path test from
`c06_0003`, isolation sweep (4.4), restore and retention (4.5), CI and final review (4.6).
