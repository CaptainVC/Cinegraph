# ADR-0007: Authorization-first relational GraphRAG reads

## Status

Accepted

## Decision

GraphRAG reads use PostgreSQL's graph-claim tables as the system of record. The
application compiles entitlement and spoiler visibility into a `RetrievalScope`
before calling the graph reader. The SQL adapter repeats every scope, rights,
source-review, and revision predicate in its visibility `EXISTS` query; a claim
without visible evidence cannot be returned or expand a traversal frontier.

Seed aliases are normalized with NFKC and casefolding. Traversal is bounded
breadth-first in both edge directions for at most two hops. Each frontier query,
selected claim set, result set, and per-claim evidence set is capped and ordered
deterministically. Evidence is ranked and capped in SQL with a window function.
The application validates stable identifiers, exact episode identity and safe
cutoffs again before ranking results.

## Consequences

Guest and authenticated scopes use the same reader and cannot be widened by
model-visible arguments. Conflicting polarities remain separate claims. A graph
database and LLM-driven planning are intentionally deferred to later phases;
this slice provides a testable, portable GraphRAG retrieval foundation.
