# ADR-0010: Authentication security, session rotation, and tenant isolation

## Status

Accepted for Phase 33.

## Decision

Cinegraph treats the session principal as the sole source of account identity and
corpus entitlement. Guest sessions carry the immutable Modern Family Seasons 1–2
scope; authenticated sessions carry the current unrestricted scope revision.
Every resolve compares the persisted scope and revision with the current policy,
failing closed on stale or forged records.

Session tokens are opaque random values. Only SHA-256 digests are persisted. Login,
registration, and password change rotate sessions; password change revokes all
previous sessions and creates one replacement. Session listings and revocations are
owner-scoped by both user and profile identifiers.

Production uses `__Host-cinegraph_session` and `__Host-cinegraph_csrf` cookies. The
session cookie is Secure, HttpOnly, Path=/, and SameSite=Lax; the CSRF cookie is
Secure, readable by the browser, Path=/, and SameSite=Lax. Production unsafe API
requests require a constant-time double-submit match and same-origin Origin or
Sec-Fetch-Site signal, including the initial unauthenticated auth request after
the UI seeds the CSRF cookie. Development keeps usable non-Secure cookie names
while retaining the same response privacy rules.

## Consequences

Account/profile/password/session routes cannot target another tenant, and unknown
or cross-owner sessions intentionally return the same 404. Email verification,
password reset, MFA, and distributed rate limiting remain deferred; this decision
does not claim to implement them.
