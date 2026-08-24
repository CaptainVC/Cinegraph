# Authentication and security model

## State and rotation

An anonymous request has no principal. `POST /api/v1/auth/guest` creates a guest
principal with the exact `guest-modern-family-s01-s02-v1` corpus revision. A valid
presented authenticated token is reused; a valid presented guest token is
revoked and replaced. Registration and login revoke a valid presented prior token
and issue an authenticated session. An invalid stale cookie never blocks login.

Changing a password verifies the current password, hashes the replacement, atomically
updates the account, revokes all active sessions, and issues exactly one replacement.
Logout revokes the current session; logout-all revokes every active session owned by
the current user/profile. Sessions are listed newest-first with a bounded cap and
contain only `session_id`, timestamps, and a `current` marker.

## Cookies and CSRF

| Environment | Session cookie | CSRF cookie | Secure | HttpOnly |
|---|---|---|---|---|
| Development | `cinegraph_session` | `cinegraph_csrf` | no | session only |
| Production | `__Host-cinegraph_session` | `__Host-cinegraph_csrf` | yes | session only |

All cookies use `Path=/`, no Domain, and configured SameSite. Cookie deletion uses
the same name, path, Secure, and SameSite attributes. The CSRF cookie is random,
non-HttpOnly, and is never persisted server-side. Browser JavaScript reads only this
CSRF cookie and sends it in `X-CSRF-Token` for unsafe requests; it never reads a
session token. Production unsafe API requests also require a matching same-origin
Origin or `Sec-Fetch-Site: same-origin` signal.

## Endpoints

`GET /api/v1/account` returns the current account. `PATCH /api/v1/account/profile`
updates only `display_name`. `POST /api/v1/account/password` rotates the session.
`GET /api/v1/account/sessions` lists bounded owner sessions. `DELETE
/api/v1/account/sessions/{session_id}` revokes one owner session and returns 404 for
unknown or cross-owner IDs. `POST /api/v1/account/logout-all` revokes all owner
sessions. Guest account endpoints return stable 403 account-required errors.

## Corpus policy and limitations

Clients cannot submit or widen corpus scopes, candidate episode sets, profile IDs,
or trusted workflow context. Authenticated access is unrestricted by the current
policy; guests remain exactly Seasons 1 and 2. Email verification, password reset,
MFA, and shared multi-node rate limiting are deferred features.
