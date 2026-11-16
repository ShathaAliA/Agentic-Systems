# C04 — external U1 results and central migration contract

Core-only, additive interface. No SQL migration, database, document/RAG module,
provider or HTTP route is added or changed. The existing `execute_question`
callbacks and authorization/accounting behavior remain available unchanged.

## Decisions from the PoC audit

| Candidate | Core decision |
|---|---|
| `usage002_result_payload` | Do not copy: it adds business response JSON to the central ledger, contrary to approved D01. |
| `usage003_merge` | Do not copy without a matching graph: it joins the old payload branch and RBAC; Core currently has one head, `rbac001`. |
| `app/usage/integration.py` | Do not copy: it merely re-exports existing processor functions. A real integration consumer must justify any future compatibility shim. |

Existing reviewed central migration source files are pinned by normalized
SHA-256 in `tests/fixtures/central_history_baseline.json`. Tests read Alembic
revision metadata with `ScriptDirectory`; they do not run `env.py`, resolve a
DSN, execute `alembic upgrade/stamp`, or create a database engine. They check a
connected single-head graph, unchanged historical source and the mapped
`UsageRequest` metadata allowlist without content/JSON payload columns. These
checks are not a live schema inspection or a substitute for migration tests on
the eventual integration branch. New legitimate additive migrations are allowed;
changing reviewed historical files requires explicit review, not fixture refresh
to hide a changed migration.

## Optional typed integration

`app/usage/external_results.py` supplies:

- `PreparedQuestionResult(outcome, payload)`: validated business processing returns
  `answered` or `no_answer`, with non-null payload. Partial/conflict answers map to
  accounting outcome `answered`; their business status remains in the payload.
- `StoredQuestionResult`: owner user ID, U1 request ID, server command fingerprint,
  accounting outcome and payload. Payload is excluded from dataclass repr.
- `ExternalResultStore.save_once(record)`: commit a durable immutable record
  **outside Central**, before returning. `(user_id, request_id)` must be unique;
  never overwrite a conflicting result. An implementation may accept an identical
  retry after verifying full identity/content equivalence, but Core never
  automatically retries a write with uncertain acknowledgement.
- `ExternalResultStore.load_authorized(user_id, request_id)`: revalidate owner and
  every source's current department, audience, publication and version access
  before returning. Return None if absent, or raise the sanitized 403 access error
  if revoked. Never return unfiltered content for Core to trim afterward.
- `execute_with_result_store(...)`: optional wrapper around existing U1. Calls
  processing once, persists before accounting closure, checks record metadata
  and outcome on delivery, then returns the payload. Incomplete requests never
  load a response. Original callbacks can continue to be used by existing clients.

Identity, context revision, fingerprint and cost quote are server-owned, never
HTTP user/billing claims. The typed wrapper does not prove physical durability,
source authorization, JSON schema validity or database immutability by itself.
Those are mandatory properties of the actual store/integration. Frozen
dataclasses are not an immutable database, and an opaque payload still needs the
agreed document answer schema. Synthetic memory stores are tests only.

## Failure and reconciliation contract

| Event | Required behavior |
|---|---|
| Save fails or its acknowledgement is lost | U1 becomes uncertain; keep the hold; no response/reprocessing/automatic refund. |
| Save commits but controlled accounting closure fails | Keep stored result and uncertain request; do not deliver until explicit reconciliation. |
| Abrupt exception/crash during closure | Request may remain running with a hold. Replay must not redispatch or deliver the saved response. |
| Completed request has no readable result | 503 `question_result_unavailable`; no regeneration or additional debit. |
| Owner/request/fingerprint/outcome mismatch | 503 `question_result_integrity_error`; no content delivered. |
| Access revoked after completion | Recheck and refuse delivery/replay; keep immutable accounting/history records. |

There is no distributed transaction or automatic reconciliation worker in C04.
Operators must compare stable request/provider receipt references and the real
store record before resolving a hold. Presence of a result alone does not prove
the provider cost, authorization or final accounting outcome. Do not edit the
ledger directly, blindly release holds, or rerun a provider to repair uncertainty.

## Handoff and dependencies

Core owns U1 and the approved separate Conversation Store service/schema when
that service is built. documents-ai owns document/source validation and its
integration adapter. Writing to the future Conversation Store must use its
agreed internal interface, not direct SQL from document modules. One store owns
the authoritative answer; conversation records reference it instead of creating
another mutable authoritative copy. This wrapper does not create that service
or silently choose PoC tenant `question_results` as the final storage location.

Before integration: agree the concrete storage adapter, owner/request uniqueness,
source-version schema, commit semantics, least-privilege connections, migrations,
and race/revocation tests. Use a fresh synthetic environment only. No shared
knowledge broker, document callback implementation or production claim is added.

If a separate legacy PoC database already applied `usage002`/`usage003`, do not
delete/rewrite those migrations, drop the column, copy content to Central, or
stamp another head. Inventory and back up that specific environment first;
resolve its graph and data-retention policy in a separate approved migration
plan. No such database was queried or modified here. Legal remains failed;
existing HR and Finance databases are not test targets.

## Verification

```powershell
python -m pytest -q Backend/tests/test_external_results.py Backend/tests/test_central_history_contract.py Backend/tests/test_question_adapter.py -p no:cacheprovider --tb=short
python Backend/tools/run-core-tests.py --suite unit
```

Tests use synthetic in-memory results and SQLite accounting only; no PostgreSQL,
Docker, file processing, embedding or provider requests. The final complete Core suite passed **361 tests**, with zero failures, errors or skips. Real-store persistence/isolation and RAG acceptance
remain pending; no test here authorizes activating Legal or modifying existing
database privileges.

The scoped pre-commit review found no blocking issue and no selected provider-key,
private-key, credential-bearing URL or session-token pattern in the implementation
and test additions. No actual .env, database backup or private state is committed.
This is not a full dependency audit. Docker/PostgreSQL/hosted CI were not run for
this contract-only batch. The existing Starlette/httpx warning remains.
