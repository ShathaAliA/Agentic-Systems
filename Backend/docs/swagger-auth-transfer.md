# Core transfer C01 — development Swagger authentication

Scope: selective adaptation of the local PoC Swagger form bridge onto
`feature/core-backend`. No document/RAG modules, database schemas, provisioning
logic, tenant grants or existing database data are changed. Legal must remain
failed; its live state was not queried. No push or merge is part of this batch.

## Changes

- Added `/auth/dev/token` as a form adapter to existing JSON login. Both paths
  use the same DB-backed limits and opaque expiring/revocable sessions.
- Swagger advertises a named development-only password flow with relative
  `auth/dev/token` URL. Request credentials still pass the same session validator.
- Existing `/auth/dev/login`, `/auth/me`, RBAC and revocation remain in place.
- Added pinned `python-multipart==0.0.33` for form parsing, synthetic HTTP tests,
  PostgreSQL coverage, and the 44th explicit Core access-contract operation.

No new environment settings or credentials are necessary. Existing development
mode requirements remain mandatory. This is not Microsoft authentication and
must not be enabled in production. No test identity or password is hardcoded.

## Verification

Commands from repository root, with the existing isolated Python environment:

```powershell
python -m pytest -q Backend/tests/test_swagger_development_auth.py Backend/tests/test_authentication.py Backend/tests/test_core_access.py -p no:cacheprovider --tb=short
python Backend/tools/run-core-tests.py --suite unit
./Backend/tools/test-auth-postgres-native.ps1 -Python '<absolute test Python executable>' -TestFile tests/test_authentication_postgres.py
```

The first command passed 73 tests. The PostgreSQL command passed 2 integration
tests, including the new form-login/profile/isolation/logout assertions under
least-privilege runtime credentials. It created a fresh PostgreSQL 18.6 cluster
on a random loopback port, backed it up, and confirmed shutdown afterward.
Recovery artifacts are retained in ignored private `.local/native-auth-tests/`.
Only that new test cluster received schema, fixture and role changes; no
existing HR, Finance, Legal, central database or Docker resources were used.

The full Core suite passed 282 tests with no failures, errors or skips through the sanitized runner. PostgreSQL tests were
executed natively; no Docker rebuild or hosted CI run is claimed. One existing
Starlette/httpx deprecation warning remains; it does not fail these tests.

## Security boundaries

Unknown/inactive users and wrong passwords return the same 401; invalid form
validation does not echo input. JSON and form login share counters across app
instances. Forged identity/role headers do not alter the authenticated user.
Expired, revoked and credential-rotated sessions are refused; department and
global checks remain effective. Responses retain `no-store` and production
configuration still rejects enabling development authentication.

Form client credentials and scopes are not authorization inputs. Browser
Logout alone does not revoke a server session: use `/auth/logout`. Swagger
remains development tooling; deployment documentation exposure, Entra and
production identity-provider work remain outside this batch.

## Local security review

Reviewed the C01 diff for identity spoofing, administrative bypass, session
validation, shared throttling, development-mode gating, response caching and
credential disclosure. No blocking finding was identified in this scoped review.
A local pattern scan of staged additions found no provider keys, private keys,
credential-bearing PostgreSQL URLs or complete development session tokens.
No actual .env, private recovery files, document/RAG modules or tenant migrations
are included. This was a scoped manual/pattern review, not a full dependency
vulnerability audit; gitleaks was unavailable. Docker/hosted CI were not executed.
