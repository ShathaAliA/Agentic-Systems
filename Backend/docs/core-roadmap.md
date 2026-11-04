# Corrected core backend roadmap

Tracking correction: question credits, usage and company budget are independent
task **U1**, not Subtask 2.4. No code or database changes accompany this correction.
Previously recorded migration IDs and commit history are retained.

## Day 1 — Department Provisioning

| Task | Status |
| --- | --- |
| 1.1 Architecture/code review | Implemented and documented |
| 1.2 Separate provisioning worker | Implemented |
| 1.3 Restricted database/role creation | Implemented and tested |
| 1.4 Status/retry/failure handling | Implemented and tested |
| 1.5 Legal provisioning/isolation | Isolation tested; final activation blocked by real document tenant contract |

Legal remains failed. Test schema contracts do not establish real document readiness.

## Day 2 — Authentication & Core APIs

| Task | Status |
| --- | --- |
| 2.1 Secure development authentication | Implemented and published |
| 2.2 Departments APIs | Implemented and published |
| 2.3 Users & Memberships APIs | Implemented and published; also includes approved-role assignment/revocation |
| 2.4 Roles & Permissions APIs | Implemented locally; verification and limits in role-permissions.md |
| 2.5 Endpoint protection and permission tests | Applied to existing Core routes; final cross-route matrix remains to be consolidated |

## Day 3 — Testing, Integration & Deployment

| Task | Status |
| --- | --- |
| 3.1 Unit/integration tests | Existing Core suites implemented; final integration remains |
| 3.2 PostgreSQL isolation/security | Core tests implemented; real document-schema integration pending |
| 3.3 API contract with document developer | Local contracts/handoff notes exist; jointly verified agreement pending |
| 3.4 Docker/environment/README | Development setup documented; production activation pending |
| 3.5 Code review/Pull Request | Earlier Core commits reviewed/published; no PR created |

## Independent U1 — credits, usage and company budget

Implemented locally with six unpushed commits based on `ef4d30a`. Recorded validation:
130 non-integration tests, 23 PostgreSQL integration tests. This is a tested
accounting foundation with fake processing; real provider/RAG/billing integration
and original-stack activation are pending. See `usage.md` and `usage-verification.md`.

## Approved completion of 2.4

Existing `/admin/roles` and user-role assignment/revocation APIs remain intact.
Add Admin-only permission catalog, role detail and role-to-permission views, then
controlled editing of approved department-role permission mappings. No arbitrary
role/permission definitions, scope changes, global-admin policy edits or document/RAG
module changes are included.

Proposed endpoints:

- `GET /admin/permissions`: stable paginated permission catalog.
- `GET /admin/roles/{role_id}`: approved role details and scope.
- `GET /admin/roles/{role_id}/permissions`: role permission mappings and policy version.
- `PUT /admin/roles/{role_id}/permissions`: replace a permitted department-role
  permission set atomically, with expected-version conflict detection.
- `GET /admin/roles/{role_id}/permissions/audit`: append-only policy-change history.

Before implementation, inventory current codes and explicitly classify existing
permissions into allowed department-role mappings and immutable system capabilities.
Do not invent document permissions or enable physical deletion. Global Admin
permissions and management gates such as `users:manage`/`departments:manage` remain
uneditable through these APIs. Employee mappings must remain within their approved
read-only set; manager/deputy mappings within the approved department set. Unknown
roles/codes or out-of-matrix mappings deny by default.

Require the existing active, enrolled global Admin with `users:manage`. Prevent
self-escalation through policy editing by refusing mapping changes when the actor
holds the target department role; use another authorized Admin. Role IDs/scopes and
ownership remain immutable. Policy changes cannot create a department or bypass the
one-head ownership ledger, active membership or archived-department guards.

Use a shared transaction/lock order compatible with user-administration writes,
fresh authorization after lock acquisition, expected versions and atomic audit.
Invalidate affected development sessions on real policy changes; never on no-ops.
Check effects on all role holders, not only one user, because mappings are shared.
Additive central migrations only, tested first on isolated PostgreSQL.

Acceptance: reject unverified/non-Admin writes, unknown permissions, global capability
leakage, scope changes, self-escalation and stale policy versions. Protect Admin
availability and ensure revoked permission stops subsequent authorized access.
Concurrent updates cannot silently overwrite one another; audit failure rolls back
mapping and session changes. Existing 2.1–2.3/U1 tests remain passing; include role,
department and archival isolation tests. Swagger and setup docs reflect final APIs.

Implementation sequence: inventory/matrix; central policy version/audit interface;
read endpoints; transactional controlled updates; unit/isolated PostgreSQL tests;
documentation and small local review commits. No merge, push, original database
migrations/grants, Legal activation or new provider calls are authorized by this plan.
