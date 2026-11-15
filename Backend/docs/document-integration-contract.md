# Core / documents integration contract — proposal V1

Status: **Core proposal, not jointly approved**. Reviewed documents-ai snapshot:
`b66df78`; Core baseline: `8fb8d01`. This document and the DTOs under
`Backend/contracts/` do not mount or implement document or RAG endpoints.
Legal remains failed. Existing HR/Finance databases are outside this work.

## Ownership and release gates

| Owner | Required deliverable | Acceptance evidence |
| --- | --- | --- |
| Core | V1 contract loader, canonical fingerprint, explicit grant forwarding | Unit and isolated PostgreSQL contract tests |
| Document developer, P0 | Restore rejection of privileged runtime identities; documents-ai currently changes the rejection to a warning | Privileged runtime connection rejected before serving requests |
| Document developer, P0 | Restore established central migration history and a coherent graph; six historical migrations are deleted and users001 references missing dept001 | Fresh central upgrade and existing-Core upgrade both pass without rewriting applied history |
| Document developer | Business migrations descending from tenant_0001, reviewed table/sequence policy | Fresh tenant upgrade, required objects, identity, connectivity and least-privilege/isolation tests |
| Document developer | Authorized tenant sessions, audience filtering, review/version/source lifecycle | Independent cross-department, role, original-access and publication tests |
| Both | Review proposed API shapes and operational policy | Recorded agreement plus end-to-end tests; no agreement is implied here |

The two P0 fixes remain with the document developer. Core does not merge, patch or
execute the documents-ai branch. The reviewed branch already adds authentication,
department permission checks, UUID upload names and a 20 MB limit; these are useful
progress but do not establish tenant storage or document audience isolation.

## Identity and storage boundary

Reuse Core `get_current_user` and fresh RBAC checks. Reject unknown/inactive users;
never accept identity, roles, authorization decisions or usage costs from bodies or
unverified headers. A department selector is input to authorization, not proof of it.
Resolve the active department registry only after authorization, then use its
restricted tenant connection. Keep central user/RBAC transactions separate from
tenant document transactions; do not use central `get_db`/Base for document data.
Never expose provisioning URLs, credential references, raw provider exceptions or
retrieved context in responses or logs. Apply private/no-store response handling to
the eventual document route prefix as well as existing Core routes.

Document audience requires **department AND role**, each with an explicit all
option. Apply it before retrieval and again before returning source excerpts.
Original files are accessible only to the owning department head/deputy and system
Admin; ordinary documents:read permission is insufficient. Employee citations
contain an authorized excerpt, document name, immutable version and location,
without an original-file URL. Table grants do not implement audience restrictions.

## Proposed API surface (paths require joint agreement)

| Operation | Proposed route | Required behavior |
| --- | --- | --- |
| Upload | POST /departments/{id}/documents | Authorized head/deputy/Admin; multipart upload; server-assigned identity; validate detected type, extension and parser structure |
| Review | GET /departments/{id}/documents/{document_id}/review | Authorized reviewer; extracted text, structured tables, source locations and processing errors |
| Correct | POST /departments/{id}/documents/{document_id}/corrections | Append before/after, actor and time; preserve original bytes |
| Publish | POST /departments/{id}/documents/{document_id}/publish | Explicit reviewed version and audience; only approved content searchable |
| Archive | POST /departments/{id}/documents/{document_id}/archive | Preserve versions and audit; no physical delete endpoint |
| Original | GET /departments/{id}/documents/{document_id}/original | Separate restricted original-file gate and tenant/object authorization |
| Ask | POST /departments/{id}/questions | Validated identity, audience-filtered evidence, accounting reservation; DTO proposal below |

`contracts/document_api_draft.py` and `document-api-examples.json` define testable
question/answer proposals. Positive integer document IDs reflect the current model;
opaque version IDs are proposed and require document-developer agreement. Sources
include page, slide, sheet/cell or equivalent stable location. Answer statuses are
answered, no_answer, partial and conflict. Supported answers require evidence;
conflicts identify both sources without choosing a winner. Partial answers identify
unanswered departments. Human handoff is a separate service, not implemented here.

Supported inputs: PDF (digital/scanned), DOC/DOCX, MD, CSV, XLSX, PNG/JPG and PPTX.
Initial limits: 20 MB/file and 100 PDF pages; Arabic content. Spreadsheet lookup
does not promise calculations. Extraction preserves table rows/columns and source
locations, records failures, and requires review before indexing/publication.
Original storage, corrections, versions, archives and audit remain document-owned.
Chat messages remain immutable and cannot be deleted, including by Admin.

## Accounting boundary

Integrate with U1 using authenticated identity and an idempotent request key scoped
to that identity. Charge one processed employee question even for no_answer;
orchestration, conversion summaries and human replies add no question charge.
Refund technical failures through the trusted server workflow. Clients cannot
provide provider cost, token counts, a privileged claim or retrieved context.
Monthly credits do not roll over; enforce daily/rate/company-budget limits too.
Provider usage receipts and actual costs must come from a trusted adapter, not
estimated client claims. Real provider integration remains pending; reconcile
OpenAI configuration with documents-ai's provider choice before acceptance.

## Operator-owned tenant contract V1

The runtime example remains `ready:false`. Do not set it true to work around a
missing schema. Use `contracts/tenant-v1.schema.json` for structural tooling;
the Python loader additionally checks semantic policy coherence.

All fields except pending are required: contract_version=1, ready boolean,
required_revision (pinned revision or null while unready), required_tables,
table_privileges and sequence_privileges. Names are validated lowercase public
objects. JSON contains no credentials, database URLs, migration paths or commands.
Only operator-controlled migration code runs; no path in the JSON selects code.
The file is UTF-8 JSON, at most 64 KiB, with duplicate keys and extra fields rejected.

A ready contract requires _provisioning_identity, a business table, an exact
revision descending from tenant_0001, and explicit readable policy for every
declared business table. Infrastructure metadata stays SELECT-only. Business
operations are limited to SELECT/INSERT/UPDATE/DELETE; sequence operations to
USAGE/SELECT. Actual object existence and effective permissions are checked against
PostgreSQL. Unlisted objects receive no implicit rights. There is no ready
foundation-only file contract; a trusted direct constructor exception exists solely
for infrastructure tests and is not exposed as a JSON/CLI option.

The worker forwards the same policy to grant reconciliation and verification.
Its fingerprint includes version, readiness, revision, tables and both privilege
maps; list/map order and pending notes do not change it. Changing a policy requires
reverification before active status; it does not reset retry limits. Policies are
snapshotted so caller mutation cannot change the verified contract. Migration
files at an existing revision must remain immutable: this fingerprint does not
hash migration source code.

Unreadable, invalid, unsupported or unready contracts become unavailable and fail
closed through the worker, clearing verified readiness when reconciled. Bounded
errors include tenant_contract_unreadable, tenant_contract_invalid,
tenant_contract_version_unsupported, tenant_contract_unavailable and
migration_revision_not_descendant. Existing required-object, policy and permission
errors also remain bounded. No automatic repair of unrelated owners or ACLs occurs.

## Joint acceptance still pending

Document developer supplies real tenant migrations and an explicit privilege map;
Core reviews them without replacing document models. Run them first on backed-up,
fresh isolated infrastructure. Prove central-history compatibility, authenticated
tenant routing, HR/Finance separation, audience denial, metadata immutability,
original access restrictions, source/version integrity and failure rollback.
Only then review activation separately. Synthetic contract tests cannot authorize
Legal activation or certify document/RAG functionality.
