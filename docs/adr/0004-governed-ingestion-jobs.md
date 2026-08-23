# ADR 0004: Governed, durable corpus jobs

## Decision

Corpus preparation is a read-only inventory followed by explicit, durable jobs. A job stores only trusted catalogue identifiers, source/config fingerprints, scheduling and bounded retry metadata. It never stores subtitle text, prompts, provider responses, credentials or filesystem paths.

Jobs are claimed with a lease and opaque worker identifier. PostgreSQL uses row locks with `SKIP LOCKED`; all completion and heartbeat updates also use lease-owner compare-and-swap predicates. Events are append-only and retained by a restrictive foreign key. SQLite provides serialized development semantics.

The inventory requires a final approved review status and a matching SHA-256 ledger record before transcript ingestion is planned. Script-aligned but unreviewed episodes become speaker-review jobs; raw-only episodes become alignment jobs. Missing or invalid artifacts are reported but never automatically executed.

## Consequences

The worker runtime can be added later without changing corpus policy. Planning is deterministic and repeatable. Operators must run migrations before `--enqueue`; the CLI deliberately does not auto-migrate.
