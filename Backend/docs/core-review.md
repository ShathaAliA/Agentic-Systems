# Subtask 3.5 — Core review, 2026-11-05

Reviewed Core baseline 9f033d2 against target `Backend` at 7d9e02b. The merge base
is b872e72; at review start Core has 54 exclusive commits and 127 changed paths,
and Backend has one exclusive .gitignore commit. The spelling/case of the target
is significant. No checkout, merge, branch rewrite, push or PR creation occurred.
This is a review of the cumulative Core change, not just the container changes.

## Reviewed boundaries and evidence

| Area | Review focus | Evidence / limits |
| --- | --- | --- |
| Authentication | Opt-in development-only provider, bounded login input, Argon2id, random opaque expiring tokens, digest storage, logout/password rotation | auth config/routes/service/enroll/models; missing/invalid/inactive/expired credentials, rate counter concurrency and safe error tests |
| RBAC and APIs | Validated identity only, global versus department permissions, active user/membership/department, safe response models and bounded list sizes | auth dependencies, authorization, departments/users/role policy routes and services; Core access inventory and isolation suites |
| Administration | One-head creation ledger, immutable global policy, last enrolled Admin protection, self-escalation denial, version checks, shared writer lock, transactional audit/session revocation | Department, user and policy services/models; simultaneous writers and audit rollback tests |
| Tenant routing | Authorization before engine lookup, restricted credentials, active registry and archival fence | database_router and runtime_security; real document audience filtering remains document-owned |
| Provisioning | Separate identity, managed-name validation, identifier quoting, safe references/errors, retries/claim fence, exact revision and policy fingerprint, least-privilege checks before active | worker, contract, migrations, postgres adapter; fresh synthetic tenant and physical PostgreSQL negative tests |
| Usage/U1 | Server accounting only, serialized reservations/holds, per-user idempotency, trusted receipts, uncertain failures without automatic refunds | usage service/models/processor/routes; concurrent admissions and ledger tests; no real provider integration |
| Migrations | Historical revision retained, additive central chain, unready tenant example | PostgreSQL upgrade with historical synthetic data; production constraints/data still need operator reconciliation |
| Containers/test tooling | Nonroot runtime, separate test image, separated secrets, localhost ports, disabled proxy trust, no automatic migrations/seeds, opt-in isolated tools | Compose quiet validation and native suite; image/runtime behavior not verified because Docker engine unavailable |

No new blocking Core application defect was confirmed by this review. No runtime
behavior, application model or production migration was changed in 3.5. One test
coverage gap was addressed: the shared PostgreSQL fixture formerly began populating
historical data at p002; it now starts at Backend's b8e1db57a78c revision and checks
synthetic users, departments, roles, permissions, memberships and assignments through
p002 and head, including preservation of a failed registry row. The historical
initial migration is unchanged relative to Backend. All nine PostgreSQL groups
exercise these strengthened setup assertions. This is not a restore test or a
guarantee that arbitrary existing company data requires no reconciliation.

## Open findings / gates

Priorities below distinguish integration/release blockers from Core-only review.
They do not authorize additional implementation or declare a penetration test.

| Priority | Owner | Finding / acceptance gate |
| --- | --- | --- |
| P1 integration | documents-ai developer | Runtime privilege guard still warns rather than rejects administrative credentials; restore fail-closed rejection and prove it with a negative connection test |
| P1 integration | documents-ai developer | Central migration history is replaced with a new initial migration; restore established applied revisions and demonstrate both fresh and existing-Core upgrades |
| P1 integration | documents-ai developer + Core review | Documents remain on central Base/get_db, tenant chain is foundation-only, no agreed V1 business grant/revision contract; provide tenant migrations and routing/audience/isolation evidence before activation |
| P1 integration | documents-ai developer | Original download still uses general documents:read and listing returns unrestricted model data; implement original-file head/deputy/Admin gate and safe audience-filtered DTOs |
| P1 container readiness | Core/operator | Linux daemon unavailable; build both targets and test UID/secret-volume access, read-only operation and isolated Docker PostgreSQL workflows before claiming container readiness |
| P2 merge preparation | Integrator | Both branches add different .gitignore contents; review their union without losing private-state/artifact exclusions. Actual GitHub mergeability has not been tested; no merge was attempted |
| P2 operations | Core/operator, future scope | Department engine cache is bounded at eight and rejects further pools; size/eviction strategy requires load testing for ten departments, not assumed adequate from 20 synthetic users |
| P2 operations | Core/operator, future scope | Login windows/sessions have expiry checks but no scheduled retention cleanup; define bounded maintenance using a separate reviewed identity, without deleting audit/chat history |
| P2 release | Project owner | No CI workflow, vulnerability audit or immutable image/dependency lock certification added; native tests are local evidence rather than GitHub required-check enforcement |

## documents-ai snapshot: 1a76601 (read-only)

The new fa52018/1a76601 changes add pending_review/approved/archived status,
integer versions, an audit model, approval endpoint, approved-only retrieval and
archival instead of physical document deletion. These improve the earlier branch;
they do not close the above gates. Previous versions are archived before a new
upload/extraction succeeds, with separate commits rather than a single publication
transaction. Audience fields, immutable source-version locations and an approved
tenant contract remain missing. Raw provider errors can still reach answers.
No document tests were run, imported, modified or represented as passing here.
All fixes remain with the document developer; Core does not supply replacement
document models or merge the branch.

## Safety and conclusion

The normal API runtime still rejects privileged roles and inherited/owner powers.
Operator migration/provisioning code and direct internal service calls remain
trusted boundaries, not public APIs. Revocation/archival protects subsequent
authorized operations; this review does not promise cancellation of every in-flight
request. Runtime grants protect database capabilities, not employee document audience.

Existing HR/Finance/Legal databases were not accessed or modified. Last documented
real Legal status is failed, not freshly queried; test Legal fixtures remain failed,
and the example contract stays ready:false. Core is ready for a **draft review**,
not a statement of document integration, production identity or deployment readiness.
See core-review-verification.md for actual test/scanner results and
core-pr-draft.md for the local proposed PR text.
