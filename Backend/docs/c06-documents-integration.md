# Documents ↔ Core question integration (single result authority)

Branch `integrate/core-backend-into-documents-ai`. Implements the plan agreed after merging
`feature/core-backend` into `documents-ai`. Legal remains `failed`; no real provider, company data
or live database was used.

## Result of the integration

There is **one** question-result authority: Core's isolated C06 store. Documents' tenant
`question_results` table is frozen history (retained, never copied or deleted, no code writes it).

| Piece | Where | Role |
| --- | --- | --- |
| `DocumentSourceAuthorizerProvider` | `app/documents/source_provider.py` | Core's source-authorizer provider; wraps the existing single-tenant `DocumentSourceAuthorizer`. A conversation spanning several departments is refused (503) |
| Composition root | `app/integration.py`, `create_app(result_store_factory=...)` | One `ConversationResultStoreFactory` serves Core's conversation routes (raw factory + provider) and Documents' `/ask` (three-argument shim) |
| `/ask` | `app/documents/router.py` | Runs through `QuestionLifecycleService`: durable intent, charge once, seal the result, link the turn, recovery by `QuestionRecovery` |
| Legacy store | `app/documents/result_store.py`, tenant `question_results` | Disabled (503) and frozen; documented as non-authoritative |

## Configuration

`C06_RESULT_STORE_URL_FILE` names a private JSON file `{"url": "postgresql+psycopg://..."}` for the
restricted C06 runtime role (not owner, no DDL, `SELECT, INSERT` only). Without it the factory is `None`
and `/ask` and the conversation routes fail closed. There is no environment fallback. A symlinked file or a
URL that is not PostgreSQL with a username and database is refused.

## Behaviour of `/ask` (unchanged response shape)

Answered / no-answer: the answer payload (as before). `409 question_processing_or_uncertain` while
running or uncertain; `503 question_technical_failure_refunded` after a confirmed technical failure;
`403` for lost scope, orchestrator or foreign conversations (foreign: 404) and revoked sources;
`503` when the store or source provider is not configured. Turns are now linked, so Core's
conversation history shows answered questions.

## Verification (2026-11-19)

Offline: route behaviour through a seam (`router._question_lifecycle`) and composition/legacy checks in
`tests/test_documents_core_integration.py`. PostgreSQL: `tests/test_joint_ask_postgres.py` runs the real
`/ask`, real lifecycle, Documents' provider and a real C06 store on a fresh cluster (tenant side is a synthetic
SQLite document store with a stubbed retriever and LLM). It covers: charge once and replay, turn linkage and
history, audience revocation and archived documents, changed question conflicts, orchestrator/foreign/missing
conversations, provider failure leaving `uncertain` without redispatch, missing provider, unconfigured
integration, and the legacy table staying empty.

## Not covered

Real tenant PostgreSQL/pgvector combined with the C06 store in one run; embedding quality and answer accuracy
(no real model); physical source locations (still `reviewed_text` chunk citations, a Documents debt);
Microsoft Entra and company data; a public conversation/message HTTP route; global Admin history access;
retention, encryption and external audit. Access is not an atomic snapshot (revocation can race a request
being processed).
