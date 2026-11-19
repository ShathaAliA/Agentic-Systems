# Core Docker/CI verification — 2026-11-06

Baseline: feature/core-backend 6a5f05e. Local changes only: CI workflow, reusable
container smoke tool, its failure tests and documentation. No push, PR or merge.

## Executed

- Launched Docker Desktop hidden without resetting configuration or changing
  security settings, WSL distributions, socket files or existing volume contents.
- Read the backend error report. Desktop failed while initializing Inference
  manager: the local dockerInference listener path could not be accessed/removed.
  A Docker version probe stalled and was interrupted. No application Compose
  services were started. Docker itself may resume services according to existing
  user settings; this work did not issue any command to start the original stack.
- Ran `python Backend/tools/check-core-container.py`: exited 1 with the bounded
  message `Linux Docker engine unavailable`, before any container creation.
- Ran the complete native Core suite: 235 non-integration and 63 PostgreSQL tests
  passed, no failures/errors/skips. Report:
  `.local/core-tests/b877a05e18b94247a9ab3d7b2ec92780/`.
- After adding two smoke failure-path tests, reran the non-integration suite:
  **237 passed**, no failures/errors/skips. Report:
  `.local/core-tests/b3933d784d2b47e5943d2b2a9897b9c6/`.
  Latest distinct coverage is **237 non-integration + 63 PostgreSQL = 300 cases**;
  these are two runs, not a claim of one 300-case container/CI run.
- Workflow YAML parsed and local checks passed for trigger branches, read-only
  permissions, pinned action SHAs and bounded job durations. This is not actionlint
  or a GitHub-hosted workflow execution.
- Compose config --quiet passed using a process-local non-secret placeholder.
  Git whitespace checks passed; Gitleaks scans of workflow/tools found no leaks.

The PostgreSQL tests use newly created clusters, retained backups and private
credentials. Existing HR/Finance/Legal databases were not queried or modified by
the tests. Real Legal was not freshly queried; its last documented status remains
failed and the example tenant contract remains ready:false. Document/RAG code and
production migrations are unchanged.

## Still pending

Docker image builds, runtime UID/dependency/read-only/tmpfs checks and Docker
PostgreSQL integration tests could not execute without a usable engine. Socket
existence alone does not establish engine availability. No workaround that deletes
the listener artifact, resets Desktop or changes protection was attempted.

The GitHub workflow is prepared locally and has not run remotely. The first
authorized publication must verify hosted Windows PostgreSQL 17+ availability,
Linux dependency installation, image builds and smoke behavior. No artifact uploads,
deployment credentials or production database URLs are configured. Required-check
branch protection is not enabled by adding this file.

This adds independent Core CI preparation; it does not resolve documents-ai's
tenant routing, migration, authorization or source/version integration blockers.
