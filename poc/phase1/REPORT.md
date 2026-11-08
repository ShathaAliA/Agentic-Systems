# PoC Phase 1 results — 2026-11-08

Branch baseline: feature/core-backend 763055b. Scope stops at infrastructure and
synthetic data; no real processing, OCR pipeline, embeddings, retrieval, LLM or API
provider calls. RAG answers, accuracy, latency and provider cost are not measured.

## Delivered

- Seven actual files: D1-v1/D1-v2 digital PDFs, image-only scanned PDF D2, XLSX D3,
  DOCX D4, Markdown D5 and digital PDF D6. Arabic D2 rendering visually checked.
- ground-truth.json: six logical documents/seven immutable artifact versions,
  SHA-256 and byte sizes, authored policy-body text, audience/status, exact evidence
  quotes/locations, five synthetic user profiles and all twenty expected cases.
  Presentation titles/watermarks are outside the expected policy-body text.
- Fresh private account passwords, Argon2 credentials and isolated bootstrap/reader
  database identities. No credentials are included in Git, fixtures or this report.
- Standalone Compose with private network/no ports/no external volumes, generated
  UUID resource names and own empty Docker client configuration. PostgreSQL image
  is pinned to the digest of the image used by the successful Docker verification.
- Persisted D1 history: v1 published/v2 draft → v1 archived/v2 published in one
  transaction, retaining both artifacts and before/after events.
- SHARED_KNOWLEDGE.md proposes an approved shared projection/gateway. TC18 is
  explicitly blocked on that future service; no tenant isolation bypass exists.

## Tests actually executed

1. Phase 1 pytest suite: **41 passed**, zero failures/skips. Five non-failing SWIG
   deprecation warnings from PyMuPDF were emitted. This checks file structure,
   authored truth, evidence resolution, hashes, audiences, twenty scenario fixtures,
   Compose boundaries, run ID guards and refusal to seed existing resources.
2. Native PostgreSQL 18 fresh-cluster setup/verification: **passed**. Final ACL
   checks verified under private run `8f1a58dd47bf414d91d4f4b462744906`.
3. Docker PostgreSQL 17 startup, bootstrap, verification, repeat read-only verify,
   and shutdown: **passed**, run `74499074694b447ba9d5c4799e1a5ae9`.
4. Both backends verified five users, seven catalog entries, D1 event history,
   no administrative role flags or memberships, no schema/database DDL rights,
   actual rejected cross-database connections in both directions, registry-reader
   denial of password hashes, and denial of tenant-reader writes/deletes.
5. Baseline dump/global-role-without-password/owner-ACL artifacts were captured
   before role/schema changes. New clusters/services were stopped; artifacts and
   PoC volumes were retained, not deleted. No original application data was used.

Private evidence: `.local/poc-phase1/<run-id>/verification.json`, run.json,
before.dump, globals-before.sql and owners-acls-before.json. No recovery artifacts
or account credentials belong in commits or externally shared reports.

## Issues encountered and fixed within setup tooling

- First native attempt retained inherited service pipe handles on Windows. Only
  its new cluster was stopped. Service control now uses private log output/closed
  stdin; command capture uses temporary output files. Fresh retries passed.
- An empty private Docker client config did not discover the installed Compose
  plugin. The controller now includes only the bundled plugin directory derived
  from the Docker executable, without loading user registry credentials or contexts.
  The final private-client Docker run and stop passed.
- Earlier diagnostic runs were retained privately: native d7506f519bd4408480f0a709f4640075
  (initial attempt, cluster stopped), native c2bc8e9f47de4e598cc14b8a091f7a3c
  (passed/stopped), Docker e1a05e7220134f4b90f0826c0473e080 (passed/stopped).

## Limits and review gate

No Core authentication API, real document model or production tenant migration is
claimed. The fixture audience oracle is not a deployed authorization service.
No shared-knowledge service is implemented and no twenty-case answer evaluation
was run: fixture rows remain `not_run_phase1`, and reports record zero RAG cases.
DOCX uses paragraph locations, XLSX uses real cells, and image-only PDF text still
requires later OCR. Synthetic extraction quality is not inferred from setup success.

No existing Legal, HR, Finance or central development database, secret file or
volume was selected or modified. Only newly generated PoC secrets/resources are
used. Real Legal was not queried; its last documented status remains failed. No
documents-ai/RAG code, branch merge, push or deployment was performed.

Stop here for Phase 1 review before any document processing or model request.
