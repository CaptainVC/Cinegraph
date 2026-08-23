# ADR-0006: Relational graph claims and provenance

## Status

Accepted

## Decision

Cinegraph stores graph entities, semantic claims, aliases, and source-scoped evidence
in PostgreSQL (SQLite is used for local tests). A claim is stable across source
replacements and is identified by extraction revision, series, subject, predicate,
object, and polarity. Confidence belongs to evidence, not the semantic claim.

Every evidence row retains its transcript chunk, source version, episode timing,
transcript index revision, extraction revision, and source governance status. New
evidence is inserted before retired-source evidence is removed in one short
transaction. Repeating a replacement is idempotent and conflicting immutable IDs
fail and roll back.

Conflicting claims coexist: polarity, object, and evidence context are part of the
claim identity. Phase 30 must rank and filter conflicting claims rather than
destructively selecting a single truth. A changed extraction revision requires a
full re-extraction; old revisions remain distinguishable and are not silently
mixed into a current traversal.

## Consequences

The relational system of record keeps deployment and backups simple and preserves
strong foreign keys and transactional replacement. A graph database is deliberately
deferred until traversal workloads justify it; Phase 30 can project authorized
claims into GraphRAG reads without changing this write contract.
