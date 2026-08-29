# Cinegraph HTTP API

The FastAPI application is an application boundary over the existing corpus,
identity, retrieval, and LangGraph services. It does not accept entitlement
scopes or candidate episode identifiers from clients.

Run the development server from the repository root:

```bash
uv run python scripts/run_api.py
```

The server binds to `127.0.0.1:8000` by default. Configure it with
`CINEGRAPH_API_HOST` and `CINEGRAPH_API_PORT`. Production uses `__Host-` Secure
session/CSRF cookies and double-submit CSRF plus same-origin checks for unsafe
requests; development uses usable non-Secure names.

`create_app(context=...)` takes lifecycle ownership of the injected context: lifespan
startup acquires the singleton job-supervisor lease and performs recovery, and lifespan
shutdown closes that context after draining agent callbacks. Do not reuse one context
across application lifespans.

## Contracts

- `GET /health/live` checks the process.
- `GET /health/ready` checks SQL access to the durable agent-job schema, requires the
  single-process recovery supervisor to be live, and requires the configured Qdrant
  collection to be green with the expected dense, sparse, and payload-index schema.
- `GET /client-config` bootstraps the product shell with the canonical API prefix and
  centralized browser polling/deadline limits; the same contract is mirrored at
  `<api-prefix>/client-config` for API clients.
- `POST /api/v1/auth/guest` issues an eight-hour guest session.
- `POST /api/v1/auth/register` creates an account and session.
- `POST /api/v1/auth/login` authenticates an account.
- `GET /api/v1/auth/session` resolves the current cookie.
- `POST /api/v1/auth/logout` revokes the current session.
- `GET /api/v1/account` returns the authenticated account without password data.
- `PATCH /api/v1/account/profile` updates the current display name.
- `POST /api/v1/account/password` changes the password and rotates the session.
- `GET /api/v1/account/sessions` lists bounded owner-scoped active sessions.
- `DELETE /api/v1/account/sessions/{session_id}` revokes one owner session.
- `POST /api/v1/account/logout-all` revokes all current-user sessions.
- `GET /api/v1/catalogue` returns only corpus-visible catalogue entries.
- `POST /api/v1/chat` runs governed retrieval and grounded generation.
- `POST /api/v1/recommendations` ranks only entitled, spoiler-visible episode
  candidates and requires visible transcript citations for every explanation.

Guest sessions remain constrained to Modern Family seasons 1 and 2 in the
trusted identity service. Chat requests may select relaxed access for discovery
or a strict/sequential spoiler boundary, but cannot broaden corpus entitlement.
The model sees only segments returned after both policies have been compiled.
Unknown, duplicate, or absent citations are retried once and then become a safe
refusal through the LangGraph workflow.

Recommendation requests apply runtime, watched/unwatched, excluded-theme, corpus,
and spoiler constraints before retrieval. The model receives only that bounded
candidate set; injected episode or citation identifiers are rejected after ranking.

## Request guardrails and audit trail

Every response carries a validated or server-generated `X-Request-ID`,
`nosniff`, frame-denial, referrer, no-store, and rate-limit headers. Production
responses also carry HSTS. JSON validation failures expose only affected field
names, never submitted values; unhandled failures return a stable internal-error
contract while the request ID remains available to operators.

Request bodies are capped at 64 KiB. A bounded, thread-safe token bucket applies
larger costs to authentication and chat routes, and structured audit events record
method, path, status, duration, outcome, and principal kind without cookies,
tokens, query strings, request bodies, or response bodies. Values and route costs
are centralized in `ApiConfiguration`.

The current limiter is intentionally process-local for the single-process
development runtime. A multi-worker or multi-node production deployment must use
a shared rate-limit adapter or enforce an equivalent policy at the trusted reverse
proxy. Forwarded address headers are not trusted by the application boundary.
