# Documents API contract — current development branch

Status: protected document lifecycle verified with synthetic data. Real question
result/conversation integration is disabled pending Core Gate 4. This is not
production release approval. Legal remains failed; the tenant manifest is unready.
Use /openapi.json as the executable schema and /docs for development testing.

## Identity and responses

Development auth uses opaque, expiring, revocable sessions, not JWT or Entra SSO.
POST /auth/dev/login accepts JSON email/password; POST /auth/dev/token supports
Swagger's OAuth2 password form. Both require explicit development_password mode.
GET /auth/me returns a safe profile. POST /auth/logout revokes the current session.
Send Authorization: Bearer <session> privately. Never expose credentials in screenshots.
Caller-provided IDs/role headers are not authentication. Every document operation
checks fresh identity, active department and its required RBAC permission.

## Document routes

Department selectors are query parameters except upload/replacement (multipart)
and ask (JSON). Selectors never grant access by themselves.

| Method/path under /api/documents | Permission | Request |
| --- | --- | --- |
| POST /upload | documents:upload | multipart file + department_id |
| GET / | documents:read | department_id; audience-filtered metadata |
| GET /{id} | documents:read | department_id; audience-filtered metadata |
| GET /{id}/review | documents:update | department_id; extracted text, version, status |
| POST /{id}/revise | documents:update | department_id + JSON expected_version, text |
| POST /{id}/approve | documents:update | department_id + JSON expected_version, audiences |
| POST /{id}/archive | documents:archive | department_id + JSON expected_version |
| PATCH /{id} | documents:update | department_id + JSON expected_version, filename |
| POST /{id}/version | documents:update | multipart file, department_id, expected_version |
| GET /download/{id} | documents:update | department_id; restricted original-file download |
| POST /{id}/permissions | documents:manage_permissions | department_id + JSON expected_version, department_id, role_id (audience fields in JSON) |
| GET /{id}/permissions | documents:manage_permissions | department_id |
| DELETE /{id}/permissions/{audience_id} | documents:manage_permissions | department_id + JSON expected_version |
| POST /ask | documents:read | strict JSON question, department_id, conversation_id, request_key |

There is no physical document-delete route. Archive preserves data. The archive
permission is registered additively without changing existing role mappings;
department mappings can be assigned explicitly through the approved policy API.
Global Admin baseline mappings require separately reviewed operator enrollment.

Publishing requires a pending, existing expected version and a nonempty audience
list. Each audience object explicitly contains department_id and role_id; null
means ALL for that field. Department AND role must match within a rule; rules are
ORed. Missing audience rows deny access. This does not grant cross-tenant access.
Publication/audience replacement is atomic and concurrent publication is serialized.
Changing a source or revoking scope prevents later delivery of affected results.
Original files are never included in employee citation responses.

## Question/result boundary

/ask is currently disabled by default (503 document_result_integration_unavailable)
before provider dispatch or reservation. Core Gate 4 must supply a trusted factory
that checks conversation ownership/scope and binds the independent Core result
store with source reauthorization. No environment or legacy-tenant fallback exists.
The authenticated caller cannot choose cost, provider receipts or trusted context.

The eventual payload follows app/conversation_store/schema.py: request and
conversation IDs, status, answer, answer_kind, llm_called, sources,
unanswered_departments and handoff_available. Each source includes department,
document/version identity, name, supporting quote and a typed location. Current
legacy chunks use reviewed_text / chunk:<id>; physical page/cell provenance is
pending. A request key is scoped to its owner; replay does not redispatch or debit
again, and changed fingerprints conflict. Uncertain outcomes are not auto-refunded.
No frontend should rely on legacy query parameters or tenant result_payload.

## Errors and frontend configuration

401 invalid/missing identity; 403 insufficient scope; 404 missing authorized object;
409 version/idempotency conflict; 422 invalid strict request; 503 unavailable or
unconfigured integration. Unexpected errors are sanitized; sensitive responses
use no-store. Messages are not guaranteed to all use one language.

Frontend needs its API base URL and authenticated session only. Never send database
URLs, provisioning identities, model keys or trusted role/cost fields to clients.
Development auth is not production SSO. See documents-blocker-repairs.md for actual
verification evidence and backend-release-readiness.md for remaining gates.
