# Independent task U1 verification — 2026-11-04

Tracking correction only: originally implemented under the 2.4 label. U1 is
independent of the original Roles & Permissions APIs task. Recorded test results
and migration/commit identities below are unchanged.

Branch: `feature/core-backend`, based on published `ef4d30a`. Accounting foundation
only; no actual provider calls, document/RAG changes, push, merge or deployment.

## Executed checks

| Check | Actual result |
| --- | --- |
| Full non-integration suite | **130 passed**, 24 integration tests deselected; 58.10 seconds |
| Isolated PostgreSQL accounting suite | **9 passed**; 8.56 seconds |
| Authentication PostgreSQL regressions | **2 passed**; 6.62 seconds |
| User-administration PostgreSQL regressions | **5 passed**; 8.91 seconds |
| Department PostgreSQL regressions | **7 passed**; 9.82 seconds |
| Python compilation / Git diff whitespace check | Passed |

The local suite includes all 107 previous non-integration tests plus 23 accounting
checks. It covers configuration fail-closed behavior, strict numeric inputs, own-user
privacy, request/attempt/receipt replay, no-answer charging, paid technical refunds,
rate consumption after refund, daily/monthly rollover, old-period refunds, carried
holds, lowered/disabled policy, timezone freeze, inactive users, execution claims,
global receipt uniqueness, overrun accounting, cancellation and ledger rollback.
The fake processor demonstrates that routing/multiple department/summary steps
count as one question, that request retries do not dispatch again, and that an
ambiguous provider exception cannot manufacture a refund.

PostgreSQL 17.10 tests used a dedicated restricted runtime identity and real locks:

- Eight duplicate request deliveries create one reservation and one debit.
- Concurrent unique requests cannot exceed daily credit or company money caps;
  distinct users share the same company budget.
- A simultaneous start/cancel race never releases money after execution starts.
- Attempt caps prevent excess provider authorizations. Concurrent identical receipts
  charge once and concurrent technical completion returns one question credit once.
- Denying ledger INSERT rolls back both the provider receipt and held-money update;
  a subsequent verified retry records the cost exactly once.
- Uncertain in-flight holds persist across month boundaries and cannot be released
  merely by supplying a reconciliation reference while an attempt remains pending.
- A concurrent technical-refund loop exhausts the rate window despite zero net
  question consumption. No provider call is triggered for rejected admissions.
- Two Admin policy updates based on the same version yield one 200 and one 409.
- The API role cannot UPDATE/DELETE ledger or policy-audit rows, DELETE requests,
  or rewrite request ownership (SQLSTATE 42501). Employee APIs do not expose other
  users, company budget amounts, fingerprints or execution claims.

Each integration suite used a separate backed-up test container on an internal
network with no published ports. Owner/ACL snapshots, database/global backups and
readable backup catalogs were captured before grant changes. The new migration
adds five central tables and indexes, leaving the initial policy unconfigured.
Private artifacts/stopped containers are retained under ignored local state,
including accounting run `4814b1f97090`, auth `4846dcc6ce58`, users `1b30650983ed`,
and department `1222afe96aad`. No existing database was dropped or reset.

One existing non-failing Starlette/HTTPX TestClient deprecation warning remains.
No dependency upgrade was required. A full restore drill and production capacity
assessment were not executed.

## Original development infrastructure

Read-only verification confirmed HR/Finance remain active, Legal remains
`failed / tenant_contract_unavailable`, and central revision remains `p002`.
Central, HR and Finance owners remain `ai_user`, with recorded restricted CONNECT
grants unchanged. No original data, migration, credential, owner, grant or worker
state was modified. The Day 1 live mutating provisioning test was not run; its local
regressions passed. The original stack has not been activated for this feature.

## Limits and remaining integration

Read [setup and accounting contract](usage.md) before normal-stack activation:
backup/ACL review, separate central migration `usage001`, least-privilege grants,
explicit Admin configuration, and reviewed deployment. No allowance or prices are
guessed by startup. Policy changes do not reset historical balances.

All processor/provider receipts in these tests are synthetic. Real quote calculation,
provider request caps, upstream idempotency, signed receipt verification and final
cost measurement are **not implemented** here. Receipt references are audit pointers,
not proof of terminality. Trusted future adapters must validate outcomes and receipts;
an LLM or client claiming a technical failure cannot authorize a refund.

Only already-incurred verified overruns are recorded beyond a cap; they cannot be
undone by accounting. The system retains pending attempt coverage and reports any
deficit, blocking further admissions when the company budget is exhausted. No
automatic timeout sweep, blind retry or public force-release endpoint exists.

Human conversations, post-answer transfer summaries, background ingestion billing,
actual LLM/RAG binding, last-unanswered-question transfer eligibility and real model
quality/cost tests remain later integration work. Existing platform APIs are not
globally blocked by exhausted question credits. Legal activation remains dependent
on the real tenant migration contract, not on this accounting implementation.
