# AI Platform / Agentic Systems

FastAPI backend in `Backend/`. Central PostgreSQL stores users, department RBAC,
and the database registry. Each department has its own database and restricted
login role. Only the separate provisioning worker creates databases/roles.

- [Day 1 setup, migration interface and worker operation](Backend/docs/provisioning.md)
- [Architecture audit](Backend/docs/provisioning-audit.md)
- [Development verification results and outstanding blockers](Backend/docs/day1-verification.md)
- [Subtask 2.1: development authentication, enrollment and tests](Backend/docs/authentication.md)
- [Subtask 2.2: department creation grants and archival](Backend/docs/departments.md)
- [Subtask 2.3: central users, memberships and approved roles](Backend/docs/users.md)
- [Independent task U1: question credits, usage and company budget](Backend/docs/usage.md)
- [Core backend roadmap and task status](Backend/docs/core-roadmap.md)

Authentication is disabled by default. Subtask 2.1 adds development-only password
login, expiring/revocable opaque sessions, `GET /auth/me`, and reusable RBAC
dependencies. Enable it only after the documented central migration, restricted
grants and offline enrollment; Microsoft Entra integration remains pending.

Subtask 2.2 adds Admin-issued department-name grants, one owned department per
head, protected department APIs and archival without data deletion. Fresh migrations
create no departments. HR/Finance are explicit development fixtures only:
`APP_ENV=development` and `python seed.py --development-fixtures` from `Backend/`.
See the setup guide before applying the central migration or changing grants.

## Tests

Install `Backend/requirements-test.txt` in a private Python environment. From the
repository root, run all Core unit/API and isolated PostgreSQL groups with:

```powershell
python Backend/tools/run-core-tests.py --suite all --backend native
```

Native mode uses Windows/PowerShell 7.2+ and installed PostgreSQL 18 by default;
`--postgres-bin` overrides its binaries. Docker mode is available with
`--backend docker` and an existing `ai-platform-core:test` image built with
`docker build --target test -t ai-platform-core:test Backend`. See the
[Subtask 3.1 testing guide](Backend/docs/core-testing.md) for unit-only mode,
per-group operation, private/redacted reports and actual verification limits.

Default pytest discovery is fenced to `Backend/tests/` from both the repository
root and Backend, including a Backend-only build context. Legacy backend-root
`test_*.py` scripts can mutate the registry during import and are rejected by the
automatic test entry points. Environment flags alone cannot enable database tests.

Original-cluster verification is a separate operator-only workflow. The Core
runner never invokes it; manual live tests also require an explicit CLI opt-in,
owner/ACL snapshots and backup acknowledgement. Do not run them as a substitute
for isolated tests. **Legal must remain failed** until real document migrations
and isolation checks pass; the supplied contract intentionally has `ready: false`.

Roles & Permissions APIs: [setup, policy matrix and verification](Backend/docs/role-permissions.md).

Core endpoint access policy and test matrix: [Subtask 2.5](Backend/docs/core-access-contract.md).

PostgreSQL physical isolation and least-privilege grant policy: [Subtask 3.2](Backend/docs/postgres-isolation.md).

Core/document integration: [V1 contract, proposed API and ownership gates](Backend/docs/document-integration-contract.md).
The operator-owned `Backend/tenant-contract.example.json` remains unready. Its V1
schema is in `Backend/contracts/tenant-v1.schema.json`; supply an agreed pinned
business migration and reviewed explicit privileges before requesting activation.
The API DTOs under `Backend/contracts/` are proposals, not live document endpoints.

Container setup, private environment files, runtime/test image targets and offline
operator boundaries: [Core local container guide](Backend/docs/core-containers.md).
