> Historical implementation notes. Current status and mandatory gates: [sync-blocker-verification.md](sync-blocker-verification.md).

# P1 Notes

## 1) Audience: (NULL, NULL)

`tenant_0005_audience_all.py` removes `ck_audience_not_both_null`.

`audience_service.py` — `(NULL, NULL)` visible within dept scope only.

`(NULL, NULL)` does **not** mean cross-dept access.
`authorized_department_session` filters on a single department.

## 2) U1 Real Integration

`app/usage/integration.py` — `run_once` + `AccountingContext` (wraps `app.usage.service`).

`router.py` `ask`:
- `request_key` required.
- `run_once` → `reserve` → `start` → `processor` → `finish`.
- `UNIQUE(user_id, request_key)` prevents double charge.
- `ConfirmedTechnicalFailure` → refund.

Tests: `tests/test_u1_integration.py` (3 passed).

## 3) department_id varchar(36)

`tenant_0006_department_id_36.py` — `ALTER COLUMN` + safety check.

Does not fix `runtime_grant_policy_missing` (fixed by updated `TenantMigrator`).

## 4) contract_version: 1 (int)

`tenant-contract.json` → `"contract_version": 1`.

## 5) required_revision: tenant_0006

`tenant-contract.json` → `"required_revision": "tenant_0006"`.

## 6) table_privileges + sequence_privileges

`tenant-contract.json` → 5 tables، 6 sequences.

`TenantMigrator` (Core) reads them. **Needs reviewer verification.**