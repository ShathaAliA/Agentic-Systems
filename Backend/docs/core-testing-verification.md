# Subtask 3.1 verification — 2026-11-05

Branch: feature/core-backend, based on published 97fe2b5. This work changes test
configuration, test tooling, synthetic workflows and documentation only. No Core
application, document/RAG, model or migration code was changed.

## Executed environment and results

Windows, PowerShell 7, Python 3.13.14, PostgreSQL 18.6. The final supported command:

```powershell
.local/rbac-venv/Scripts/python.exe Backend/tools/run-core-tests.py --suite all --backend native
```

| Group | Passed |
| --- | ---: |
| Non-integration unit/API and test-safety cases | 187 |
| PostgreSQL authentication | 2 |
| PostgreSQL departments | 7 |
| PostgreSQL users | 5 |
| PostgreSQL role policies | 5 |
| PostgreSQL usage | 9 |
| PostgreSQL Core access | 6 |
| PostgreSQL new cross-module lifecycles | 3 |
| Total isolated PostgreSQL | 37 |

All executed groups had zero test errors/failures/skips in their result reports.
The live original-cluster test was excluded, not counted as successful. The
existing Starlette/httpx deprecation warning remains; it is not an application
failure. Python AST and whitespace checks passed.

The twelve test-execution cases verify default discovery from both working
directories and a Backend-only build context, explicit-path pre-import rejection
using a harmless canary, inherited opt-in rejection, environment scrubbing,
redaction, failure/skip/empty-result handling and nonzero aggregate exit with
continued group reporting. No actual legacy provisioning script was executed.

Three new PostgreSQL workflows span user creation/enrollment, membership and role
policy changes, audit/session effects, one-time head creation rights, unavailable
schema failure/archival and trusted synthetic accounting receipts with replay.
The physical provisioning adapter is mocked in the unavailable-contract workflow;
it must never be called. No real provider or tenant database is created there.

## Recovery, reporting and safety

Each of the seven PostgreSQL groups used a distinct new loopback cluster, with
private backup archives and pre-grant owners/ACL snapshots. A separate limited API
identity executed application operations. Startup attempts enter cleanup and an
unconfirmed shutdown makes the command fail. Final native clusters were stopped
and retained, without database deletion.

The aggregate run writes private logs, redacted JUnit XML and summary.json under
ignored .local/core-tests. Private per-cluster reports, password files and dumps
remain under .local/native-auth-tests. No report or secret is committed.

The original development verification script's pytest invocation was updated to
select only its live test and require the new explicit opt-in. That script was
not run. This prevents it silently reporting success after all live tests skip;
it does not authorize original-cluster operations in the Core runner.

## Pending and deliberately unexecuted

- The Docker runner was updated for the root/Backend pytest guards, explicit
  isolated option, XML reporting and cleanup result. Docker mode/PostgreSQL 17
  parity was not executed in this run; native PostgreSQL 18 is the verified path.
- No command targeted original HR/Finance data, provisioning credentials or Legal
  registration. Legal's last directly verified real state is failed; this report
  does not claim a fresh original-stack status query.
- The real documents-ai contract/migrations and protected document interfaces are
  still pending. Passing mocked Core workflows does not establish document-level
  permissions, real tenant document-schema isolation, OCR or RAG quality.
- 3.2 security/isolation completion, 3.3 jointly accepted document contract, 3.4
  deployment work and 3.5 PR preparation remain separate. No push, merge, new PR,
  deployment or subsequent subtask was performed.

This completes 3.1 for Core; full-platform integration is explicitly pending.
