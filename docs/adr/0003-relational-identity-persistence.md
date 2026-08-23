# ADR 0003: Relational identity persistence and transaction ownership

## Status

Accepted for Phase 26.

## Decision

Identity persistence uses SQLAlchemy 2.x at the adapter boundary, synchronous
repositories, and an explicit per-command unit of work. Domain models and ports do not
carry ORM annotations. A unit of work owns one short-lived `Session`, performs an
explicit commit on success, rolls back on exceptions, and always closes. Password
verification remains outside the database transaction; account creation and session
issuance share one transaction.

Alembic migrations are the only schema authority. Runtime startup does not call
`create_all` or silently migrate. The initial migration stores account/session state in
relational tables and stores guest season entitlements in a normalized child table,
with unique, foreign-key, check, and lifecycle constraints enforcing invariants that
must hold under concurrent requests.

## Trade-off

Development defaults to a gitignored SQLite database so tests and local development do
not require a service. Production settings fail closed unless the parsed SQLAlchemy URL
uses the Psycopg 3 PostgreSQL dialect. This keeps the local feedback loop lightweight
while making production deployment use a concurrent, server-backed transaction store.
The in-memory unit-of-work adapter remains available for fast application tests and
implements rollback semantics rather than bypassing the transaction contract.
