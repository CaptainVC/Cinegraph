# Identity database

Identity persistence uses synchronous SQLAlchemy 2.x repositories behind a port-level
unit of work. Each identity command owns a short-lived session and explicit commit;
exceptions roll back and sessions always close. SQLAlchemy metadata is an adapter
mapping only; domain models remain dependency-free.

Alembic is the sole schema authority. A fresh development database is SQLite by
default, while production settings fail closed unless the parsed URL uses the
`postgresql+psycopg` driver. Runtime startup does not create or migrate tables.

From the repository root:

```powershell
uv run python scripts/migrate_identity_database.py upgrade
uv run python scripts/migrate_identity_database.py downgrade -1
```

The migration environment reads centralized settings and passes the URL only to the
migration engine; it never prints the URL. The initial migration creates
`user_accounts`, `sessions`, and normalized `session_entitlements`, including unique
normalized email/token constraints, authenticated user/profile coherence, guest versus
authenticated access, session lifecycle checks, and useful expiry and profile indexes.
