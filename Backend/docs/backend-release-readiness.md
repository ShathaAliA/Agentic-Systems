# Backend Release Readiness — Documents & AI

**Date:** 2026-11-16  
**Branch:** `documents-ai`  
**Status:** ✅ **Documents & AI ready** / 🔴 Core pending (`CREATE EXTENSION` + Legal)

---

## ما هو جاهز (Documents & AI)

| # | البند | الحالة |
|---|---|---|
| 1 | **Document Pipeline** (7 صيغ + OCR عربي + OCR مختلط) | ✅ |
| 2 | **Atomic Upload** (`db.flush()` + commit واحد) | ✅ |
| 3 | **Archive-on-Approve** (RAG يبقى على القديم) | ✅ |
| 4 | **Audience** (`(NULL, NULL)` + dept+role) | ✅ |
| 5 | **U1 Integration** (`run_once` + `result_payload`) | ✅ |
| 6 | **Error Handling** (رسائل عربية آمنة) | ✅ |
| 7 | **Tests** (28 passed: 13 جديد + 15 سابق) | ✅ |
| 8 | **API Contract** (docs/api-contract.md) | ✅ |

---

## ما يتبقى (Core — شذى)

| # | البند | السبب |
|---|---|---|
| 1 | **`CREATE EXTENSION vector`** في `worker.py` | **بند Core** — `tenant_0002` يفشل على `dept_*` جديد |
| 2 | **Legal activation** | `worker.py --retry-failed` (بيئة شذى) |
| 3 | **`tenant-contract.json` `ready: true`** | بعد Legal |
| 4 | **60 سؤالاً + دقة 90%** | بعد Legal + LLM (رصيد) |
| 5 | **Frontend** | يحتاج CORS (بند 3 — شذى) |

---

## ما يتبقى (LLM — رصيد)

| # | البند | السبب |
|---|---|---|
| 1 | **Gemini quota** | **رصيد نفد** — **بديل:** OpenAI أو Ollama |
| 2 | **`generate_answer`** | **يعمل** — **لكن** — **Gemini** — **quota** |

---

## Environment (Production)

**⚠️ For production:**
- `DATABASE_URL` — PostgreSQL central (wadi_db).
- `PROVISIONING_REGISTRY_URL` — registry (worker only).
- `PROVISIONING_ADMIN_URL` — admin (worker only).
- `TENANT_CONTRACT_FILE` — path to contract V1.
- `DEPARTMENT_CREDENTIALS_DIR` — runtime credentials (read-only).
- `LLM_PROVIDER` — `openai` (recommended) / `gemini`.
- `OPENAI_API_KEY` / `GEMINI_API_KEY` — provider.
- `GEMINI_API_KEY` — **⚠️ quota** — **يحتاج** — **رصيد** .

**⚠️ Secrets:** never commit `.env`. Use Docker secrets / K8s secrets / vault.

---

## Docker / Compose

**`Dockerfile`** — exists (FastAPI + uvicorn).  
**`compose.provisioning.yml`** — exists (worker + API).

**⚠️ To run:**
```bash
docker compose -p <project> -f docker-compose.yml -f compose.provisioning.yml up