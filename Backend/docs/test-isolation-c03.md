# C03 — Core test execution isolation

Scope: test tools only on `feature/core-backend`. No application authorization,
document/RAG module, tenant schema, provisioning behavior or existing database
is changed. Legal remains excluded/failed by policy; its live state was not
queried. Existing HR and Finance databases were not contacted.

## Changes

- `test-environment-policy.json` is a non-secret policy shared by Python and
  PowerShell. Runners remove inherited authentication overrides, database and
  provider configuration, opt-ins and credential-file references. Defaults disable
  authentication and dotenv for test execution. PowerShell direct runners restore
  the caller's environment after infrastructure work, including failure paths.
- The Python report-directory helper honors `--pwsh` and passes a sanitized
  environment. Failure to establish a private report directory returns a generic
  nonzero result before spawning tests. No automatic ACL relaxation is used.
- Native tools are resolved before allocating state. State must be a uniquely
  named direct child of the appropriate private test area; linked paths and
  traversal are refused. Shutdown checks the owned data directory's server
  status before/after stopping it. Startup failure does not imply a successful
  shutdown. Backups and stopped data directories remain available for recovery.
- Docker uses a local socket only; `DOCKER_CONTEXT` precedence over `DOCKER_HOST`
  is checked. Test networks remain internal with no published ports. The test
  client has explicit non-root UID/GID, all capabilities dropped and
  `no-new-privileges`. On Unix a root operator is rejected rather than weakening
  private bind-mount permissions. Windows uses UID/GID 10001:10001.
- Docker resources carry a per-run label. Cleanup stops the PostgreSQL test
  container only after validating its ownership label and confirms it is stopped.
  Ambiguous creation/ownership/shutdown yields failure, not a success message.
  PostgreSQL containers, networks, data and backups are retained; the disposable
  test client uses `--rm`. No deletion of a database or existing resource is added.

## Verification

From repository root with the test Python environment and PowerShell 7.2+:

```powershell
python -m pytest -q Backend/tests/test_test_execution.py Backend/tests/test_test_isolation_tools.py -p no:cacheprovider --tb=short
python Backend/tools/run-core-tests.py --suite unit --pwsh '<PowerShell executable>'
./Backend/tools/test-auth-postgres-native.ps1 -Python '<absolute test Python executable>' -TestFile tests/test_authentication_postgres.py
```

The complete Core suite passed **340 tests**, zero failures/errors/skips. The final focused safety suite passed **27 tests**.
Synthetic safety coverage executes PowerShell helpers and mocked Docker
commands, including environment restoration, local endpoint and context checks,
UID restrictions, path validation, ownership mismatch, confirmed shutdown,
configured PowerShell and safe preflight errors. No Docker engine is contacted
by those synthetic tests. One test assertion was corrected to distinguish the
pytest `-p` plugin option from a Docker published-port option.

Native PostgreSQL authentication verification passed **2 tests** on a fresh
PostgreSQL 18.6 cluster with random loopback port and synthetic fixtures. The
runner backed it up before migrations/role changes, confirmed shutdown, and
retained private recovery artifacts under ignored `.local/native-auth-tests/`.
No existing cluster or container was used. The selected integration group is
authentication only, not a claim that all PostgreSQL groups ran in this batch.

## Limits and integration handoff

Real Docker execution, Linux UID/bind-mount behavior on an engine, container
builds and hosted CI were **not executed** here. Mock tests validate the generated
commands and safety decisions, not Docker Desktop functionality. Remote/TCP
Docker engines are deliberately refused. If the installed tools, ACLs, endpoint
or mount permissions do not satisfy the policy, fix the environment explicitly;
do not fall back to root or world-readable secrets. Terminating the whole runner
process forcibly can prevent `finally` cleanup; retained artifacts support manual
inspection of its uniquely owned resources.

No document test groups or pgvector images are imported from PoC. Those require
the later documents-ai integration. The shared policy/helper files must travel
with the test runners and be present in a test image. They are not runtime API
credentials or .env files. Existing secret redaction of aggregate reports remains
enabled; this is not a universal secret detector or full deployment audit.

Scoped pre-commit review found zero selected secret-pattern matches in code/test
additions and no staged actual .env, private recovery artifact or application
module change. This is a pattern/manual review, not a full dependency audit.
