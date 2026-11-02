# AI Platform / Agentic Systems

FastAPI backend in `Backend/`. Central PostgreSQL stores users, department RBAC,
and the database registry. Each department has its own database and restricted
login role. Only the separate provisioning worker creates databases/roles.

- [Day 1 setup, migration interface and worker operation](Backend/docs/provisioning.md)
- [Architecture audit](Backend/docs/provisioning-audit.md)
- [Development verification results and outstanding blockers](Backend/docs/day1-verification.md)

## Tests

From `Backend/`, install `requirements-test.txt`, then run:

```sh
python -m pytest -q -m "not integration"
```

Pytest deliberately collects `Backend/tests/` only. The older `test_*.py` files
at the backend root are manually executed development scripts, some of which
mutate the registry during import. Do not use them as an automatic test suite.

Opt-in live development checks, with preflight, owner/ACL snapshots and backups:

```powershell
./Backend/tools/verify-development.ps1
```

This requires the existing local `ai_platform_postgres`, HR/Finance seed records,
and an already-registered Legal department. It never drops databases. The document
schema contract is currently unavailable, so **Legal must remain failed**, not
active. The supplied tenant contract example intentionally has `ready: false`.
