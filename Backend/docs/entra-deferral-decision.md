# Decision: Microsoft Entra sign-in is built after the front end

Status: approved by the project owner (2026-11-23). Supersedes the idea of doing the Entra integration before the
front end. The technical requirements for the integration itself stay in
[authentication.md](authentication.md) ("Maintenance, rollback and Entra handoff").

## Decision

Build and try out the front end first, exercise the services and functions through it with the existing
**development password sign-in**, and only then connect sign-in to Microsoft Entra. The prepared OIDC/JWT
verifier work (previously "PR-D") moves to after the front end.

## Why

- The front end does not need Entra to be built and tried: every feature (questions, human handoff, notifications,
  audit) works through the session identity that `/auth/me` already returns.
- Using the screens will show missing fields, awkward errors and missing endpoints. It is cheaper to fix them
  before the API contract is frozen.
- Entra needs inputs that are not ready (tenant, app registration, a decision on groups). How roles map is easier
  to decide once the screens show who sees what.
- No real data is in use: the legal status is still `failed`, so postponing real sign-in adds no exposure.

## Conditions (so nothing has to be redone)

1. **One identity interface in the front end.** The client asks only "who am I?" through `/auth/me` and treats the
   session as opaque. It must not contain anything specific to development passwords or to Entra; switching the
   source later is a back-end change.
2. **Decide the session mechanism now.** The agreed deployment is a web app behind an HTTPS reverse proxy on the
   same origin (CORS closed). Recommended for real sign-in: an `HttpOnly`, `Secure`, `SameSite` cookie plus CSRF
   protection for state-changing calls. Settle this before screens depend on it; the development flow may keep its
   bearer token until then, hidden behind the same client interface.
3. **Design the session states from the start:** expiry, a role revoked while the user is working (the back end
   re-reads permissions on every request and answers `403` or `404`), sign-out, and a refused or inactive user.
4. **Development sign-in stays confined to development and test.** It is refused elsewhere today; this remains a
   hard condition before any deployment.
5. **Synthetic users and departments only.** No real people or documents are entered before the Entra step.
6. **Prepare the minimum inputs for Entra:** how users map to departments and roles (Entra groups, or managed by
   the Admin in this system), and which Entra claims identify a person.

## What does not change

The requirements for the later integration are the ones already recorded: a separate validator that checks
signature, issuer, audience, expiry and tenant policy; mapping the immutable issuer and subject to central users and
explicit internal roles; no identity taken from a header or an email claim alone; no fallback to development passwords;
RBAC and isolation tests re-run after the integration.

## Exit criteria for starting the Entra step

- The front end covers the agreed flows and has been tried end to end with synthetic users.
- The session mechanism (condition 2) is decided and implemented behind the identity interface.
- Tenant, app registration and the role-mapping decision are available.
- The owner approves starting; the legal gate is addressed separately and is not opened by this step.
