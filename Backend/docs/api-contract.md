# API Contract — Documents & AI (for Frontend)

**Version:** 1.0  
**Base URL:** `http://localhost:8000`  
**OpenAPI:** `/openapi.json` (Swagger: `/docs`)

---

## Authentication

**Development (opaque session):**

**Use:** `Authorization: Bearer <token>` on every request.

**⚠️ Not JWT** — opaque session (stored server-side).

---

## Documents Endpoints (10)

| Method | Path | Permission | Body/Query | Response |
|---|---|---|---|---|
| `POST` | `/api/documents/upload` | `documents:upload` | `file` (multipart), `department_id` (form) | `{document_id, chunks_count, doc_version, doc_status}` |
| `GET` | `/api/documents/` | `documents:read` | `department_id` (query) | `{count, documents:[{id, filename, file_type, status, version, upload_date}]}` |
| `GET` | `/api/documents/{id}` | `documents:read` | `department_id` (query) | `{id, filename, file_type, status, version, upload_date}` |
| `PATCH` | `/api/documents/{id}` | `documents:update` | `department_id`, `filename` (optional) | `{status, message, id, filename}` |
| `DELETE` | `/api/documents/{id}` | `documents:delete` | `department_id` | `{status, message}` (archive) |
| `GET` | `/api/documents/download/{id}` | `documents:update` | `department_id` | `FileResponse` (binary) |
| `POST` | `/api/documents/{id}/approve` | `documents:update` | `department_id` | `{status, message}` |
| `POST` | `/api/documents/{id}/revise` | `documents:update` | `department_id`, `new_text` | `{status, message, new_version}` |
| `POST` | `/api/documents/{id}/version` | `documents:update` | `file`, `department_id` | `{status, message, new_version}` |
| `POST` | `/api/documents/ask` | `documents:read` | `query`, `department_id`, `request_key` | `{query, answer, sources:[{document, chunk_id, version_number, location}], request_key, replayed}` |

### Audiences (3)

| Method | Path | Permission |
|---|---|---|
| `POST` | `/api/documents/{id}/permissions` | `documents:manage_permissions` |
| `GET` | `/api/documents/{id}/permissions` | `documents:manage_permissions` |
| `DELETE` | `/api/documents/{id}/permissions/{audience_id}` | `documents:manage_permissions` |

---

## Error Responses (Arabic-safe, no stack traces)

| Status | Meaning | Example `detail` |
|---|---|---|
| **400** | Bad request (file rejected, empty) | `"الملف غير مدعوم أو تالف"` |
| **401** | Unauthenticated | `{"detail": "Not authenticated"}` |
| **403** | Forbidden (cross-dept, no permission) | `{"detail": "Permission denied"}` |
| **404** | Not found | `{"detail": "المستند غير موجود"}` |
| **409** | Conflict (request_key reused) | `{"detail": "request_key_payload_conflict"}` |
| **422** | Validation (Pydantic) | `{"detail": "Invalid request data"}` |
| **500** | Server (generic) | `{"detail": "حدث خطأ غير متوقع"}` |
| **502** | Provider (LLM) | `{"detail": "تعذر الحصول على إجابة من المزود"}` |
| **503** | Service (DB down, U1 refund) | `{"detail": "Service temporarily unavailable"}` |

**⚠️ All errors return Arabic `detail` — no `str(e)` or stack traces.**

---

## Idempotency (`/ask` — `request_key`)

- **Required** — UUID recommended.
- **Same key + same query** → returns stored answer (`replayed: true`).
- **Same key + different query** → `409 request_key_payload_conflict`.
- **No double charge** (U1 — server-side).
- **Technical failure** → refund (`503` — "تم إرجاع الرصيد").

---

## Environment Variables (Frontend — only what you need)

| Variable | Value | Notes |
|---|---|---|
| `BACKEND_URL` | `http://localhost:8000` | API base |
| `AUTH_MODE` | `development` | opaque session (not JWT) |

**⚠️ Frontend does NOT need:** `DATABASE_URL`, `PROVISIONING_*`, `TENANT_CONTRACT_FILE`, `DEPARTMENT_CREDENTIALS_DIR`.

---

## OpenAPI (Swagger)

- **Swagger UI:** `http://localhost:8000/docs`
- **OpenAPI JSON:** `http://localhost:8000/openapi.json`
- **47 paths** — 10 documents + 37 Core (auth, users, departments, usage, roles).

**⚠️ Frontend team:** use `/docs` for interactive testing + `/openapi.json` for codegen (typescript-fetch, openapi-typescript).