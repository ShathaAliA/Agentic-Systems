# C05 closure — Core integration readiness (2026-11-15)

**Disposition: CLOSED for the Core-side contract-audit and automated-verification scope.**
This is **not** approval of the live document integration, production tenant
provisioning, result-store persistence, or any database migration.

## Baseline and evidence

- Branch: `feature/core-backend`; verified tested head
  `4cba7564747d5f6a52b17ed4d29508ceeb63c3ca`.
- Change set: `Backend/tests/test_c05_integration_contract.py` and
  `Backend/docs/c05-integration-readiness.md`; the subsequent test-only
  correction guards route iteration with `hasattr(route, "path")`.
- Pull-request run: [43709135319](https://github.com/ShathaAliA/Agentic-Systems/actions/runs/43709135319)
  — **success**, `unit-and-container` and `postgres-isolation` passed.
- Push run: [43709130300](https://github.com/ShathaAliA/Agentic-Systems/actions/runs/43709130300)
  — **success**, both jobs passed on the same head.
- `unit-and-container` job log:
  **376 passed, 65 deselected, 1 warning** in the `not integration`
  selection. The warning concerns the Starlette/httpx test-client
  deprecation. Runtime Docker smoke reported
  `Restricted Core runtime smoke passed; no database connection tested`.
- `postgres-isolation` job log: **64 passed, 0 failures, 0 errors,
  0 skipped**, across authentication (2), departments (7), users (5),
  role_policies (5), usage (10), core_access (6), core_lifecycle (3),
  database_isolation (19) and tenant_contract (7).
- Previous run [43706793535](https://github.com/ShathaAliA/Agentic-Systems/actions/runs/43706793535)
  failed because `_IncludedRouter` has no `path` attribute. The
  test-only fix is present in the successful head; no production routing
  code was changed for that fix.

## Accepted C05 deliverables

1. Reviewed Core identity, RBAC, tenant V1, migration-history, DTO,
   accounting U1, external-result and response-security boundaries.
2. Added Core-only synthetic contract tests for fail-closed tenant
   readiness, draft DTOs, migration metadata, scope-sensitive fingerprints,
   access denial, replay idempotency, and revocation.
3. Documented the partner branch as **read-only evidence**; no partner
   code was imported and no joint acceptance was asserted.
4. Verified both hosted Core CI jobs passed at the tested head.

## Explicit exclusions and open gates

- **Legal remains failed as the required project constraint.** Its live
  database/status was **not inspected or changed**; synthetic Legal
  fixtures remain denied. `tenant-contract.example.json` stays
  `ready:false`.
- No existing HR, Finance, or Legal database was targeted; hosted
  PostgreSQL tests used fresh isolated clusters.
- No migrations, provisioning, provider/LLM calls, document operations,
  deployment, merge, cherry-pick or PR state change were performed as
  part of this C05 closure.
- **Not proven:** real document authorization, real persistent
  Conversation/Result Store, crash/recovery against the proposed store,
  tenant `question_results` integration, production permissions, or
  end-to-end RAG correctness.
- **Unresolved design gates:** `rbac001` versus
  `usage003_merge` migration histories; Core's no-content-in-Central
  rule versus the partner's `usage002_result_payload`; single
  authoritative result-store ownership; document source/audience/version
  semantics; tenant activation and least-privilege grants.

## Next phase

Proceed to **C06 design only** in
`Backend/docs/c06-conversation-result-store-design.md`.
Do not implement a store, change migrations, activate Legal, touch
existing department databases, modify `documents-ai`, or merge until
design decisions and integration ownership are approved.

**Evidence standard:** The pass counts above are from the linked
GitHub Actions job logs. They do not imply that real HR/Finance/Legal
environments were tested.
