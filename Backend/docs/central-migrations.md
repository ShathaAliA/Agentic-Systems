# Central migration history

The current single central head is `usage003_merge`. It joins the published
`usage002_result_payload` branch with Core `rbac001` without rewriting either
revision or deleting existing data. The legacy nullable result_payload column
is not mapped or written by runtime code. No old answers are copied or purged.
New results belong only in tenant `question_results` (tenant_0007).

Do not downgrade or apply this to the existing development cluster automatically.
Use the separate migration identity and the fresh-cluster Core runner first.
No API identity receives DDL, CREATEDB, CREATEROLE or SUPERUSER.
See sync-blocker-verification.md for actual executed checks and remaining gates.
