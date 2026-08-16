# Cinegraph HTTP API

The FastAPI application is an application boundary over the existing corpus,
identity, retrieval, and LangGraph services. It does not accept entitlement
scopes or candidate episode identifiers from clients.

Run the development server from the repository root:

```bash
uv run python scripts/run_api.py
```

The server binds to `127.0.0.1:8000` by default. Configure it with
`CINEGRAPH_API_HOST` and `CINEGRAPH_API_PORT`. Production mode marks the opaque
session cookie `Secure`; every environment uses `HttpOnly` and `SameSite=Lax`.

## Contracts

- `GET /health/live` checks the process.
- `GET /health/ready` checks that the configured Qdrant collection is reachable.
- `POST /api/v1/auth/guest` issues an eight-hour guest session.
- `POST /api/v1/auth/register` creates an account and session.
- `POST /api/v1/auth/login` authenticates an account.
- `GET /api/v1/auth/session` resolves the current cookie.
- `POST /api/v1/auth/logout` revokes the current session.
- `GET /api/v1/catalogue` returns only corpus-visible catalogue entries.
- `POST /api/v1/chat` runs governed retrieval and grounded generation.

Guest sessions remain constrained to Modern Family seasons 1 and 2 in the
trusted identity service. Chat requests may select relaxed access for discovery
or a strict/sequential spoiler boundary, but cannot broaden corpus entitlement.
The model sees only segments returned after both policies have been compiled.
Unknown, duplicate, or absent citations are retried once and then become a safe
refusal through the LangGraph workflow.
