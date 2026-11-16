# C02 — response and error policy

Core-only follow-up to C01 on `feature/core-backend`. This change neither imports
document modules nor registers document endpoints. The 44 Core operations and
their authentication/RBAC policies remain unchanged.

## Behavior

`app/response_security.py` provides ASGI response middleware for these exact
roots and their slash-delimited descendants:

- `/auth`, `/admin`, `/me`, `/departments`
- `/api/documents` (policy preparation only; these routes are not added)
- `/health/db`

Both root and descendant paths receive `Cache-Control: no-store` and
`Pragma: no-cache`, including errors and redirects. Similar names such as
`/api/documents-public` are not matched. ASGI `root_path` is accounted for;
query strings are not used to decide policy. Existing `WWW-Authenticate`,
`Retry-After`, redirect and method headers are preserved. Public liveness
responses retain their existing behavior.

Existing generic validation 422 and SQLAlchemy 503 handlers are retained.
An unexpected application exception before headers produces a generic JSON 500
and no-store even on public paths. The application logs only a fixed event,
without exception text, traceback, query, body, token, SQL or driver parameters.
HTTP errors deliberately returned by application code retain their status and
body; this middleware is not a scrubber for arbitrary successful payloads.

After headers have been sent, the response cannot safely be replaced. The
middleware logs the same fixed event and raises a generic failure with the
original exception chain suppressed. It does not append an error body or send
a second response start. Sensitive streams already have no-store headers;
headers on an already-started public stream cannot be changed retroactively.
Cancellation and non-HTTP ASGI traffic are not caught as HTTP application errors.

## Verification and limits

Tests use synthetic ASGI routes and in-memory SQLite identities only. No
existing PostgreSQL database, Docker container, document processor or LLM is
used. Legal's live state was not queried or changed; the failed requirement
remains in place. HR and Finance databases are outside this batch.

Commands from repository root:

```powershell
python -m pytest -q Backend/tests/test_response_security.py Backend/tests/test_core_access.py Backend/tests/test_authentication.py Backend/tests/test_swagger_development_auth.py -p no:cacheprovider --tb=short
python Backend/tools/run-core-tests.py --suite unit
```

The focused run passed 116 tests. After updating an existing audit-failure HTTP expectation to the new generic 500 response, the final full Core run passed 325 tests with zero failures, errors or skips. The rollback and session-preservation assertions remain intact.
Coverage includes boundary/root-path matching, 2xx/4xx/5xx response policy,
redirects, missing document routes, generic error bodies, application log
canaries, partially started responses, auth headers and the 44-operation gate.

This is not a deployment logging audit. Reverse proxies, access logs and APM may
record URLs independently; configure and review them before production. No
global logging configuration, CORS, database privileges, migration, provider,
U1 behavior or document/RAG module is changed. Docker/hosted CI and PostgreSQL
integration tests are not part of this response-only verification.

Scoped pre-commit review found no blocking issue and zero matches for selected
provider-key, private-key, credential-bearing PostgreSQL URL and session-token
patterns in the code/test additions. No actual .env or private state is staged.
This is not a full dependency or deployment security audit. The final suite
retains only the existing Starlette/httpx deprecation warning.
