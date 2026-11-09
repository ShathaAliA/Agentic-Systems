# Internal U1 question adapter

`app/usage/question_adapter.py` is a synchronous Core integration boundary, not
an HTTP route, identity provider, RAG implementation or result store. It uses the
existing accounting engine and RBAC. No migrations, new grants, credentials or
environment settings are needed. Legal remains failed; PoC is deferred.

## Caller contract

1. Resolve `authenticated_user_id` from `get_current_user`. Never map an unverified
   request field/header to it. Use the guarded central application engine, not a
   provisioning connection. Call this synchronous adapter in a worker/threadpool
   from async HTTP code; supply synchronous callbacks using their own short sessions.
2. Validate `QuestionCommand`: a required request key, exact question, unique tuple
   of selected department IDs and an immutable **server-resolved** context revision.
   Convert the HTTP department list to a tuple deliberately; the internal model is
   strict. Context includes every conversation/history/configuration input that can
   affect processing; resolving that snapshot belongs to the caller. Never use a
   constant revision for changing history. Stable delivery retries retain it.
3. Compute a conservative `estimated_micro_usd` on the server, covering all paid
   steps. Clients cannot choose quotes, provider receipts or accounting outcomes.
   Retries must preserve the original envelope; a changed envelope conflicts safely.
4. Supply all three callbacks below. They are trusted application code, never client
   input. Core checks fresh active user and `documents:read` for **each selected
   department** before admission and before loading a completed response. Global
   permissions retain their existing scope; failed/archived departments are denied.
   This boundary is for explicit authorized departments. Automatic routing must
   first resolve authorized targets; it must not query every tenant to discover them.

| Callback | Required behavior |
| --- | --- |
| `authorize_content(user_id, command) -> bool` | Return literal `True` only after current context/conversation ownership and applicable content checks pass. No permissive default. Runs before admission/replay and before completed-response delivery. |
| `process(accounting_context) -> str` | Retrieve only authorized content; authorize each paid attempt before dispatch through `AccountingContext`; settle final server-verified receipts. Persist the response in the owning authorized conversation/result store keyed by `(user_id, context.request_id)` **before** returning `answered` or `no_answer`. |
| `load_response(user_id, request_id) -> object or None` | Resolve the original stored result for that owner, recheck every source's current audience/status, and return only a safe authorized response. Deny if access was revoked; return `None` when no result is available. Never blindly return a cached answer. |

Partial/conflict answer payloads remain in the external response. Their successful
accounting outcome is `answered`; absence of evidence is `no_answer`. A model/client
claim is not authority to refund. Use `ConfirmedTechnicalFailure` only after final
authoritative receipts, including verified zero-cost attempts where applicable.
Unsettled/ambiguous exceptions leave accounting uncertain with holds retained.

No question, answer or source text is stored in U1. Only the SHA-256 fingerprint
(exact question, sorted departments, context revision, adapter version) is persisted
alongside existing metadata. The fingerprint is not encryption or anonymization;
accounting DB access remains restricted. No cross-tenant result store or new central
content table is introduced. A production result-store design remains subject to
joint review before connecting this adapter to documents-ai.

## Results and failures

`QuestionResult.receipt` contains request ID, state, outcome and replay flag; it
never includes execution claims or provider credential references. Completed
answered/no_answer work loads the external response. Running/reserved/uncertain/
refunded work returns `response=None`; the route must map these states explicitly,
not report a successful answer. Replays never invoke `process` again.

| Condition | Behavior |
| --- | --- |
| Missing/invalid command fields | Pydantic validation failure; HTTP caller maps to 422 |
| Unknown/inactive user | Existing U1 404/403; no response delivery |
| Department/content denial | 403; no new admission on initial denial |
| Same key, changed question/departments/context/quote | 409 `request_key_payload_conflict` |
| Unconfigured policy | 503; no processing |
| Credit/rate/budget exhausted | Existing U1 429; no processing |
| Completed result missing | 503 `question_result_unavailable`; no redispatch/refund |
| Confirmed technical failure | Existing verified refund workflow; no result load |
| Ambiguous processing failure | Uncertain receipt; no result load or automatic retry |

Callback exceptions at authorization/loading propagate to the caller. The eventual
HTTP adapter must sanitize errors, use private/no-store responses, and never expose
raw provider/DB exceptions. Lost result storage cannot be repaired by reusing the
same key to rerun the provider. A crash between external result persistence and
accounting completion requires reconciliation; there is no distributed transaction
or automatic takeover. Authorization can change during generation: the processor
must enforce source access itself and the loader must recheck at delivery time.

## Tenant contract fixtures

`tests/fixtures/core_tenant_contract_cases.json` gives machine-readable accepted
and rejected **synthetic** V1 manifests. `contract_records` is a test table, not a
document model or a recommended document grant policy. Loader acceptance proves
only structural validity: pinned revision existence/ancestry, actual migrations,
connectivity, runtime grants and isolation still require fresh-cluster verification.
The fixture must never replace the document developer's real manifest.

The document developer owns actual schema/revisions, explicit runtime grant maps,
content authorization and result persistence. Core owns accounting and strict
manifest validation. Both sides must agree on callbacks and state/error mapping.
Do not switch any real manifest to ready or activate Legal based on unit tests.

## Verification

From repository root, using the existing local test environment:

```powershell
.local/rbac-venv/Scripts/python.exe Backend/tools/run-core-tests.py --suite unit
pwsh -NoProfile -File Backend/tools/test-auth-postgres-native.ps1 -TestFile tests/test_usage_postgres.py
```

The second command creates a fresh cluster, captures recoverable pre-change backups,
uses the restricted test API identity, and stops/retains only that new cluster. It
does not target the existing central, HR, Finance or Legal databases. The existing
Docker alternative is `Backend/tools/test-auth-postgres.ps1` with the same TestFile.
No real provider requests or document processing are part of these tests.
