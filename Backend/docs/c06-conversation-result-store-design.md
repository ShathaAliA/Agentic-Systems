# C06 — Conversation / Result Store: approved baseline and Gate 3 progress

**Status:** Gates 1/2 verified; Gate 3 approved and implemented locally for
isolated verification. No real document delivery, provider or Gate 4 integration.
D01/D02/D03 were already approved by the user on 2026-11-15.
**Branch:** `feature/core-backend`.
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

**Approved D01:** a Core-owned Conversation Store service with a **separate
PostgreSQL database on the same server**, its own restricted connection and
separate migration identity. A schema inside Central or an existing tenant is
not the approved alternative. Human department conversations stay in their
owning tenant and start with the employee-approved summary only. Architecture
approval does not authorize database creation, grants or Gate 2 implementation.

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

**Question text/history:** retained full history is already a product requirement.
Sent messages cannot be edited or deleted by any application role, including
Admin. Gate 2 is a temporary result-only slice; question text and history are
deferred to Gate 3, not removed or made subject to a new product opt-in.
Operational encryption/access/backup controls still need implementation. Do not
copy prompts or embeddings into Central or add a message deletion/expiry API.

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
- Retained immutable sent messages and no application edit/delete are approved.
  Account deactivation/revocation blocks delivery, not audit retention. Backup
  lifetimes, keys and recovery need technical policies without silently enabling
  chat deletion. Any exceptional retention change needs separate approval.
- C02 no-store, sanitized errors, least privilege and current
  authentication/session checks remain mandatory.

## 7. Design alternatives and decisions pending

| Decision | Preferred option | Must be approved by |
| --- | --- | --- |
| Physical store | Approved D01: separate Core-owned database on the same server, not a Central schema | User approved; implementation scope pending |
| Tenant isolation | Owner/scope enforcement plus restricted PostgreSQL roles and proven isolation within D01 | Concrete controls to test |
| Existing partner `question_results` | Resolve single authority before migration or adapter | Core + documents-ai owner |
| Conversation history | Full retained immutable history required; result-only Gate 2, history in Gate 3 | Already approved product scope |
| Answer/source schema | Reuse approved RC2 DTOs; finalize strict storage mapping and source adapter | Document owner + Core integration review |
| Migration graphs | Reconcile `rbac001` and `usage003_merge` in a separate approved plan | DB/migration owner |
| Provider settlement/reconciliation | U1 receipts authoritative; manual review of uncertain states | Core accounting owner |
| Backup/encryption operations | Preserve no-edit/no-delete behavior; define keys, recovery and backup controls before real data | Concrete operational review |

## 8. Gate progression

**Gate 0 — approved baseline:** preserve D01/D02/D03 and immutable-history
requirements. Resolve remaining concrete contracts without reopening the physical
store selection. CI success does not authorize implementation or infrastructure.

**Gate 1 — synthetic contract tests:** fresh isolated DB, negative
authorization and replay cases, immutable conflicts, crash after
commit, concurrent writers, missing/corrupt result, source revocation,
failed Legal, and proof no content in Central.
Gate 1 is now implemented as a test-only SQLite harness and verified by hosted
CI at `f5f7c8888a1a371efa117a2a4747138ba72cab2d`: 387 unit tests and 64 fresh
PostgreSQL tests passed. The latter verify Core, not a C06 PostgreSQL store.

**Gate 2 — minimal result persistence:** add a dedicated Core store
interface adapter and additive store-only migrations in a new test
database; keep existing U1 and historical migration files untouched.

**Gate 3 — conversation metadata:** reference the single result
authority; add idempotent immutable question turns and owner-scoped paginated
reads. User-approved Gate 3 exposes four create/read routes, with no message
submission/edit/delete or Admin cross-user bypass. Real source verification is
still required before content delivery; absent/revoked content is masked.

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

## 9. Approved Gate 2 — minimal isolated PostgreSQL result adapter

The user explicitly approved this scope after reviewing this plan. That approval
does not extend to Gate 3 or live delivery. Existing HR/Finance/Legal/Central databases,
containers, credentials and volumes are not test targets.

1. **Freeze the slice contract:** D01's separate database; opaque Central request
   references, no cross-database FKs. A versioned envelope follows approved RC2
   answer/source DTOs: owner, request, fingerprint, accounting outcome, business
   status and pinned source versions/locations. One U1 request has one owner.
2. **Store-only migrations and adapter:** proposed immutable `question_results`
   and `result_sources`, with atomic result/source commit and request uniqueness.
   Keep all historical Central/tenant migrations unchanged. A separate migration
   identity owns schema work; runtime has only necessary INSERT/SELECT access,
   no UPDATE/DELETE/DDL/ownership or privileged role flags. Prove restrictions
   with real SQL denial tests, not Python dataclasses alone.
3. **Fail-closed reads:** a mandatory trusted source authorizer checks every
   delivery/replay; no allow-all default. C04 validates result identity/outcome
   against U1. Tests inject synthetic decisions; application delivery remains
   disabled without the real document adapter. No public routes, Admin bypass
   or weakening of the department gate/shared-knowledge boundary.
4. **Fresh synthetic infrastructure:** uniquely owned cluster/database, private
   state, fresh credentials, backup before migrations/grants, owners/ACL evidence,
   confirmed shutdown and retained recovery artifacts under C03 guards. Real
   data remains excluded pending encryption, key/backup/restore controls and
   company processing approval. No fallback to current department resources.
5. **Acceptance tests:** fresh migrations/replay, reopen/restart, concurrent
   duplicate writes, malformed/corrupt records, wrong owner/scope, revoked source,
   no_answer, lost save acknowledgement, save-before-U1-finish, controlled and
   abrupt finish failures. No redispatch/refund/delivery while running/uncertain.
   Verify append-only rows, privilege isolation, secret-free logs and no content
   in Central. Run full Core plus the new isolated PostgreSQL store suite.
6. **Report and stop:** prepare small local reviewable commits and report actual
   counts/limitations. No push, merge, provider call or Gate 3 implementation.

**One authority:** Gate 2 creates a candidate Core-owned store for isolated
validation, not live document writes. Before joint delivery, agree how partner
tenant `question_results` becomes a legacy/non-authoritative reference or follows
a separately reviewed migration. Do not dual-write answers or copy/delete partner
data; documents-ai remains untouched. Source validation and business DTO mapping
must be jointly tested before enabling integration.

**Excluded:** question/history APIs until Gate 3, human handoff/notifications,
shared-search broker, document/RAG/source adapter implementation, real providers,
production setup, automatic reconciliation, legacy conversion and Legal activation.

**Gate 2 exit:** isolated schema, immutability, privilege and failure tests pass;
no real integration claim. Then propose Gate 3 and joint source/authority work.

**Current C06 state:** Gate 3 owner-only routes are registered with an explicitly
injected store factory (default unavailable). Independent c06_0002 adds immutable
conversation/turn references and internal completed-U1 linking. No actual
document/RAG/source adapter or public question submission is connected. See
[Gate 3 contract/verification](c06-gate3-verification.md) and retain
[Gate 2 security limits](c06-gate2-verification.md).
