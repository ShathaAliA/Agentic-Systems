# Central Migrations Reconciliation

## Status
- Central Alembic revisions (from `feature/core-backend`):
  - `tenant_0001_documents_tables` → **removed** (legacy, was in old documents-ai)
  - Replaced by `c001` … `usage001` (Core central schema: users, departments, RBAC, usage)
  - `p001_provisioning_attempts`, `p002_verified_contract` (registry)

## Verified
- Fresh central DB created (`wadi_db_test`) and upgraded to `head`: