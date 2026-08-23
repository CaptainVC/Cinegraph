# Authorized GraphRAG reads

Phase 30 adds the read side of the relational graph foundation. `GraphRagQueryService`
normalizes seed aliases and predicates, compiles `RetrievalScope`, short-circuits
empty scopes, and validates every adapter result. `SqlAlchemyGraphClaimReader`
resolves aliases and performs bounded bidirectional breadth-first traversal over
claims that have authorized evidence in the requested episodes.

Visibility is enforced twice: SQL requires exact episode identity, safe-until
timestamp, allowed rights, active source, approved review status, and current
transcript/graph revisions; the application repeats those checks before ranking.
Claims retain asserted, negated, and uncertain polarities. Results are ordered by
hop distance, deterministic evidence score (confidence plus saturated distinct
episode support), and stable claim UUID.

The reader has no LLM or LangGraph dependency. Phase 31 can use this port as a
tool while preserving trusted scope ownership in the application boundary.
