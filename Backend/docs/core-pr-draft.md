# Core Draft PR description

**Title:** Add secure Core backend APIs, tenant provisioning and usage accounting

**Head:** feature/core-backend

**Base:** Backend

**Proposed state:** Draft

**Reviewed snapshots:** Core 37d5fdf; Backend 7d9e02b; documents-ai 81c239f.
The user authorized publication as a Draft on 2026-11-06. The publication update
changes this description only; successful CI evidence below belongs to 37d5fdf,
and checks for the subsequent documentation commit must be assessed separately.

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

- GitHub Actions [Core verification run #1](https://github.com/ShathaAliA/Agentic-Systems/actions/runs/42134087470)
  completed successfully for **37d5fdf**: **237 non-integration tests on Linux and
  63 isolated PostgreSQL tests on Windows**, 300 passed in total. Historical
  synthetic users/department/RBAC data survive the Backend-base → p002 → current-head
  path in each PostgreSQL group.
- Both Docker runtime and test images built successfully on GitHub. Restricted
  runtime smoke passed: process health, UID 10001, absence of pytest, writable
  tmpfs and read-only-root configuration.
- Compose config --quiet passed with a non-secret, process-local placeholder.
- Gitleaks scanned the cumulative Core history: one reviewed false positive on
  synthetic-question-1 in an idempotency example, no actual secret detected.
- Whitespace and private-file scope gates checked. Only non-secret .env.example
  templates are tracked; local state and recovery reports stay outside Git.

See Backend/docs/core-review-verification.md and core-ci-verification.md for the
historical local evidence, and the linked GitHub run for hosted results. The local
Docker Desktop Inference manager still fails; hosted build/smoke success does not
resolve that local problem. PostgreSQL tests inside Docker, mounted secret/tenant
volume ownership and real document integration were not covered by this CI run.
One non-failing Starlette TestClient/httpx deprecation warning remains; dependency
migration requires separate compatibility verification.

### Open review and integration gates

- Review the full cumulative diff, including both branches' .gitignore additions;
  do not discard private-state/artifact exclusions. No mergeability claim is made.
- Complete remaining container/database and mounted-secret checks on fresh
  infrastructure; hosted runtime smoke is already successful.
- documents-ai is not included or merged. At **81c239f**, its tenant dependency uses
  a fixed shared connection and interpolates request input into search_path rather
  than using Core's registry-authorized separate-database connection. Restore the
  runtime privilege rejection and remove fixed privileged connection credentials.
- Restore established central migration history. The document migration currently
  starts an independent root in central alembic/versions; provide real business
  migrations descending from tenant_0001 and an agreed **Core V1** contract with
  exact revision/tables/explicit privileges for each separate department database.
- Keep central RBAC checks on the central session before tenant access; implement
  document audience (department AND role), restricted original downloads and safe
  response DTOs. Verify correction/reindex/version consistency, source locations,
  bounded error responses and independent cross-department isolation tests.
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
- [x] User authorized the documentation update and opening this Draft PR.
- [x] Head/base snapshots refreshed; publication checks cover secrets/private files.
- [ ] Resolve review findings and applicable container/integration gates.
- [ ] Separate authorization and review for any merge or deployment.
