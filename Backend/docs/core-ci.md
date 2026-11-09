# Core CI — local preparation, 2026-11-06

The workflow in .github/workflows/core.yml runs on pushes to feature/core-backend,
pull requests targeting Backend and manual dispatch. Repository permissions are
read-only. It uses no repository secrets, production credentials or original
department databases, and performs no deployment or provisioning of Legal.

The Linux job runs non-integration tests, builds runtime/test images and checks
process health in a nonroot container with a read-only filesystem, dropped
capabilities and no-new-privileges. It also verifies pytest is absent from runtime.
This process-health check does not establish database or tenant readiness.

The Windows job uses available hosted PostgreSQL 17+ binaries with the existing native
isolated runner. It fails explicitly if those binaries are unavailable. Each test
group creates its own fresh cluster. No private recovery artifacts are uploaded.
Hosted runner binary availability and version must be verified in the first run.
Actions are pinned to SHAs resolved from their official v4/v5 refs on 2026-11-06.
Checkout does not persist credentials. No CI reports or private backups are uploaded.

Local validation and actual test results are in core-ci-verification.md.
Docker Desktop was launched hidden without resetting configuration or touching
volumes. Its backend entered an error state while initializing Inference manager:
the local dockerInference listener path could not be removed/accessed. Some pipe
names can exist in this error state without a usable Linux engine. The bounded
smoke preflight returned Linux Docker engine unavailable and created no container.
No socket file was deleted, settings reset, security protection disabled or WSL
distribution modified. Image builds/runtime smoke execution remain unverified.
GitHub job execution has not occurred because these changes are local.
The workflow is prepared locally; publishing/running it requires an authorized
push. It is not yet a required branch-protection check.

Once the engine is repaired, build both image targets as documented in
core-containers.md, then run:

```powershell
python Backend/tools/check-core-container.py
python Backend/tools/run-core-tests.py --suite integration --backend docker
```

The smoke tool creates a UUID-named, volume-free container with no network or
published ports, and removes only that container. It verifies liveness, UID,
absence of pytest, writable tmpfs and read-only-root configuration. It does not
establish tenant/database readiness or external secret-file ownership compatibility.

Legal remains unactivated, with last documented state failed. No original
HR/Finance/Legal database or document/RAG implementation is changed.
