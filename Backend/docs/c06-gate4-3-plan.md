# C06 Gate 4.3 — internal question lifecycle (PLAN, NOT IMPLEMENTED)

Status: draft for review. No code, migration, grant or CI change has been made.
Branch: feature/core-backend only. Baseline: Gate 4.2 (47a4a50).

## 1. Goal

Add one trusted, **internal** (no public HTTP route) service that runs a question
end to end inside an owned Core conversation:

    validate -> reserve credit -> process -> seal result -> link turn -> deliver

with exactly-once observable behavior for the same owner/request key/fingerprint:
no duplicate debit, no provider redispatch, no duplicate turn, and no successful
answer after an uncertain save/finish.

## 2. What already exists (verified by reading the code)

| Piece | Where | Reused as-is |
| --- | --- | --- |
| Command + fingerprint | `usage/question_adapter.py` `QuestionCommand` | yes |
| Reserve / start / finish / uncertain / cancel_before_start | `usage/processor.py` `run_once` | yes |
| Typed result boundary (save_once, load_authorized) | `usage/external_results.py` `execute_with_result_store` | yes |
| Immutable conversations and turns, advisory-locked sequence | `conversation_store/conversations.py` | yes |
| `append_completed` (idempotent link, scope/owner/completion checks) | same | yes |
| Source authorizer provider (fail closed, no cache) | `conversation_store/authorization.py` | yes |

## 3. Gaps 4.3 must close

1. **No orchestration.** `execute_with_result_store` and `ConversationService.append_completed`
   are separate. Nothing calls them in order for one conversation.
2. **Crash window between finish and link.** After `finish()` commits, a crash before
   `append_completed` leaves a completed result with no turn. Replay must link it
   idempotently without calling `process` again.
3. **Question is not durable while running.** The question text enters `c06_turns`
   only after completion. A running/uncertain request is invisible in the conversation
   history, which weakens the audit trail.
4. **Conversation binding.** `append_completed` requires `payload["conversation_id"]`
   to equal the conversation. Nothing yet guarantees the processor writes it.
5. **Context revision.** `QuestionCommand.context_revision` must be server-resolved from
   the conversation (for example last turn sequence). No such resolver exists.
6. **Quote estimate.** `estimated_micro_usd` has no server-side calculator.

## 4. Decisions needed before coding

| # | Decision | Recommendation |
| --- | --- | --- |
| D1 | Persist the question before processing? | **Yes**: additive migration `c06_0004` with an insert-only `c06_question_intents` (conversation, owner, request key, fingerprint, question). Runtime gets INSERT/SELECT only. Without it, running/uncertain questions are not auditable. Alternative: no migration; rely on client resend with the same key (weaker audit). |
| D2 | Context revision rule | `"<conversation_id>:<last_sequence>"`, resolved under the conversation advisory lock. |
| D3 | Quote estimate | Fixed per-department constant from Admin settings for now; real token-based estimate arrives with the provider integration. |
| D4 | Mode support | Department-mode conversations only. Orchestrator mode stays refused (as in 4.2). |

D1 needs your explicit approval because it adds a migration and a grant.

## 5. Work breakdown (small local commits)

### 4.3.1 Contract and resolvers (no DB change)
- `QuestionLifecycleService` in `app/conversation_store/lifecycle.py`.
- Server-side `context_revision` resolver and quote provider (injected, defaults unavailable).
- Fixed error table (see section 7).

### 4.3.2 Intent record (only if D1 = yes)
- Migration `c06_0004`: `c06_question_intents`, unique `(owner_id, request_key)`,
  no UPDATE/DELETE grants, no content in Central.
- Fingerprint conflict on the same key returns 409; same fingerprint returns replay.

### 4.3.3 Orchestration
Order inside one call:
1. Verify owner, active user, conversation scope, department mode.
2. Write the intent (or replay it).
3. `execute_with_result_store(...)`; the injected `process` MUST place
   `conversation_id` in the payload (checked before save).
4. If the receipt is `completed` and outcome is `answered` or `no_answer`, call
   `append_completed` (idempotent).
5. Return a status object: `answered`, `no_answer`, `processing`, `uncertain`,
   `refunded`, or `source_access_revoked`. Never a success for running/uncertain.

### 4.3.4 Replay and recovery
- Same key, same fingerprint, completed but unlinked: link now, no `process` call.
- Same key, running: return `processing`, no second dispatch.
- Same key, uncertain: return `uncertain`; no refund, no redispatch, no auto-reconcile.
- Same key, different command: 409, nothing changes.

### 4.3.5 Accounting rules (already in U1, now covered end to end)
- One unit per processed message, including `no_answer`.
- Technical failure with confirmed receipts refunds once; ambiguous failure keeps holds.
- Concurrent windows cannot exceed balance (reservation is atomic).

### 4.3.6 Tests and evidence
Offline (SQLite Central fixtures): validation, scope, inactive user, mode refusal.
Native PostgreSQL 18.6 on a fresh private cluster (pre-backup, random loopback port,
confirmed shutdown), registered in the aggregate runner and both exact allowlists:

- duplicate request, same and changed fingerprint
- concurrent identical requests (single dispatch, single debit, single turn)
- crash after save before finish; crash after finish before link; link retried
- save acknowledgement lost; finish failure controlled and abrupt
- `no_answer` counts one unit
- revoked source or department during processing and on replay
- foreign conversation and owner isolation, Admin gets no cross-owner access
- no question/answer content appears in Central
- orchestrator conversation refused

## 6. Acceptance criteria

- All existing suites stay green (baseline 423 offline, 61 PostgreSQL).
- Every new case above passes with zero skips; failures are not counted as passing.
- Exactly one debit, one dispatch and one turn per request key under replay and concurrency.
- No successful answer is returned while a request is running or uncertain.
- Historical migrations untouched (c06_0004 additive only, if approved); documents-ai,
  HR, Finance, Legal and live containers untouched; Legal stays `failed`.
- A new document `c06-gate4-3-verification.md` records commands, counts and limits.

## 7. Error table (proposed)

| Condition | HTTP-style result |
| --- | --- |
| Invalid command | 422 |
| Unknown or foreign conversation | 404 `conversation_not_found` |
| Inactive user, revoked scope, orchestrator mode | 403 |
| Same key, different fingerprint | 409 |
| Running | status `processing`, no content |
| Uncertain / refunded | status `uncertain` / `refunded`, no content |
| Source revoked or provider missing | 403 / 503, no content |

## 8. Out of scope for 4.3

Public HTTP intake route, real Documents provider and RAG/LLM call, multi-department
orchestration, human handoff, automatic reconciliation or refunds, restore and
retention procedures (4.5), full isolation sweep (4.4), CI and integration-contract
documentation (4.6), any push or merge.

## 9. Risks

- Migration `c06_0004` interacts with the sealing grants from 4.1; it must be verified on
  a fresh cluster and on an upgrade from `c06_0003` with prior rows.
- The post-provider recheck of RBAC is not an atomic snapshot (known limit from 4.2).
- Synchronous callbacks cannot be force-interrupted; providers must bound their own IO.
