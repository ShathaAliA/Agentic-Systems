# Phase 2A verification — 2026-11-11

Status: **steps 1 and 2 verified locally; stopped for review**.

Final isolated security-review run: `309925602b1449428d160f2b5c69f603`, branch `PoC`.
No changes have been pushed. Core application, document/RAG modules, repository
tenant manifest and Phase 1 sources are unchanged.

## Actual results

| Check | Result |
| --- | --- |
| Focused foundation regression suite after security review | 11 passed; one upstream TestClient deprecation warning |
| Docker PostgreSQL/Core verification | 13 groups passed |
| Actual loopback HTTP authentication | Six synthetic users passed |
| Actual HTTP department/role permission matrix | Eight cases passed |
| HTTP test session logout/revocation | All six sessions created by the HTTP smoke test revoked |
| Document processing / LLM or embedding requests | Zero |
| Gitleaks scan of new Phase 2A sources and foundation test | No leaks found |

The full-schema run includes real Core central migrations, tenant provisioning
through the existing Worker and required tenant migrations before activation.
Its two synthetic tenants became active only after the Worker's schema,
connectivity and privilege checks succeeded. They are unrelated to Legal.

The 13 groups include missing/forged identity rejection, expiring logins,
administrator separation, active department RBAC, session revocation, and SQL
privilege checks for central and both tenant application identities. Runtime
identities have no SUPERUSER, CREATEDB, CREATEROLE, replication or BYPASSRLS rights;
other-database CONNECT and schema/role creation are denied. Each tenant
application identity successfully executed a vector L2 distance operation;
empty documents and question-results tables were verified.

Private evidence is in `.local/poc-phase2/RUN_ID/verification.json`,
`http-verification.json`, `resource-safety.json`, backups and owners/ACL snapshots.
These files and generated credentials must remain outside Git.
Generated Docker client/buildx state was also retained privately under .local;
the controller now explicitly directs child build tools to its run-scoped
configuration directory. No actual .env or generated credential file is part
of the source changes.

## Docker repair and resolved startup issues

Docker Desktop initially failed at orphaned Inference/Secrets Engine AF_UNIX
reparse-point sockets. After stopping only validated Docker Desktop processes,
the two ordinary runtime directories were atomically moved to sibling backups.
They were checked to contain zero-byte IPC entries only. This final attempt
also verified that the original paths disappeared and created empty directories
before restarting Desktop. The new engine.sock had a fresh timestamp and
docker info returned linux.

Backup paths and checks are recorded privately in
`.local/poc-phase2/docker-repair-verified.json`. Backups remain intact. No
factory reset, security disabling, global WSL shutdown or data-disk move was
performed. A future unclean shutdown may reproduce the upstream socket issue;
this result proves current operation, not a permanent Docker product fix.

The separate Phase 2A image excludes OCR/model/provider packages, retaining
Core pins, pgvector and the multipart parser needed for FastAPI route
registration. A malformed inline tmpfs list was corrected and regression
checked. Database/tools remain on an internal network; only the API additionally
uses a bridge network with a loopback-only published port. That API bridge
permits outbound traffic; this is not an egress-filtering sandbox. No provider
credentials are forwarded and document/model routes return 503.

A clean rerun reached successful SQL verification but could not create its
API bridge because Docker's default subnet pools were exhausted. Three empty
networks belonging exclusively to stopped **new Phase 2A attempts** were removed
after checking project labels and absence of endpoints. No containers, volumes,
databases or pre-existing networks were removed. API creation and HTTP checks
then passed on the clean rerun. The up command now includes the HTTP smoke check;
network pool capacity remains a host prerequisite for future new runs.

## Preserved resources and limitations

After testing, the new Phase 2A containers were stopped; their volumes and
backups were retained. Metadata comparison against the pre-test inventory
confirmed all **34 prior containers** remained present with unchanged states,
and all **27 prior volumes** remained present. Original database contents were
not accessed. Legal's failed status was not changed; HR, Finance, original
central databases and Phase 1 resources were not used by the test.

The earlier native diagnostic passed 11 identity/SQL groups but lacked pgvector;
it is superseded for these acceptance gates by the full Docker results above.
An earlier full Core unit run reported 296 passed. The later incomplete repeat
is not counted as a new pass; the security review reran 11 focused cases and
the full isolated SQL/HTTP foundation. See SECURITY_REVIEW.md for the findings.

No remaining blocker was observed for approved steps 1 and 2. File ingestion,
OCR, RAG, model quality/cost and Humain compatibility are outside these results
and have not been run. Do not infer document or LLM acceptance from this report.
