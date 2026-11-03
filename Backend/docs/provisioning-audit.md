# Day 1 architecture audit

Audited branch `feature/core-backend`, starting at `f727b03`. Read every tracked backend
module, both central Alembic revisions, all four existing test scripts, seed, root
README, Compose, requirements, and the empty department model base before changes.

- Central registry is unique per department/database, stores a credential *reference*,
  and already supports provisioning/active/failed. Registration is transactional and
  restricted to an active global role holding `departments:manage`.
- Dynamic routing checks active users/departments, scoped membership/permissions,
  and active registry before resolving credentials. Global grants intentionally
  bypass department membership. Existing authentication is not implemented; callers
  must obtain user IDs from a trusted authentication context.
- No process creates tenant databases or roles. No retry, claim, migration version,
  or safe failure tracking exists. Runtime and migration configuration are coupled.
- Central migrations end at `f48bfc74f531`. Tenant models contain only an empty
  `DepartmentBase`; there are **no document models or tenant migrations to replace**.
- Docker defines only PostgreSQL. Its bootstrap `ai_user` is a superuser; it must
  remain an administrative credential, never the FastAPI `DATABASE_URL`.
- Existing tests are executable scripts, two with import-time DB mutations; they
  are not isolated pytest unit tests. Requirements omit Alembic.
- Development inventory confirms HR/Finance active, Legal already registered and
  provisioning. Existing HR/Finance databases and roles must not be recreated.

Implementation boundaries: a separate worker owns provisioning, explicit tenant
Alembic interface, owned-resource markers, bounded retries, credential files outside
Git, activation after migrations and positive/negative privilege checks. No document
schema is invented. Development verification must fingerprint HR/Finance data before
and after and report any ACL changes separately.
