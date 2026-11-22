# Backend release readiness — documents-ai

2026-11-16. **Synthetic document lifecycle verified; production release blocked.**
Legal must remain failed. No live database activation, ready:true publication,
company-data provider use, branch merge or deployment is authorized here.

## Verified scope

- Core authenticated identity, active department scope and audience authorization.
- Explicit audiences before top-K retrieval; missing policy denies access.
- Real PDF upload/extraction, reviewer-only text view, Arabic correction and audit,
  manual version-checked publication, pgvector retrieval and archive exclusion.
- Original bytes retained and access restricted; exact source reauthorization.
- Immutable legacy tenant results; forward-only Central payload-write fence.
- Fresh tenant_0008 migration/worker checks with a separate extension bootstrap
  identity, restricted runtime role, original-data retention and ACL tests.
- Local evidence before the publication reconciliation: 466 offline tests and
  26 pgvector tests passed. Subsequent tests and hosted CI must be reported for
  their exact heads, not inferred from this baseline. See documents-blocker-repairs.md.

Vectors in these tests are deterministic fixtures. Passing tests do not prove
embedding quality, generated-answer accuracy or production operational readiness.

## Remaining ownership and gates

| Item | Owner / required acceptance |
| --- | --- |
| Trusted conversation/result binding | Wired and tested synthetically: `/ask` runs through Core's `QuestionLifecycleService` with one C06 result authority and Documents' source authorizer (see c06-documents-integration.md). Still disabled unless `C06_RESULT_STORE_URL_FILE` is configured; real tenant PostgreSQL + C06 joint run, Entra and company data remain separate gates |
| Physical source provenance | Documents: immutable versioned page/slide/sheet-cell metadata; do not invent locations from fallback page 1 |
| Legacy Central content | Joint operator review: retain existing rows until an explicit backup, retention and transfer decision; no purge or history rewrite |
| Runtime image / hosted CI | Verify actual production Dockerfile and exact-head jobs; a lightweight local contract-test image is insufficient proof |
| Identity / real data / providers | Separate company approvals, production Entra/RBAC mapping, trusted provider receipts, cost/accuracy/security evaluation |
| Tenant activation | Separate approved plan after migration/ACL/isolation and joint integration acceptance; Legal stays failed throughout this work |

CREATE EXTENSION vector must use a separate bootstrap identity only in the new
approved target database. Do not grant SUPERUSER/CREATEDB/CREATEROLE to FastAPI or
make the provisioning worker privileged to work around a missing extension.
A successful synthetic worker test does not authorize touching Legal, HR or Finance.

## Configuration boundary

.env.example is non-secret and disabled by default. It uses private URL-file
references and separate runtime, migration and provisioning identities. Provisioner
and migration secret files must not be mounted in FastAPI containers. Configure
model pricing/keys privately only when integration is approved; no baked-in rates
or exhausted-quota assumptions establish current provider readiness.

Use only guarded fresh-cluster runners for tests. Do not launch an existing Compose
project, connect a test fixture to a fixed development port, run retry-failed for
Legal, change real manifests to ready, or delete databases as a testing shortcut.
Current API shapes are documented in api-contract.md and the executable OpenAPI.
