# C06 — Conversation / Result Store: design proposal (no implementation)

**Status:** DESIGN ONLY / approval pending. **Branch:** `feature/core-backend`.
**Precondition:** C05 Core-side verification closed; see
[c05-closure.md](c05-closure.md). This document adds no runtime code,
migrations, API routes, storage, credentials, grants, or database operations.

## 1. Goal and scope

Provide a **durable, authoritative answer/result store outside the
Central Usage Ledger**, with conversation metadata referencing those
results. Preserve C04's existing `ExternalResultStore` contract and
U1's reserve/process/settle lifecycle. Enforce user ownership, department
authorization, document publication/audience/version checks **on every
delivery**, including cached replay.

**In scope for the eventual C06 implementation (not this design commit):**
result persistence and immutability, minimal conversation references,
authorized reads, idempotency, integrity, transaction ordering, failure
and restart tests, retention/security policy, and isolated test setup.

**Out of scope:** implementing document upload/OCR/RAG/LLM, choosing
providers, modifying `documents-ai`, moving content into Central,
rewriting applied migrations, production activation, Legal recovery,
HR/Finance database changes, merging branches, and automatic settlement
of uncertain provider charges.

## 2. Architecture and authority

Proposed service boundary:

```text
Authenticated request
    -> Core identity + active department permissions
    -> trusted QuestionCommand + server context_revision/fingerprint
    -> U1 Central ledger: reserve -> running
    -> authorized document/RAG processor (future integration, separate owner)
    -> C06 Result Store: save_once(immutable result + source references)
    -> U1 Central ledger: finish answered/no_answer
    -> fresh Core + document audience/version authorization
    -> C06 Result Store: load_authorized -> response

Conversation metadata -> references C06 authoritative result IDs
Central ledger -> accounting state and opaque request IDs only
```

**Proposed default:** a new, Core-owned **Conversation Store service
with a separate dedicated database/schema**, not the existing Central
database and not a direct write into an existing HR/Finance/Legal
tenant. This is a *design preference*, not a selected production
database or permission to provision one. The alternative of a
per-tenant physical store must be compared before approval, especially
for multi-department questions and strict tenant isolation.

There must be **one authoritative answer copy**. The partner branch's
tenant `question_results` table is a pre-existing candidate, not an
approved second source of truth. Before implementation, decide whether
it will be replaced, adapted as the single authority, or treated as
non-authoritative transient data. No dual writes to competing result
tables.

Core owns the result-store interface, conversation ownership and
accounting. The document team owns source resolution, published-version
and audience checks, and trusted retrieval/provider adapters. No
document module may write directly to C06 SQL; it uses a reviewed
internal service interface.

## 3. Proposed logical schema (not DDL)

| Entity | Essential fields | Constraints and policy |
| --- | --- | --- |
| `conversations` | `conversation_id`, `owner_user_id`, `created_at`, `updated_at`, `state` | Owner immutable; no unrestricted cross-user listing; avoid raw content by default |
| `conversation_turns` | `turn_id`, `conversation_id`, `sequence`, `usage_request_id`, `result_id`, `created_at` | Unique `(conversation_id, sequence)`; one authoritative result reference; no duplicated answer |
| `question_results` | `result_id`, `owner_user_id`, `usage_request_id`, `fingerprint`, `accounting_outcome`, `business_status`, `answer_payload`, `source_scope_revision`, `created_at`, `schema_version` | Unique `(owner_user_id, usage_request_id)`; immutable after commit; outcome only `answered`/`no_answer`; business status may be partial/conflict |
| `result_sources` | `result_id`, `department_id`, `document_id`, `version_id`, `location`, `source_revision` | Pinned source references; every source checked on read; no unvalidated client-supplied IDs |

All identifiers, types, indexes, partitioning, FK feasibility across
physical databases, JSON schema version, encryption and retention
periods are **pending design review**. `usage_request_id` is an
opaque reference to Central, **not** a cross-database foreign key.
The stored record must preserve the C04
`StoredQuestionResult(user_id, request_id, fingerprint, outcome,
payload)` invariant.

**Question text/history:** not stored by default in the first proposed
result-only slice. A conversation UI requiring original question text
must first approve a separate sensitive-content policy (purpose,
consent/notice, encryption, access, retention, deletion and audit).
Do not silently copy the question from U1 or store prompts/embeddings
in the Central ledger.

**Multi-department answers:** bind the immutable result to the exact
authorized department scope and every cited source. Replay is
fail-closed if *any* required scope/source becomes unauthorized.
A future product policy may explicitly support redacted partial
delivery, but never by silently trimming an immutable authoritative
answer.

## 4. Existing contracts and API design

Existing internal C04 contract is authoritative for this design:
- `ExternalResultStore.save_once(StoredQuestionResult)` must durably
  commit before returning; conflicting same-key writes reject, never
  overwrite. Identical retry is allowed only after proving exact
  equivalence; no blind retry on uncertain acknowledgment.
- `ExternalResultStore.load_authorized(user_id, request_id)` must
  validate owner, active identity, department membership/role,
  document publication, audience and pinned source versions for **every**
  response/replay. Missing -> `None`; revoked -> sanitized 403.
- `execute_with_result_store` validates
  owner/request/fingerprint/outcome and checks the stored outcome
  against U1's completed receipt before returning payload.
- Only `answered` and `no_answer` are U1 accounting outcomes.
  Business `partial` and `conflict` are `answered` plus the
  approved strict answer DTO; `no_answer` cannot cite evidence.

**Proposed external HTTP operations, not approved routes:**
`POST /conversations`, `GET /conversations`,
`GET /conversations/{id}`,
`GET /conversations/{id}/turns`.
The question submission route remains the document team's contract;
it must not trust client-supplied `user_id`, `fingerprint`,
`estimated_micro_usd`, `provider_receipt`, or
`context_revision`. A read endpoint must recheck authorization and
return only safe DTOs with `Cache-Control: no-store`; pagination
and cursor leakage controls are required. Do not register any route
until a new access-inventory policy is approved.

## 5. Transaction and failure ordering

1. Validate active authenticated identity and department access;
   resolve `context_revision`, source scope and cost estimate on the
   server. Use `QuestionCommand.fingerprint()` unchanged.
2. Reserve/start U1 in Central; do not hold a DB transaction across
   provider calls.
3. Run trusted retrieval/generation; provider attempts use stable
   keys and verified receipts under U1. No automatic redispatch on
   ambiguous provider outcome.
4. `save_once` commits the authoritative result and pinned source
   references **atomically in the separate store**. Enforce uniqueness
   and immutability in the DB, not just frozen Python dataclasses.
5. Only after durable acknowledgment, finish U1 as
   `answered`/`no_answer`.
6. Recheck current authorization, load and verify the result, and
   deliver it only if U1 is `completed` with a matching outcome.
7. Conversation turn creation is idempotent and references the
   authoritative result. A failure to link a turn must not create a
   second result or trigger a new provider call; exact UI behavior
   and recovery procedure require approval.

| Scenario | Required response | No automatic action |
| --- | --- | --- |
| Concurrent same owner/request writes | One immutable winner; identical retry only with verified equality; conflict rejected | No overwrite |
| Save fails / acknowledgment lost | U1 uncertain, hold preserved; no answer delivered | No provider rerun/refund |
| Save succeeds, U1 finish fails | Result remains stored, request uncertain/running; no delivery | No fabricated completion |
| U1 completed, result missing/corrupt | Sanitized 503; integrity incident | No regeneration/extra charge |
| Permission revoked or source unpublished | Sanitized 403; do not reveal old answer | No partial leak |
| Request replay with different fingerprint | Reject as idempotency conflict | No second debit |
| Crash/restart | Deterministic lookup by owner/request; honor U1 state | No implicit reconciliation |
| Legal failed/tenant unavailable | Deny before tenant access | No activation/provisioning |

**No distributed transaction** is assumed between Central and C06.
An operator-reviewed reconciliation workflow is a separate future
design, requiring authoritative provider receipts, store evidence,
auditing and explicit approval. Do not repair by editing ledger rows
or blindly refunding.

## 6. Isolation, security and retention

- A new dedicated store must have its **own restricted credentials**;
  no superuser/owner credentials in application runtime. Separate
  schema/role for migration administration, unavailable to request
  handling. No live HR/Finance/Legal DSNs in C06 tests.
- Enforce owner + department/source scope in service and preferably
  database row-level security or equivalent proven physical
  isolation. Cross-department queries need an explicit access model;
  RLS alone cannot validate document publication in another service.
- Encrypt sensitive payloads at rest and in transit; keep keys in
  managed secret storage, not repo, logs or Central. Review key
  rotation, backup encryption, restore and deletion semantics.
- No raw answers, prompts, source quotes, tokens, embeddings or
  credentials in audit logs. Log request/result IDs, event status,
  safe error codes and authorization decisions only.
- Retention/deletion policy is unresolved: legal hold, user deletion,
  account deactivation, backup expiry and immutable accounting
  retention must be separated. Revocation must immediately block
  delivery even if the result remains in protected storage.
- C02 no-store, sanitized errors, least privilege and current
  authentication/session checks remain mandatory.

## 7. Design alternatives and decisions pending

| Decision | Preferred option | Must be approved by |
| --- | --- | --- |
| Physical store | New Core-owned dedicated store, separate from Central and existing tenants | Core + document owner + database/security reviewer |
| Tenant isolation | Department-scope enforcement plus DB isolation/RLS; compare per-tenant physical DB | Security/DB reviewer |
| Existing partner `question_results` | Resolve single authority before migration or adapter | Core + documents-ai owner |
| Conversation history | Metadata + result refs first; raw question text opt-in only | Product/privacy reviewer |
| Answer/source schema | Strict versioned payload and pinned refs; fail closed on any revoked source | Document owner + Core |
| Migration graphs | Reconcile `rbac001` and `usage003_merge` in a separate approved plan | DB/migration owner |
| Provider settlement/reconciliation | U1 receipts authoritative; manual review of uncertain states | Core accounting owner |
| Retention, backup, encryption, deletion | Define explicit policies before persistent content | Security/privacy/operations |

## 8. Implementation plan (future, not authorized here)

**Gate 0 — approve design:** resolve all ownership/schema/security
decisions above; confirm source/audience/version and multi-department
semantics, privacy policy and tenant contract. Do not treat C05 green
CI as approval of these decisions.

**Gate 1 — synthetic contract tests:** fresh isolated DB, negative
authorization and replay cases, immutable conflicts, crash after
commit, concurrent writers, missing/corrupt result, source revocation,
failed Legal, and proof no content in Central.

**Gate 2 — minimal result persistence:** add a dedicated Core store
interface adapter and additive store-only migrations in a new test
database; keep existing U1 and historical migration files untouched.

**Gate 3 — conversation metadata:** reference the single result
authority; add idempotent turns and owner-scoped paginated reads
without raw history by default.

**Gate 4 — integration and security:** joint source/audience
authorization adapter, provider receipt tests, row isolation,
restricted runtime role, backup/restore and retention tests in fresh
synthetic environments only.

**Gate 5 — release review:** green Core CI, negative-path evidence,
explicit production approvals and an independent migration/tenant
activation plan. No automatic merge or Legal activation.

### Acceptance criteria for any future implementation

- One authoritative durable result; no content in Central.
- Exactly-once observable answer for the same owner/request/fingerprint;
  no duplicate debit/provider dispatch on replay.
- Deny unauthorized/revoked owner, department or source on *every*
  delivery, including historical conversations.
- No successful answer after uncertain save/finish; no silent
  refund/reconciliation.
- Restart/concurrency/immutable-conflict and schema validation tests
  pass against a newly created isolated database.
- Existing Core CI remains green; HR/Finance untouched; Legal remains
  failed unless separately authorized and verified.
- No document branch changes, no migration-history rewriting, no merge.

**Current C06 state: proposal drafted; implementation NOT started.**
