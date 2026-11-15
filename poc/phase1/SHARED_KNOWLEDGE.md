# Cross-department public knowledge — proposal, not implemented

Requirement: an employee in a different department can use an explicitly public
document such as D1-v2. This does **not** authorize a connection to the originating
department database. Audiences express information access; database credentials
remain tenant-scoped. TC18 records the desired outcome and an explicit execution
blocker. Phase 1 does not claim this capability works.

## Proposed boundary

1. A head/deputy explicitly approves publication to all departments. The reviewed
   audience still includes allowed roles: department AND role, with separate '*'
   wildcards. A public-department document is not necessarily public to all roles.
2. A trusted publisher emits an idempotent outbox event for an immutable approved
   version. It exports only approved text/chunks and safe source references, not
   originals, internal paths, credentials, private revisions or audit data.
3. A separate shared-knowledge store holds the approved projection, with its own
   restricted service identity. It is not a SQL view/join into tenant databases.
   No employee or tenant application role receives cross-database CONNECT grants.
4. A knowledge gateway validates Core identity and current permissions, reads the
   employee's authorized tenant through its existing boundary, and asks the shared
   service for eligible public knowledge. It merges results after authorization.
5. Every item carries originating document/version, policy generation, publication
   status, hash and real source location. The gateway checks current publication
   eligibility before retrieval and before exposing evidence/answers. A store or
   policy-service failure denies shared access; there is no direct-tenant fallback.

## Revocation and consistency

Publication changes first advance an authoritative policy generation/deny marker.
Old projections and caches cannot pass eligibility checks afterward. The outbox
updates/removes the shared projection; only the matching approved generation is
servable. Retries are idempotent. Archiving D1-v1 and publishing D1-v2 invalidates
the old searchable projection while retaining its provenance for audit. Historical
source references never grant newly unauthorized users access to original files.

## Options considered

- **Proposed:** separate approved shared projection/gateway. Clear credential
  boundary, with additional publication/revocation machinery and tests required.
- Request-time service-to-service federation into originating tenants: possible
  only with approved narrowly scoped service authorization, but increases fan-out,
  latency and operational complexity. Not implemented or approved by this PoC.
- Shared credentials, dynamic search_path, PUBLIC CONNECT, database links, or giving
  an outsider the origin tenant's runtime identity: rejected because they bypass
  the intended isolation boundary.

## Required proof before enabling TC18

- Outsider can answer from the D1-v2 safe projection with correct source/version.
- Actual outsider/tenant SQL connection to origin database is denied.
- The same outsider cannot retrieve D2 or D4, or see private source metadata.
- Department wildcard + role restriction and role wildcard + department restriction
  are tested independently, including an unauthorized role in an allowed department.
- Archive, revocation, version switch, stale-cache and publication-failure races fail
  closed without exposing old or unauthorized data.
- Originals remain protected and are not copied into the shared store.

This design requires joint Core/documents approval and a later implementation task.
No shared store, federation service, RAG query or cross-tenant grant is created in
Phase 1. It does not unblock Legal activation or certify document integration.
