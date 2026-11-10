# Synchronization fixes â€” 2026-11-10

Branch: `sync/core-into-documents`, baseline `c0757b7`. Local changes only.
**Not approved for a PoC merge:** real pgvector schema verification is blocked.

## Changes and boundaries

- Reuse the reviewed Core question adapter and accounting processor; compatibility
  imports replace the duplicate processor. Replay loads the tenant-owned immutable
  result and revalidates every current source audience, status and version.
- `tenant_0007` adds append-only `question_results`; runtime has SELECT/INSERT only.
  Answers are never written to central usage tables. The existing published
  `usage002_result_payload` migration is preserved as history, with its nullable
  column intentionally unmapped/unused. No old data is copied, deleted or migrated.
- The new central `usage003_merge` reconciles RBAC/result-column heads without DDL
  or data loss. Full fresh-cluster tests found the original multiple-head failure;
  a subsequent complete Core PostgreSQL run succeeded after this repair.
- Manifest remains `ready:false`, requires tenant_0007 and includes documents_id_seq.
  Never activate Legal on the strength of unit tests.
- Question pricing fails closed until reviewed model/rates/revision are configured.
  Current metered provider support is text-only OpenAI. Input/output caps reserve
  the complete envelope; UTF-8 text size is checked before dispatch. SDK retries
  are disabled. Provider response IDs and token usage drive integer settlement.
  Cached-input usage needs its reviewed rate; missing/ambiguous evidence retains
  holds for reconciliation. No word-count costs or fabricated attempt receipts.
- Tenant metadata is separated from central Base. Application import/health no
  longer opens a configured development database or initializes extraction models.
  Document responses join no-store and Core's invalid-identity endpoint matrix.
- Legacy PoC setup/E2E/generator scripts were removed; history retains their source.
  Existing document checks now use the guarded Core fresh-cluster fixture and retain
  databases for recovery instead of dropping them. No fixed company database URLs.

## Executed verification

The final complete unit run passed **289 tests**, zero failures/errors/skips.
Redacted report: `.local/core-tests/4d34de9d8eec40349d476672958b80b5/summary.json`.
No real LLM request, company document processing or original database query was made.

- The seven focused security cases passed: paid replay once, source revocation,
  owner isolation, changed payload, missing result, provider usage/caps/cache-price
  handling, single migration heads, and department-scoped audience roles.
- A complete Core/native PostgreSQL run passed **65 tests**: authentication 2,
  departments 7, users 5, role policies 5, usage/concurrency 10, access 6, lifecycle 3,
  database isolation 19, synthetic tenant contract 7, document result migration 1.
  Redacted report: `.local/core-tests/2a7e1b9aea934f84a5c9ba5836c65efc/summary.json`.
- The standalone result test executes tenant_0007 on PostgreSQL, verifies owner-bound
  replay and revoked role rejection, and checks UPDATE/DELETE/CREATE are denied to
  the API role. It does **not** claim to execute tenant_0001..0007 with pgvector.
- 51 focused HTTP/access/discovery/rejection cases also passed. The only observed
  successful-suite warning was the existing Starlette/httpx TestClient deprecation.

Before migrations/grant changes, runners backed up only their newly initialized
clusters. Those clusters were stopped and retained with private recovery artifacts.
No existing central/Legal/HR/Finance database, ACL, owner, data or volume was changed.
Legal was not queried or updated; its required failed hold remains. PoC is deferred.

## Still blocked / not executed successfully

- The actual full-document provisioning test was attempted on fresh native PG18.
  It failed at `CREATE EXTENSION vector`: extension vector is not installed on this
  PostgreSQL system. This is a real failure/blocked gate, not a passed or skipped test.
- Docker Desktop was started from its real installed path, but its backend failed
  while initializing the `dockerInference` listener (Windows cannot access the
  socket path). The engine remained unavailable. No factory reset, socket deletion,
  Windows security change or existing-volume cleanup was performed.
- Thus full legacy document schema checks, real document worker ACL verification
  and Docker image/runtime verification have not passed for these changes locally.
  The document tests also need a separately privileged bootstrap to install vector
  in a NEW tenant before the limited owner/worker runs migrations; do not promote
  the worker or API to superuser to work around this requirement.
- Hosted CI has been prepared but not triggered: no changes were pushed and no PR
  was opened/merged. Its full-schema checks still need to succeed before merge.

## Reproduction / remaining gates

Use the existing local test venv, from repository root:

```powershell
.local/rbac-venv/Scripts/python.exe Backend/tools/run-core-tests.py --suite unit
.local/rbac-venv/Scripts/python.exe Backend/tools/run-core-tests.py --suite integration --backend native
```

With a working Docker engine and the locally built Core test image:

```powershell
pwsh -File Backend/tools/test-auth-postgres.ps1 -TestFile tests/test_documents_postgres.py -PostgresImage pgvector/pgvector:pg17
pwsh -File Backend/tools/test-auth-postgres.ps1 -TestFile tests/test_document_provisioning_postgres.py -PostgresImage pgvector/pgvector:pg17
```

Both Docker commands create private internal networks and fresh containers without
published ports, capture backups, and stop/retain their new test resources. Linux
test-container UID matches the private host bind mount; secrets are not chmod 777.
The runtime image remains non-root and never receives bootstrap/provisioner secrets.

CI now includes these two document gates on sync-branch pushes and PoC-targeted PRs.
The pgvector image currently uses the explicit pg17 tag; resolve/pin its tested
digest when the engine/registry verification is available. No image execution is
claimed by a configuration-only check.

Configure `LLM_PROVIDER=openai`, an exact reviewed `LLM_MODEL`, `LLM_PRICING_REVISION`,
`LLM_INPUT_MICRO_USD_PER_MILLION`, `LLM_OUTPUT_MICRO_USD_PER_MILLION`, and (for cached
usage) `LLM_CACHED_INPUT_MICRO_USD_PER_MILLION`. Prices are integers, not API client
input. Keep the API key in private local configuration. No default price is supplied.
Real API receipt/pricing behavior remains to be verified separately with synthetic
data and an approved spend limit; unit tests use a fake SDK only.

Before merge: run the full schema/worker/Docker gates, review the local commits and
secrets scan, and explicitly approve how the application integrates with PoC Phase 2.
No Phase 2 work, Legal activation or migration of existing departments is authorized
or implied by these fixes. Cross-store crashes remain uncertain until reconciliation;
there is no automatic provider redispatch or distributed transaction.
