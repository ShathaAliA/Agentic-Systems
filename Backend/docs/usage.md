# Independent task U1: question credits, usage and company budget

The [internal question adapter contract](u1-question-adapter.md) adds a reusable
RBAC-checked boundary around this foundation. It does not mount a question route
or store answers; connecting documents-ai and its authorized result store remains
joint integration work.

Tracking correction: this work was originally labeled Subtask 2.4. It is now U1;
the original 2.4 is Roles & Permissions APIs. Code, migration `usage001` and the
six existing local commits retain their technical identities; history is not rewritten.

Central accounting foundation only. It imports no document/RAG/provider SDK and
makes no external model calls. There is no public question-processing or billing
mutation endpoint. Real LLM binding and human-chat/transfer integration remain later
work. See [executed verification](usage-verification.md).

## Policy and units

The policy starts **unconfigured**, with no default allowance or free fallback.
An enrolled global Admin with `users:manage` must explicitly configure it. All
authenticated roles, including Admin, are subject to admission limits when asking
questions; Admin permission does not bypass accounting.

| Policy field | Meaning |
| --- | --- |
| `enabled` | Accept new question reservations; existing reservations remain honored |
| `timezone` | IANA company timezone; fixed after the first request |
| `monthly_questions` | Calendar-month allowance per user, without rollover |
| `daily_questions` | Calendar-day cap per user |
| `rate_count`, `rate_seconds` | Per-user rolling-window admission limit |
| `monthly_budget_micro_usd` | Company calendar-month money cap |

Limits take effect immediately for new admissions. Changing them does not reset
consumption, erase spend, or release outstanding holds. Lowering a cap below current
commitments blocks further admission and reports the deficit. Zero values disable
the corresponding allowance; a zero company budget blocks new questions even with
a zero-cost quote. Timezone changes after usage return 409, including after refunds.
This avoids moving users between period boundaries to reset balances.
Policy PUT also requires `expected_version`: zero for first configuration, then
the version returned by GET. A stale concurrent update returns 409 rather than
silently overwriting newer limits. The version is not a client-controlled reset.

Money uses integers: one USD equals 1,000,000 micro-USD. No floating point, exchange
rates, model prices or live provider quotations are supplied. Server integration
must compute a conservative total envelope from reviewed model/pricing/token/step
limits. The question envelope includes paid routing, section agents and any summary
performed within that processing job. Input bounds are validation guards, not trial
allowance recommendations. Admin chooses the actual limits after measurement.

Question periods are captured at admission in the company timezone. Refunds reverse
that original period and never grant extra credits to a new month/day. Final provider
receipts are charged to the month they are observed by the trusted server clock;
unverified provider/client timestamps cannot backdate accounting. Old outstanding
money holds encumber the current company budget across month boundaries. This
internal observation convention is not a provider invoice reconciliation system.

## State and safety invariants

`reserved -> running -> completed/refunded`, with `uncertain` for unresolved work.
An answered or unanswered question consumes one credit. Technical failure returns
that credit only after all authorized attempts have authoritative terminal receipts.
Pre-start cancellation also returns it. Refunded admissions still count toward the
rate window, preventing a technical-refund loop from generating unlimited calls.
Rejected admissions never start a provider operation.

Every accounting mutation uses a shared PostgreSQL advisory transaction lock and
appends ledger entries in the same transaction. Question consumption, observed spend
and total outstanding holds derive from the append-only ledger. Request/attempt
rows track lifecycle, replay and verification state; they are not editable through
HTTP. The API identity must have no ledger/audit UPDATE or DELETE permission.

A request key is unique per user and must be stable on delivery retries. The
server-generated SHA-256 fingerprint and quoted envelope must match on replay;
otherwise return 409. A replay never consumes another credit or invokes the
processor again, even after a month boundary. Use a fresh key for a genuinely new
question. No prompts, document text or answer content are stored in these tables.
Outcomes are trusted server decisions: client/model text claiming a technical
failure is not evidence authorizing a refund.

Execution starts exactly once with a private claim. Each paid provider attempt is
authorized **before dispatch**, using its own stable key and capped portion of the
question envelope. The sum of observed cost and pending attempt caps cannot exceed
the envelope when authorizing more work. Attempt replays return `created=False` and
must never dispatch again. Provider receipt references are unique per provider
across all requests, including zero-cost receipts. Duplicate identical settlements
and completion/refund callbacks are idempotent; conflicting ones return 409.

The adapter must verify actual receipts and prices. These functions validate
accounting structure, fencing and idempotency, not provider signatures. If a verified
actual charge exceeds an authorized cap, it is still recorded. The request becomes
uncertain; money covering other in-flight attempts stays held, and a budget deficit
blocks further admissions. A refund never erases paid costs. This cannot undo an
external charge or guarantee a real cap when an adapter supplies an incorrect quote.

No reservation expires automatically. Timeout, worker death, a new month, disabled
accounts and policy changes cannot free potentially billable work. Outstanding
authorizations prevent completion/refund. Uncertain requests additionally need a
trusted `verification_ref` before closure. That reference is an audit pointer, not
proof by itself: the server/operator must first establish that processing is over
and all charges are final. No public reconciliation or force-release endpoint exists.
Recovered callbacks use the original claim; there is no automatic execution takeover.

## Trusted integration contract

Use the guarded central application engine and the authenticated current user's ID,
never a body/header identity. Each service operation flushes; its caller owns commit
and rollback. Do not hold a DB transaction across an external call.

1. `reserve(user_id, request_key, fingerprint, estimated_micro_usd)` atomically checks
   user activity, monthly/daily/rate limits and company spend plus all outstanding
   holds. An idempotent replay returns the original request and `created=False`.
2. `start(request_id)` claims execution once; then `authorize_attempt(...)` allocates
   each paid step within the original envelope. The provider call must use the
   persisted attempt ID as its upstream idempotency/reconciliation key.
3. `settle_attempt(...)` records a final server-verified receipt, including a verified
   zero charge when appropriate. Receipts may arrive after user deactivation.
4. `finish(..., answered/no_answer/technical_error)` releases unused money and
   finalizes one question or its refund, provided no attempt is outstanding.
5. `mark_uncertain(...)` retains holds after an ambiguous result. Only verified
   closure can resolve it; `cancel_before_start(...)` cannot cancel started work.

`app/usage/processor.py` provides `run_once` and `AccountingContext` as a tested
integration example. Its callbacks are fake in tests. A confirmed technical failure
can refund after receipts; a generic exception produces uncertainty without storing
raw exception text. The returned processing receipt contains status/IDs, not an AI
answer. Future chat storage must retrieve the original response on replay and
recheck content permissions independently. Usage admission is not document access
authorization. Paid background ingestion and later human-transfer work need their
own reviewed integration; this task does not meter unconnected external services.

## HTTP interfaces

| Path | Access |
| --- | --- |
| `GET /me/usage` | Own remaining credits/rate availability, own observed spend, budget-blocked flag |
| `GET /me/usage/ledger` | Own paginated ledger only |
| `GET /me/usage/requests` | Own safe request state, without claim/fingerprint/receipt references |
| `GET`, `PUT /admin/usage/policy` | Admin read/configure; strict complete policy body |
| `GET /admin/usage/policy/history` | Admin policy-change audit |
| `GET /admin/usage/summary` | Admin current-month company spend, all outstanding holds and deficit |
| `GET /admin/usage/users/{user_id}` | Admin target-user summary, including inactive users |
| `GET /admin/usage/ledger` | Admin ledger; optional user filter |
| `GET /admin/usage/unresolved` | Admin outstanding request metadata |

Lists default to 50, maximum 100, with stable ordering. Responses use no-store
caching. Employee views expose no company budget amount or other user's records.
An unconfigured personal summary returns `configured:false`; Admin policy GET
returns null. Internal admission failures use 429, unconfigured policy 503, replay/
state conflicts 409 and invalid values 422. Clients cannot submit deductions,
refunds, provider charges, execution claims or server timestamps through these APIs.
Displayed balances are informational; a positive balance does not guarantee that
the eventual server-calculated quote fits. Locked admission is authoritative.

Existing platform APIs remain available when credits are exhausted. Future human
conversation routes must not consume a question credit. Transfer of the last
unanswered question needs later chat/ownership validation; no working human-chat or
transfer feature is implied by this accounting foundation.

## Setup and tests

Do not apply this automatically to the original cluster. Verify development-only
targets, capture owners/ACLs and legitimate identities, retain recoverable database
and global backups, and use the separate central migration identity:

```sh
python -m alembic upgrade usage001
```

This follows `users001` and adds five central tables/indexes with no policy or
department seeds. Keep provisioning/migration secrets outside the API. After
reviewing existing and default grants, a restricted API identity additionally needs:

```sql
GRANT SELECT, INSERT ON usage_policy, usage_requests, usage_provider_attempts, usage_entries, usage_policy_audit TO :"api_role";
GRANT UPDATE(enabled, timezone, monthly_questions, daily_questions, rate_count, rate_seconds, monthly_budget_micro_usd, version) ON usage_policy TO :"api_role";
GRANT UPDATE(state, outcome, held_micro_usd, claim_id, started_at, closed_at) ON usage_requests TO :"api_role";
GRANT UPDATE(state, actual_micro_usd, receipt_key, charged_month, input_tokens, output_tokens, settled_at) ON usage_provider_attempts TO :"api_role";
```

Retain the earlier user/RBAC/auth SELECT grants. Grant no ledger/policy-audit
UPDATE/DELETE, request DELETE, DDL, ownership, CREATEDB, CREATEROLE or SUPERUSER.
No new secrets, provider keys or `.env` settings are required. No policy override
can silently release held money. Review broad pre-existing grants before claiming
column restrictions are effective; do not blindly revoke legitimate connections.

```sh
python -m pytest -q -m "not integration" --tb=short
```

From repository root with PowerShell 7.2+, Docker and the existing test image:

```powershell
./Backend/tools/test-auth-postgres.ps1 -TestFile tests/test_usage_postgres.py
```

The tool uses a new backed-up PostgreSQL container on an internal network with no
published ports. Private recovery files and stopped containers are retained. The
original development cluster, HR/Finance data and failed Legal are not test targets.

For recovery, stop new dispatch, retain the ledger/claims/provider idempotency
references, and reconcile provider results before closing unresolved requests.
Restore backups into a separate recovery database and validate before cutover.
Never truncate accounting to reset quotas. Downgrading `usage001` destroys all new
financial/audit history and is not an automatic rollback. Database administrators
and trusted adapters remain a security boundary. Global ledger aggregation/locking
is intentionally simple for the development trial; production capacity and live
pricing/receipt verification are not established by these tests.
