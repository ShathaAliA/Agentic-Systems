# C06 question lifecycle — integration contract (Gate 4.6)

For whoever wires Documents/RAG, the HTTP layer or a coordinator to Core. Core is a library
boundary here: **no public route, provider client, tenant provider or LLM call is supplied**.
Legal remains `failed` and out of scope. Evidence: Gate 4.3/4.4/4.5 verification documents.

## 1. What Core provides

| Piece | Where | Purpose |
| --- | --- | --- |
| `QuestionLifecycleService.ask` | `app/conversation_store/lifecycle.py` | Record the question, admit and charge once, run the injected processor, seal the result, link the turn |
| `QuestionRecovery.report / relink` | `app/conversation_store/recovery.py` | Owner-scoped status report and an idempotent re-link; no dispatch |
| `ConversationResultStoreFactory`, source authorizer contract | Gate 4.2 | Per-delivery source/audience authorization, fail closed |
| Store migrations through `c06_0004` | `result_store_alembic/` | Immutable results, conversations, turns and question intents |

## 2. What the integrator must supply (all trusted server code)

1. **Store and service wiring.** `ConversationService(store, central_engine)` with a
   `PostgresResultStore` bound to the authenticated user (identity comes from `get_current_user`,
   never from a request field), then
   `QuestionLifecycleService(conversations, central_engine, quote_provider=...)`.
2. **`quote_provider(command) -> int`**: a conservative positive micro-USD estimate covering every
   paid step. `None`, zero, negative or non-integer disables questions (503).
3. **`authorize_content(user_id, command) -> bool`**: return the literal `True` only after current
   content/ownership checks. Anything else is 403.
4. **`process(context, command) -> PreparedQuestionResult`**:
   - Call paid providers only through `context.perform(...)` with the stable idempotency key, and
     return the answer in the version-1 `Answer` DTO as the payload.
   - `payload["conversation_id"]` must equal the conversation and `question_request_id` must equal
     `context.request_id`; otherwise nothing is saved and the request becomes `uncertain`.
   - Every cited source's department must be inside the conversation's scope.
   - Do not persist a second copy of the answer, write to Core tables, or catch and hide failures.
5. **`SourceAuthorizerProvider.authorizer_for(user_id, conversation_id, department_ids)`** from
   Gate 4.2, owned by Documents: current document audience, approved current version and exact
   citation data. Core does not substitute `documents:read` for this decision.

## 3. Call contract

`ask(conversation_id, request_key, question, *, authorize_content, process) -> dict`, synchronous:
call it off the async event loop. Only department-mode conversations are accepted.

| Result `status` | Meaning | Content returned |
| --- | --- | --- |
| `answered` / `no_answer` | Completed, charged once, turn linked | `response`, `turn` |
| `processing` | Same key still reserved/running | none |
| `uncertain` | Outcome unknown; holds kept | none |
| `refunded` | Confirmed technical failure | none |

Retry rule: resend the **same** `request_key` and **same** question. A replay never dispatches or
charges again and reuses the stored context revision. A different question under a used key is 409.

| Error | Suggested HTTP | Cause |
| --- | --- | --- |
| 422 | 422 | Invalid command, malformed conversation id, bad callbacks |
| 403 | 403 | Inactive user, lost scope, orchestrator mode, source access revoked |
| 429 | 429 | Question limit, rate limit, company budget reached, or processing disabled by policy |
| 404 | 404 | Unknown or another owner's conversation (same answer for both) |
| 409 | 409 | Request key reused with a different question; result does not match the conversation |
| 503 | 503 | Quote/provider/store unavailable or misconfigured, or usage policy not configured (sanitized text only) |

## 4. Runtime configuration the store enforces

The runtime login role must hold exactly `SELECT, INSERT` on `c06_results`, `c06_conversations`,
`c06_turns` and `c06_question_intents`, and `SELECT` on `c06_result_sources`; it must not own the
tables, have DDL, or be a privileged role. The store refuses to run below revision `c06_0004`,
with a different database identity, or with unsafe grants. Run migrations with the limited
`c06_store_owner`, never with an elevated identity, and take a backup first (see the runbook).

## 5. Boundaries and known limits

- Access is checked before dispatch and again before delivery; it is not one atomic snapshot, so
  a revocation can race a request already being processed.
- A refused question (quota, policy) can leave a recorded intent; no charge and no dispatch occur.
- Callbacks are synchronous and cannot be force-interrupted; providers must bound their own I/O.
- A global Admin view of other owners' history, retention/deletion, encryption at rest and an external
  audit store are not provided and need separate decisions.
- Multi-department orchestration, human handoff, public intake, and the Documents provider
  implementation are outside Core and still require joint integration testing.
