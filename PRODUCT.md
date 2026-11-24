# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack
delegated: user left the choice open. Recommended and recorded: React + TypeScript on Vite as a client-rendered SPA talking to the existing FastAPI backend. Reason: an authenticated internal tool needs no SEO or SSR, and Arabic RTL plus a role-based shell is simplest in a single SPA. Revisit if Microsoft Entra sign-in requires a different flow.

## Users
Company employees, using it internally. Three roles from the backend RBAC, each with a different job:
- Employee: asks questions about their own department's documents and reads past conversations.
- Department head: manages members of their one owned department and watches their usage.
- Admin: manages users, departments, roles, the audit log and the company question budget.

## Product Purpose
An internal AI platform where each department has an isolated database and restricted access. An employee asks the AI about their department's documents only, within a defined question credit allowance. Success: staff get answers from their own department's material quickly, and nobody can reach another department's data or overspend the budget.

## Positioning
Department-isolated data plus per-department RBAC, question credits and a full audit trail. A generic chat tool cannot truthfully promise that boundary.

## Operating Context
Used inside the company; sign-in is development password login today, Microsoft Entra is pending. Backend is FastAPI with central PostgreSQL and one database and restricted role per department, created only by a separate provisioning worker. Documents include Arabic material (e.g. annual leave forms, a scanned copy and its OCR text). Interface language is Arabic only (RTL); there is no language switch.

## Capabilities and Constraints
Existing backend capabilities: authentication and sessions (`GET /auth/me`), departments (Admin-issued grants, one owned department per head, archival without deletion), users and memberships with approved roles, question credits, usage and company budget, owner conversations with immutable history, audit read for handoff, an internal question adapter. Fresh migrations create no departments; HR and Finance exist only as development fixtures. Undecided: Microsoft Entra integration, final production auth, and exact API shapes the screens will consume (to be confirmed against the backend docs per screen).

## Brand Commitments
None confirmed. No existing name, logo or voice beyond "AI Platform / Agentic Systems".

## Evidence on Hand
Backend docs under `Backend/docs/`, `README.md`, `TENANT_CONTRACT.md`. Sample Arabic documents in the repo root. No real usage data, screenshots, testimonials or brand assets; do not fabricate them.

## Product Principles
- The department boundary is always visible: users should always know which department and which data they are working in.
- Remaining question credits are never a surprise: show them before the user asks, not after they are refused.
- Every consequential action is traceable, and the interface says so plainly.
- Arabic RTL is the primary experience, not a mirrored afterthought.
- Role decides the surface: employees, heads and admins see only what their role allows.

## Accessibility & Inclusion
No product-specific standard required; follow ordinary good practice (keyboard use, contrast, readable Arabic).
