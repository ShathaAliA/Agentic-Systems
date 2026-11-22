# Human handoff — contract (H0)

Status: design, approved decisions; data model implemented in `c06_0005` (H1). Service, routes, notifications and
audit access follow in H2–H5. Legal remains `failed`; no real data; no front end in this scope.

## What it is

When the agent cannot answer, the employee can hand the question to the department's head or deputy. The employee
reviews and edits a **summary** (never the whole agent conversation), a **human conversation** opens with the
department, replies arrive later, and everything is kept for audit. A separate notification feed drives the in-app
inbox (the mailbox and bell are front-end work; this contract defines what the API gives them).

## Decisions (approved)

| # | Decision |
| --- | --- |
| 1 | Data lives in the **C06 store** (same isolated database, immutable, no content in Central) |
| 2 | The summary is written by the employee; an optional suggestion hook exists but is **off** by default |
| 3 | Notifications: an inbox **feed** and an **unread counter** (polling) first; SSE later on top of the same data |
| 4 | Open handoffs per employee are capped; default **5**, adjustable by Admin |
| 5 | Converting a human answer into approved knowledge is **later** (needs Documents) |
| 6 | Admin has **read-only audit** access, every read logged; Admin never replies or closes |

## Roles and permissions

Identity and department scope always come from Central, re-checked on every request (revoking a head's role stops
access immediately). New permission codes (to be added to the role-policy catalog in H3): `handoffs:request`
(employee), `handoffs:handle` (department head and deputy, department-scoped), `handoffs:audit` (Admin).

| Actor | Can | Cannot |
| --- | --- | --- |
| Employee (requester) | create a handoff from their own conversation turn, read their handoffs, send messages while it is not closed, mark read | see other people's handoffs, close, claim |
| Head / deputy | list the department inbox, claim, transfer to the other, reply (only as assignee), close, reopen, mark read | see the employee's agent conversation, see other departments |
| Admin | audited read of any handoff | reply, claim, close, edit anything |

## Data model (C06 store, migration `c06_0005`)

All tables are insert-only (no UPDATE/DELETE/TRUNCATE, enforced by trigger), with row-level security.

| Table | Content |
| --- | --- |
| `c06_handoffs` | requester, department, source conversation and turn, `request_key` (idempotency), the original summary |
| `c06_handoff_events` | `created`, `claimed`, `transferred`, `closed`, `reopened` with actor and assignee; the current state is derived from the latest event |
| `c06_handoff_messages` | the human conversation, per-handoff sequence, author role `requester` or `staff` |
| `c06_handoff_reads` | read receipts (`up_to_sequence`); unread counts are derived, never stored |
| `c06_handoff_access_log` | one row per Admin audit read or list |

Integrity enforced by the database:

- A handoff references a turn of **the requester's own** conversation (composite foreign keys); one handoff per
  turn and department; `(requester, request_key)` is unique.
- Creating a handoff writes its `created` event atomically (trigger), so every handoff has a state.
- State machine: `new` (created or reopened) → `claimed` → `transferred`* → `closed` → `reopened`; anything else is
  refused. Event and message sequences are assigned by trigger under a per-handoff advisory lock, so concurrent
  claims produce exactly one winner.
- Messages are refused once closed; a staff message is accepted only from the current assignee.
- Read receipts cannot point past the last message.

## Row-level security and trust

Trusted application code sets, per transaction after checking Central: `app.c06_user_id` (the actor),
`app.c06_staff_departments` (comma-separated departments the actor may handle) and `app.c06_audit` (`on` for an
Admin audit read). A row is visible to its requester, to staff of its department, or in audit mode. Inserts must be
made by the author, with the role the policy requires. As with the rest of C06, RLS is defence in depth: a
compromised application can set these values, so the application check against Central stays authoritative.

## Rules the service enforces (H2)

- The handoff targets a department that the source turn left unanswered; the summary is for that department only.
- Open-handoff cap per employee (default 5). After the monthly credit is exhausted the employee may still hand
  over the last unanswered question, as already approved; further human requests wait.
- Human messages never consume question credit. No message is edited or deleted by anyone.
- Replying requires being the assignee; replying to an unclaimed handoff claims it first, in the same transaction.

## Notification feed (H4)

`GET /notifications` lists, per handoff with something new: event type (reply, claimed, transferred, closed, new
request), time of the last event and the unread count, with no message text; a total unread counter for the bell;
marking a handoff read inserts a receipt. Nothing extra is stored: the feed is derived from messages and receipts.
Email and Teams are out of scope.

## Out of scope here

Front end, conversion of answers into knowledge, retention and deletion policy, automatic redaction of personal
data in summaries (the employee and the handler are responsible), Microsoft Entra.

## Operations

The runtime login role needs `SELECT, INSERT` on the five new tables and nothing else. The store refuses to run
below revision `c06_0005`. Take a backup before migrating, as with every C06 migration.

## Verification of H1 (2026-11-22, Docker runner, PostgreSQL 17.10)

| Suite | Result |
| --- | --- |
| New handoff schema (16 tests, 26 cases) | 26 passed |
| result_store / conversations / sealing | 16 / 13 / 7 passed (1 native-only skip) |
| authorization / lifecycle / security | 24 / 11 / 20 passed |
| recovery + restore drill (now covering the five new tables) | 7 + 5 passed |
| joint /ask (SQLite tenant) and joint /ask on real pgvector | 8 and 6 passed |
| Unit suite | 589 passed |

Behaviour worth knowing: BEFORE ROW triggers run before RLS checks, and the event guard reads events through RLS.
An actor who cannot see a handoff is therefore refused by the guard (a check violation) rather than by the policy,
with the same error as for a handoff that does not exist, so existence is not revealed.

Not covered yet: PostgreSQL 18.x native, hosted CI, the service layer, routes, notifications and Admin audit
access (H2–H5), and the permission codes in the role-policy catalog (H3).
