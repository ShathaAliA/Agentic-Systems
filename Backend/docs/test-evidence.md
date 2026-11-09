# Test Evidence — sync/core-into-documents

**التاريخ:** 2026-11-09  
**الفرع:** `sync/core-into-documents`

---

## Executed (35 passed)

| File | Count |
|---|---|
| `tests/test_no_legacy_session.py` | 3 |
| `tests/test_u1_integration.py` | 3 |
| `tests/test_file_rejection_messages.py` | 4 |
| `tests/test_documents_http.py` | 8 |
| `tests/test_document_audiences.py` | 4 |
| `tests/test_version_pinning.py` | 3 |
| `tests/test_chunk_locations.py` | 2 |
| `tests/test_b2_endpoints.py` | 4 |
| `tests/test_tenant_migration.py` | 4 |

**Total: 35 passed.**

**E2E PoC:** 7 files، 12 docs، 16 chunks، isolation ✅.

**Migrations:** `tenant_0001` → `tenant_0006` (head).

**OpenAPI:** 47 paths (10 documents).

**App startup:** `App OK` (13 route groups via `_IncludedRouter`).

---

## Skipped — Reasons

| Test | Reason |
|---|---|
| `hr_scanned.pdf` (OCR) | Tesseract not installed on Windows |
| `org_chart.png` (OCR) | Same |

Documented in `docs/system-dependencies.md`.

---

## Blocked — Reasons

| Test | Depends on |
|---|---|
| Legal activation | Reviewer decision + `worker.py --retry-failed` |
| `ready: true` in tenant-contract.json | Legal verification |
| 60 questions | Legal + synthetic data |

---

## Reviewer actions needed

1. `worker.py --retry-failed --department-id <legal_id>`
2. `ready: true` in `tenant-contract.json`
3. `tenant_0006` on HR/Finance
4. Tesseract (optional, for OCR)