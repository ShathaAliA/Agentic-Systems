# U1 adapter verification — 2026-11-09

Branch: `feature/core-backend`. Baseline: `763055b`. Local work only; no push,
merge, PR change, real provider request or document processing.

| Executed check | Actual final result |
| --- | --- |
| `python Backend/tools/run-core-tests.py --suite unit` using the existing local test venv | 266 passed, zero failures/errors/skips; 65 integration tests deselected |
| Native fresh-cluster runner, `tests/test_usage_postgres.py` | 10 passed, including the new concurrent adapter delivery test |
| Native fresh-cluster runner, `tests/test_tenant_contract_postgres.py` | 7 passed, covering the existing tenant migration/grant/isolation contract |
| Gitleaks directory scans of usage source, tests and docs | No findings |
| `git diff --check` | Passed |

The final unit run includes 21 adapter cases and 8 machine-readable tenant-fixture
cases alongside the existing suite. PostgreSQL tests use the restricted test API
role. Eight simultaneous deliveries (one processing request and seven duplicates)
produce one processing execution, one provider-attempt settlement, one question
debit and seven micro-USD of synthetic cost. A later replay loads the stored fake
result without redispatch. No real provider was contacted.

Unit coverage includes changed question/context/scope/quote conflicts, per-user key
scope, department/role denial, unknown/inactive users, failed department denial,
post-processing access revocation, affirmative content authorization, missing result
storage, quota/budget/unconfigured-policy rejection, confirmed failure and uncertain
timeout. Core's existing processor/service tests cover the underlying accounting
state machine. The synthetic manifest fixtures do not certify the real document
schema, migration head or runtime policy.

## Execution limitations and corrections

The first sandboxed native attempt could not connect to its newly created localhost
server (Windows socket permission error 10013). The runner stopped that server;
no tests were claimed for that attempt. The successful retry used the same audited
runner with permission to access its own fresh local server. Both successful test
clusters were stopped and retained with private recovery artifacts. No existing
development database or volume was used.

An initial unit assertion expected unsorted privilege lists. Core canonicalizes
them; the expected order was corrected without changing runtime behavior. The final
unit rerun above passed. An earlier restricted unit process stalled and was cancelled;
it is not counted as a successful run.

All successful suites emitted the existing Starlette/httpx TestClient deprecation
warning. No dependency changes were made for that unrelated warning. Docker and
hosted CI were not run for this change; commits remain local.

## Data and remaining integration boundaries

No SQL/migrations/grants were added for existing databases. Test-only provisioning,
migrations and privilege changes occurred exclusively in newly initialized clusters,
with pre-change backups retained by the runner. HR, Finance, Legal and the existing
central database were not connected to or changed. Legal's live state was not queried
or updated; the required `failed` hold remains. PoC is deferred and untouched.

The adapter has no HTTP route and stores no answer content centrally. Before real
use, documents-ai must provide reviewed content authorization, owned immutable
context snapshots, safe result persistence/retrieval, provider receipt verification,
conservative pricing envelopes and HTTP error/state mapping. Those callbacks remain
trusted security boundaries. A crash between result storage and accounting closure
still needs reconciliation; no automatic recovery dispatch or distributed transaction
is implemented. Real document migrations/audiences and end-to-end RAG acceptance
remain unverified by this work and cannot authorize Legal activation.
