# Local PR draft — not opened or published

**Title:** Add secure Core backend APIs, tenant provisioning and usage accounting

**Head:** feature/core-backend

**Base:** Backend

**Proposed state:** Draft

**Reviewed snapshots:** Core 9f033d2 plus local Subtask 3.5 changes; Backend 7d9e02b.
Recompute the comparison and exact head SHA before any later authorized publication.

## Proposed PR body

The baseline backend lacks authenticated administration APIs and a verified,
least-privilege department provisioning lifecycle. This change adds an opt-in
development identity provider and protected central APIs, while moving database
creation to a separate worker. A registered department becomes active only after
its pinned tenant migrations, identity, explicit grants and isolation checks pass.
The supplied contract remains unready; Legal is not activated by this PR.

### Included behavior

- Development password login with Argon2id, opaque expiring/revocable sessions,
  current-user dependency, safe profile, login rate limits and deny-by-default RBAC.
- Admin-controlled department creation grants, one department per authorized head,
  non-destructive archival, users/memberships and controlled role-permission policy.
- Separate provisioning registry/admin identities, managed database/runtime roles,
  bounded failure/retry handling, versioned tenant contract and explicit ACL checks.
- Independent U1 credit/usage/company-budget foundation with idempotent reservations,
  trusted receipt interface, immutable ledger events and uncertain-failure handling.
- Isolated unit/API/PostgreSQL tests, nonroot runtime/test image separation,
  local-only hardened Compose and setup/operator documentation.

### Migrations and operational boundaries

The historical b8e1db57a78c central revision is preserved. Central additions form
the registry/provisioning, authentication, department, users, usage and role-policy
chain through rbac001. Apply via an explicit offline migration identity only after
development-target verification, owners/ACL capture, recoverable backups and a
rollback/reconciliation plan. Existing email/name collisions require reconciliation;
do not rewrite or stamp history to bypass them. API startup never migrates or seeds.
Fresh initialization contains no departments; HR/Finance are explicit dev fixtures.

tenant_0001 contains infrastructure identity only. Real document migrations and
object privileges are pending joint review; synthetic migrations in tests are not
document schemas. Do not run provisioning for Legal or change ready:false to bypass
this gate. No existing HR/Finance/Legal data or credentials were changed for review.
Do not use destructive downgrades or remove volumes as a rollback shortcut.

### Validation

- Full native isolated suite: **298 passed**, zero failures/errors/skips, including
  63 PostgreSQL tests. Historical synthetic users/department/RBAC data survive the
  Backend-base → p002 → current-head path in each PostgreSQL group.
- Compose config --quiet passed with a non-secret, process-local placeholder.
- Gitleaks scanned the cumulative Core history: one reviewed false positive on
  synthetic-question-1 in an idempotency example, no actual secret detected.
- Whitespace and private-file scope gates checked. Only non-secret .env.example
  templates are tracked; local state and recovery reports stay outside Git.

See Backend/docs/core-review-verification.md for commands and retained local reports.
No GitHub CI run is claimed. Docker image builds/runtime/integration smoke tests
were not executed because the Linux engine is unavailable.

### Open review and integration gates

- Review the full cumulative diff, including both branches' .gitignore additions;
  do not discard private-state/artifact exclusions. No mergeability claim is made.
- Complete container verification when Docker is available, on fresh infrastructure.
- documents-ai is not included or merged. At 1a76601 it still needs the privileged
  runtime guard and central migration-history fixes, real tenant migrations/routing,
  document audience/original access enforcement and independent integration tests.
- The V1 API artifacts are Core proposals awaiting joint approval; they mount no
  document endpoints. Real OCR/RAG/provider billing, frontend, human chat, Microsoft
  Entra, TLS and production deployment are outside this Core review.

**Review request:** assess Core-only correctness, security boundaries, migration
compatibility and operator instructions. Keep this as Draft while the applicable
readiness gates remain unresolved. Do not enable auto-merge or deploy from this PR.

## Local publication checklist

- [x] Core code/security review and isolated test evidence recorded.
- [x] Real Legal unchanged, example unready, original department databases untouched.
- [x] Document/RAG code and document developer's migrations left unchanged.
- [ ] Separate authorization to push local commits and/or open this Draft PR.
- [ ] Refresh head/base comparison and recheck secrets and private files.
- [ ] Resolve review findings and applicable container/integration gates.
- [ ] Separate authorization and review for any merge or deployment.
