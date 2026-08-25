# ADR-0008: Authorization-safe series agent runtime

## Status

Accepted.

## Decision

CineGraph exposes a series-level LangGraph adapter behind application-owned,
immutable runtime context. The context contains only trusted series identity,
authorized candidate episodes, the reloaded profile watch state, and the exact
corpus access scope. It is passed at invocation time and is never placed in
model messages or model-visible tool schemas.

The agent has two read-only tools. The transcript tool delegates to the governed
hybrid answer workflow; the relationship tool delegates to authorization-first
GraphRAG. Both tools accept only bounded semantic arguments and close over the
trusted context. Their projections contain stable IDs, timing, relationship
metadata, and refusal state, never transcript text or authorization controls.

The final model response uses an adapter-private structured schema containing an
optional answer and explicit citation IDs. The adapter resolves IDs only against
tool evidence returned during the current user turn. Unknown, duplicate,
malformed, or uncited responses become safe refusals. Checkpoint history may be
used for conversation continuity, but prior-turn evidence cannot authorize a
current-turn citation.

Central configuration bounds questions, candidate episodes, retrieval, graph
traversal, model/tool calls, retries, and citation count. The synthesis model is
Terra and the cost-sensitive selector/grounding route is Luna. Prompt rules are
defense in depth; authorization remains structural and application-owned.

## Consequences

The adapter can be tested with deterministic local chat models and injected
checkpointers without an API key. Phase 32 exposes the typed result through HTTP/SSE
without exposing LangGraph state, prompts, context, or raw provider payloads.
