# Subtask 3.3 Core verification — 2026-11-05

Branch: feature/core-backend. Baseline: 8fb8d01. Local changes only; no push,
merge, deployment or documents-ai fixes. See document-integration-contract.md
for the proposed interface and outstanding joint acceptance.

## Executed

Command from repository root (private environment with test dependencies):

```powershell
.local\rbac-venv\Scripts\python.exe Backend/tools/run-core-tests.py --suite all --backend native
```

| Group | Passed | Failed/errors/skipped |
| --- | ---: | --- |
| Non-integration | 233 | 0 |
| Authentication PostgreSQL | 2 | 0 |
| Departments PostgreSQL | 7 | 0 |
| Users PostgreSQL | 5 | 0 |
| Role policies PostgreSQL | 5 | 0 |
| Usage PostgreSQL | 9 | 0 |
| Core access PostgreSQL | 6 | 0 |
| Core lifecycle PostgreSQL | 3 | 0 |
| Physical database isolation PostgreSQL | 19 | 0 |
| Tenant contract PostgreSQL | 7 | 0 |
| **Total** | **296** | **0** |

Aggregate redacted logs and JUnit XML are retained locally under
`.local/core-tests/86300a60b6784c9f8fd5eaeeb27bfb40/`. The separate earlier targeted
contract run also passed seven tests; it is not double-counted above.

Each PostgreSQL group uses a fresh local PostgreSQL 18 cluster, loopback port,
private credentials and retained baseline backup/recovery artifacts. The new
contract group captures database owners/ACLs before changing test grants, creates
separate restricted registry/runtime identities and a provisioning identity, and
tests synthetic business migrations only. It verifies replay, policy tightening,
preservation of synthetic data/OID/credentials, missing objects, unrelated/missing
revisions, unready contracts and invalidation of previous synthetic readiness.
The seeded Legal fixture remains failed. Existing HR, Finance and real Legal
databases were not connected to or changed by these tests; real Legal status was
not freshly queried. Production document migrations and models are unchanged.

## Security and limitations

Whitespace checks pass. Native Gitleaks scanned the implementation and outgoing
commits. The outgoing scan reports one reviewed false positive: the literal
`synthetic-question-1` request_key in document-api-examples.json, an idempotency
example with no authentication value. No suppressing allowlist was added. No
actual .env files, credentials or secrets are included in the commits.

Docker execution was not performed in this verification; the runner's accepted
test-file list was updated, but native execution does not certify Docker parity.
No real document ingestion, audience-filtered RAG, provider billing or end-to-end
document flow was executed. The DTOs validate structure, not authorization or
truth of source evidence. Operator contract files and migration code must remain
trusted, access-controlled and reviewed; revision fingerprints do not hash code.

documents-ai's runtime guard regression and central migration-history blocker
remain assigned to its developer. Real tenant migrations, reviewed object policy,
document lifecycle/access tests and joint API approval remain release gates.
The example stays ready:false and this work does not authorize Legal activation.
