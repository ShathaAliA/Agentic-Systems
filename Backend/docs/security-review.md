# Pre-push security review — 2 November 2026

Reviewed the four local Day1 commits: `29ab4dc`, `1a113ee`, `a0c96b5`, `d10592f`,
based on `f727b03`, including worker, role/database DDL, credentials, runtime guard,
RBAC/router integration, migrations, Docker mounts, development tooling and tests.
One additional hardening commit records the review fixes; original commits were
not rewritten. Only `feature/core-backend` is authorized for publication.

## Findings addressed

1. **Readiness bypass through a foundation-only contract.** A manually supplied
   contract could set `ready: true`, name `tenant_0001` and only the identity table.
   The CLI now treats this as unavailable. It requires a non-foundation pinned
   revision, the identity table, and at least one business table. Alias revisions
   and metadata-only contracts cannot activate a department. Missing migrations
   still fail before activation. This does not invent the document developer's
   tables or assume that a ready flag alone proves schema readiness.
2. **Administrative adapter target validation depended on callers.** Resource
   provisioning and the migration adapter now independently reject non-managed
   database/owner/runtime names before opening an administrative connection.
   The SQLAlchemy identifier preparer quotes SET ROLE as a second defense.
   Existing worker validation already constrained inputs; these changes close
   unsafe future direct-call paths rather than claiming a reachable API exploit.

Regression coverage includes foundation/alias/metadata-only contracts, missing
identity requirements, valid future document contracts, and unmanaged/injection-like
migration/resource targets. The original document model files remain unchanged.

## Verification

- **33 isolated unit cases passed** after the fixes.
- The backed-up development integration passed with final code. Legal remains
  `failed / tenant_contract_unavailable`; foundation migrations and isolation checks
  do not substitute for the missing document schema. HR/Finance remain active with
  unchanged schema/data fingerprints and registry records.
- Development checks capture owners/ACLs and recoverable HR/Finance backups before
  idempotent setup; the review added no new privilege policy or database deletion.
- The official Gitleaks image, pinned by digest
  `sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`, scanned
  the original four outgoing commits offline with redaction: **no leaks found**.
  The entire final outgoing range, including this hardening commit, must be scanned
  again before push. Raw scanner reports stay under ignored `.local/security/`.
- Publication gate checks every outgoing commit tree for `.env` files, private
  local state, key/dump artifacts and merge commits. Environment/backup/credential
  files are not staged or pushed. Test-only `CANARY_SECRET` literals are not actual
  credentials; no broad scanner ignore rule was introduced.
- Remote target is refreshed before publication; only a normal fast-forward push
  of HEAD to `refs/heads/feature/core-backend` is permitted. No force push, main
  update, merge, or automatic PR merge is part of this task.

## Remaining limitations / release gates

The document developer's real migration contract is still unavailable. Legal must
stay failed until it is supplied and actual migration/isolation checks pass.
Document-table CRUD/immutability policies need review before enabling that contract;
the Day1 business-table grant policy is intentionally not a claim of per-document
security or immutable audit enforcement. Production additionally requires managed
secret storage/rotation, trusted authentication, provisioning identity isolation,
and populated-data restore/load tests. See `day1-verification.md` for the earlier
development traceback credential incident (rotated) and the initial ACL-snapshot
gap; neither history is concealed by this review.
