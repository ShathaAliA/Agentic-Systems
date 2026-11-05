# Core unit and integration testing — Subtask 3.1

This completes the Core testing infrastructure and workflows, not integration of
unfinished document/RAG services. No production service or schema is changed.

## One entry point

From the repository root, with Backend/requirements-test.txt installed in a private
Python environment, Windows PowerShell 7.2+ and PostgreSQL 18 binaries:

```powershell
python Backend/tools/run-core-tests.py --suite all --backend native
```

The current workstation uses `.local/rbac-venv/Scripts/python.exe`; the orchestrator
always passes its own Python executable to native child runners. Override the
PostgreSQL location with `--postgres-bin`. Choose `--suite unit` for no database
infrastructure, or `--suite integration` for isolated PostgreSQL only. Docker mode
uses `--backend docker`, with `--docker` and `--image` overrides. The test image and
postgres:17-alpine must already be available; no production deployment is involved.

The orchestrator runs units then seven explicit PostgreSQL modules: authentication,
departments, users, role_policies, usage, core_access, core_lifecycle. Each module
gets a different fresh cluster because its migration/fixture contract requires an
empty database. Modules are deliberately not combined in one reused cluster.

It continues collecting group results after a failed/blocked group, but exits
nonzero if any group is not successful. Missing or malformed results, all-skipped
suites, skipped isolated tests, pytest errors and cleanup failures cannot become a
green summary. No automatic retries hide concurrency failures.

## Collection and environment safety

Root and Backend pytest configuration both fence discovery to Backend/tests.
Backend carries its own guard so a Docker build containing only Backend remains
safe. Harmless canary tests verify root/Backend collection and refuse explicit
legacy paths before import. Never run the actual legacy scripts to test this fence.

Integration modules need the explicit `--isolated-postgres` option supplied by the
isolated runners in addition to their infrastructure environment and fixture
checks. The original-cluster test is marked `live` and skipped by default, even
with inherited opt-in environment flags. Manual live provisioning requires its
own `--allow-live-provisioning` option, backup acknowledgement and original
preflight; the orchestrator has no live mode and never invokes that workflow.

Child environments remove inherited test opt-ins, database/provider credentials,
provider keys and pytest argument overrides, use APP_ENV=test/AUTH_MODE=disabled,
and disable automatic dotenv loading. Tests inject synthetic users and engines;
no real OpenAI/Gemini calls are made. These are safeguards for the supported entry
points, not a sandbox against deliberately modified test code or arbitrary commands.

## Isolation, recovery and cleanup

Native clusters use random loopback ports, fresh private data directories and
random credentials kept out of command arguments/Git. Before migration or grant
changes the runners save recovery dumps and the fixtures capture owners/ACLs.
FastAPI test credentials remain separate from the isolated migration/bootstrap
identity and have no CREATEDB/CREATEROLE/SUPERUSER privileges.

Native startup attempts enter cleanup even after partial failure. Shutdown must
be confirmed; cleanup failure is a failed command. Containers/clusters and their
private backups are retained, never deleted. No existing HR/Finance database or
Legal registry is a test target. Restrict these private artifacts to the operator
and SYSTEM on Windows, or mode 0700 on other hosts.

## Reports

Aggregate reports live under ignored `.local/core-tests/<run-id>/`: summary.json,
per-group JUnit XML and logs. They record group status, actual counts and command
exit codes. Missing executables/results are blocked; failing assertions or nonzero
exit/cleanup codes fail the group. Console/log copies redact development bearer
tokens, PostgreSQL DSNs, OpenAI-style keys and quoted password/key/token values.
Regression canaries verify redaction. Original per-cluster XML/backups remain in
private runner directories and are not publishable artifacts. Redaction is not an
assertion that arbitrary application output is safe for external publication.

## Added cross-module workflows

1. Create and offline-enroll a synthetic user; activate HR membership; assign a
   deputy role; verify HR access and Finance denial; replace the role policy;
   verify old session rejection/new permission denial; deactivate/reactivate user;
   verify fresh login and audit history.
2. Issue a department-name grant to a new synthetic head; consume it once; run the
   registry worker against an unavailable tenant contract with mocked physical
   provisioning; assert failed status without resource creation; archive without
   restoring the one-department right. The Legal fixture stays failed.
3. Authenticate a new synthetic user; configure budget through Admin API; invoke
   the trusted accounting adapter with a fake provider receipt; replay the same
   request; verify one question/one provider execution, own ledger visibility,
   truthful spend and zero remaining hold.

These do not activate a tenant or prove real document-schema isolation. Required
business migrations and end-to-end document tests remain pending from documents-ai.
See core-testing-verification.md for the final executed results and limitations.
