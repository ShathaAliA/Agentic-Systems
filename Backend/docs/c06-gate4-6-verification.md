# C06 Gate 4.6 — test tooling, integration contract and final review

Core only, feature/core-backend. No documents-ai change, public route, provider/LLM call or
live HR/Finance/Legal/Central database. Legal remains `failed`. Evidence for the whole Gate 4.3–4.6
series; see also the 4.3, 4.4 and 4.5 verification documents.

## Delivered in 4.6

- Test tooling: the three new groups (`c06_lifecycle`, `c06_security`, `c06_recovery`) are in the
  aggregate runner and both exact PowerShell allowlists; the Docker backend runs the restore drill
  with `c06_recovery`; `Resolve-TestExecutable` no longer breaks when Windows lists both
  `docker.exe` and an extensionless `docker`; the drill passes its throwaway runtime password
  through a private file, not a command argument.
- [Lifecycle integration contract](c06-lifecycle-integration-contract.md) for Documents/HTTP wiring.
- Final review of the Gate 4.3–4.6 changes (below).

## Actual results — 2026-11-19 (Docker runner, PostgreSQL 17.10)

| Group | Passed | Failed / skipped |
| --- | ---: | --- |
| Unit (official runner) | 409 | 1 failed / 13 skipped (known environmental, below) |
| authentication / departments / users / role_policies | 2 / 7 / 5 / 5 | 0 |
| usage / core_access / core_lifecycle | 10 / 6 / 3 | 0 |
| database_isolation / tenant_contract | 19 / 7 | 0 |
| c06_result_store / c06_conversations | 16 / 13 | 0 |
| c06_sealing | 7 | 0 / 1 skipped (native-only restore drill) |
| c06_authorization | 24 | 0 |
| c06_lifecycle (4.3) / c06_security (4.4) / c06_recovery (4.5) | 11 / 20 / 7 | 0 |
| Restore drill verification (restored database) | 5 | 0 |

The official aggregate runner completed unit and the first nine Docker groups, then Docker ran out of
address pools (the runner keeps one network per run by design). The remaining eight groups were run
individually with the same runner script after removing the stopped test networks, and passed.

Known environmental unit failure: `test_default_collection_uses_safe_suite_from_either_directory`
fails because both `backend/` and `Backend/` exist on this case-insensitive checkout, so `conftest`
registers twice. It is unrelated to these changes and was not re-run on a clean checkout.

## Final review notes

- Questions are recorded (intent) before quota/policy admission, so a refused question keeps an
  intent row with no charge and no dispatch. Intentional for auditability; not asserted by a test.
- A processor result not bound to the conversation is never saved and leaves the request
  `uncertain` (no automatic refund, no redispatch).
- Access is checked before dispatch and before delivery; it is not an atomic snapshot.
- The recovery report is owner-scoped; an audited global Admin view is not provided.
- No retention, deletion, encryption-at-rest, key custody or external audit store exists.

## Not verified

PostgreSQL 18.6 and the native restore test; the `c06_0003` to `c06_0004` upgrade with prior rows;
hosted CI (the push will trigger it); end-to-end with a real Documents provider or HTTP layer.

## Tooling note

Each Docker run retains one `ai-auth-network-*` network. After roughly twenty runs Docker reports
`all predefined address pools have been fully subnetted`. Remove only the stopped, runner-labelled
`ai-auth-network-*` networks (never containers or other networks) and rerun.

## Hosted CI, first run on 045e35d

`postgres-isolation` (native runner, includes the new groups) passed. `unit-and-container` failed:
`test_docker_client_command_is_non_root_and_capability_restricted` requires exactly one
`Docker-Checked ... "--rm"` client command in `test-auth-postgres.ps1`, and the restore drill had
added a second. That test is skipped when `pwsh` is missing, so it did not run in the earlier local
runs. The runner now uses one client command executed once, or twice with `-RestoreDrill`. With
`pwsh` available locally the CI unit command gives 422 passed and the one known environmental failure;
the recovery group and restore drill still pass (7 + 5).
