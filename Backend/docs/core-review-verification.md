# Subtask 3.5 verification — 2026-11-05

Implementation started from clean, published feature/core-backend 9f033d2.
Target snapshot: Backend 7d9e02b; documents-ai snapshot: 1a76601. No original
database operation, push, PR creation, merge, deployment or next task was performed.
The PR title/body/checklist is a local Markdown artifact, not an open GitHub PR.

## Changes and executed tests

The only test-code change strengthens the shared fresh-cluster fixture: migrate
from Backend's b8e1db57a78c, populate seven historical tables with synthetic data,
upgrade through p002/auth001/head and compare the original rows/columns. The failed
registry marker is preserved. Existing test identities/counts and runtime privileges
are retained. No production schema/model or document/RAG code changes were needed.

Command from repository root, using the private test environment:

```powershell
.local\rbac-venv\Scripts\python.exe Backend/tools/run-core-tests.py --suite all --backend native
```

| Group | Passed | Failed/errors/skipped |
| --- | ---: | --- |
| Non-integration | 235 | 0 |
| Authentication PostgreSQL | 2 | 0 |
| Departments PostgreSQL | 7 | 0 |
| Users PostgreSQL | 5 | 0 |
| Role policies PostgreSQL | 5 | 0 |
| Usage PostgreSQL | 9 | 0 |
| Core access PostgreSQL | 6 | 0 |
| Core lifecycle PostgreSQL | 3 | 0 |
| Physical isolation PostgreSQL | 19 | 0 |
| Tenant contract PostgreSQL | 7 | 0 |
| **Total** | **298** | **0** |

The stronger migration assertions run in every PostgreSQL group's fixture;
they are not counted as extra test cases. All groups use fresh PostgreSQL 18
infrastructure with retained baseline backups/owner-ACL snapshots and private
credentials, and stop their test clusters afterward. Logs/JUnit/summary:
`.local/core-tests/6c3257989d3d4888855c092393945d68/` (ignored, private).
Existing HR/Finance/Legal databases were not queried or changed. Real Legal's last
documented state remains failed; no claim of a fresh real-status query is made.

## Review gates

- Compose quiet validation passed again with a non-secret process-local password
  placeholder, without rendering environment contents or starting services.
- Native Gitleaks inspected the cumulative 54-commit Core history before 3.5.
  One match was reviewed: synthetic-question-1 in the document-api idempotency
  example (23123ea), not an API credential. No suppressing allowlist was added.
  New 3.5 commits are scanned separately; no actual secrets detected.
- Historical commit trees relative to Backend are inspected for actual .env files,
  tracked .local state and private key/backup artifacts. Only .env.example templates
  are permitted; none of those private artifacts were found.
- Whitespace/scope checks verify the final change set; final branch must remain
  feature/core-backend with a clean tree and only local commits ahead of origin.

## Not executable / not claimed

Docker client 29.6.2 is installed, but dockerDesktopLinuxEngine is unavailable.
Image builds, runtime secret/subpath/UID access, read-only smoke tests and Docker
PostgreSQL integration tests were not executed. Native tests and Compose parsing
are not a substitute for those container checks. No real provider, OCR/RAG,
document-audience, Microsoft identity or production test was run. No automated
GitHub checks or repository branch-protection enforcement was added.

The local PR draft proposes Backend as base and feature/core-backend as head.
Both sides have .gitignore additions; actual mergeability/conflict resolution was
not attempted or certified. documents-ai retains its two critical integration
blockers and real tenant contract gaps; these remain owned by its developer.
See core-review.md for prioritized findings and the boundary of this review.
