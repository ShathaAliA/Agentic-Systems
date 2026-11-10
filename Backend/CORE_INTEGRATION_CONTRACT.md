# Core / document integration boundary

- Resolve opaque development sessions through Core get_current_user; future Entra
  integration remains separate. Never accept body/header user IDs as identity.
- Central get_central_session owns users/RBAC/accounting only. Document Base and
  result payloads belong to the owning tenant, opened after department authorization.
- Use Core execute_question / run_once, not a duplicate accounting implementation.
  Current /ask is stateless; context revision binds model, price revision and caps.
- Persist immutable results by authenticated user + Core request ID in the tenant.
  On initial delivery AND replay, validate every source document, version, current
  status and scoped department/role audience. Missing/denied results never redispatch.
- A paid OpenAI attempt is admitted before dispatch. The full capped input/output
  envelope is reserved once. SDK retries are disabled. Use provider response IDs and
  usage counters with reviewed integer prices, not word counts or guessed receipts.
  Missing receipts/prices or ambiguous failures retain uncertain holds.
- Tenant manifest V1 remains ready:false. Current required revision: tenant_0007;
  includes documents_id_seq and append-only question_results grants.
- Full migration/ACL verification requires a new pgvector cluster; do not use the
  original central/Legal/HR/Finance databases. Legal stays failed pending review.

See docs/u1-question-adapter.md and docs/sync-blocker-verification.md. No integration
or PoC merge is implied by local unit success; real schema/CI gates remain mandatory.
