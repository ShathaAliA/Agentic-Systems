# Human handoff — contract (H0)

Status: design, approved decisions; data model implemented in `c06_0005` (H1); service implemented in
`app/handoffs` (H2). Routes, notifications and audit access follow in H3–H5. Legal remains `failed`; no real data; no front end in this scope.

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

### How H2 applies them (`app/handoffs/service.py`, `access.py`)

- One service instance is bound to one authenticated user (through its result store). Every call re-reads Central:
  the user must be active; `handoffs:request` is checked on the department when creating or sending as the
  requester; handler scope is the set of departments where the user holds a **department-scoped** role with
  `handoffs:handle`, an active membership and an active department. A global role never makes anyone a handler.
- Unknown, invisible and foreign handoffs all answer `404 handoff_not_found`. Staff views omit the conversation id
  and turn; only the requester sees where the question came from.
- Creating needs: an owned conversation that includes the department, a turn whose stored outcome is `no_answer`,
  no other handoff for that turn and department, and fewer open handoffs than the cap. With the monthly credit
  used up, only the **latest turn** of the conversation may still be handed over (`429 handoff_credit_exhausted`).
  A retry with the same `request_key` replays; the same key with other content is `409`.
- The assignee transfers (to another current handler, never to the requester or themself) and closes. If the assignee
  has lost the handler role, any current handler may do both. A requester cannot claim, close or reopen.
- Messages carry no idempotency key: a client retry after a lost response can post twice. Replying to an unclaimed
  handoff claims it first in the same transaction; replying as a non-assignee is `409 handoff_not_assignee`.
- Inbox lists oldest first by creation (stable cursor); activity ordering belongs to the notification feed (H4).
- The open cap is a callable (`open_cap`, default 5); H3 wires the Admin setting.

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
