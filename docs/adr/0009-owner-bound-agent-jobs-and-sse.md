# ADR-0009: Owner-bound idempotent agent jobs and replayable SSE

## Decision

The Phase 32 product path submits a bounded asynchronous `AgentJob` using a
server-generated candidate set and the authenticated profile's corpus scope.
The idempotency key is a canonical UUID; the request fingerprint includes the
profile, thread, series, question, scope revision/scope, and sorted candidates.
Jobs transition `queued -> running -> succeeded|safe_refusal|failed` exactly
once. An append-only event log is replayed by sequence through same-origin SSE.
Dispatcher rejection is an explicit `queued -> failed` transition with no
start timestamp, and its terminal event is emitted exactly once.

Status and event resources are owner-scoped and return indistinguishable 404s
for unknown and cross-profile IDs. Event payloads contain only lifecycle and
typed public result fields; questions, prompts, transcript text, scope objects,
provider state, exception text, and secrets never cross this boundary.

## Consequences

The Phase 32 queue, in-memory job store, and LangGraph checkpoint are process
local and not crash durable. A later persistence boundary can replace these
injected ports without changing the HTTP contract. Guest access derives only
the entitled Modern Family S1/S2 catalogue candidates; no browser watch state
or candidate IDs are trusted.
