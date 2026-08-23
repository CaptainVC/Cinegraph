# Relational database

Identity persistence uses synchronous SQLAlchemy 2.x repositories behind a port-level
unit of work. Each identity command owns a short-lived session and explicit commit;
exceptions roll back and sessions always close. SQLAlchemy metadata is an adapter
mapping only; domain models remain dependency-free.

Alembic is the sole schema authority. A fresh development database is SQLite by
default, while production settings fail closed unless the parsed URL uses the
`postgresql+psycopg` driver. Runtime startup does not create or migrate tables.

From the repository root:

```powershell
uv run python scripts/migrate_database.py upgrade
uv run python scripts/migrate_database.py downgrade -1
```

The database includes the shared identity tables plus `ingestion_jobs` and append-only
`ingestion_job_events`. The API never auto-migrates. Run the migration command before
using the durable planning CLI.

Migration `0003` adds `graph_entities`, `graph_entity_aliases`, `graph_claims`, and
`graph_claim_evidence`. Claims are stable semantic rows while evidence is source-
version scoped and replaced transactionally. The relational database remains the
graph system of record; traversal and authorization are a later application layer.

The migration environment reads centralized settings and passes the URL only to the
migration engine; it never prints the URL. The initial migration creates
`user_accounts`, `sessions`, and normalized `session_entitlements`, including unique
normalized email/token constraints, authenticated user/profile coherence, guest versus
authenticated access, session lifecycle checks, and useful expiry and profile indexes.
