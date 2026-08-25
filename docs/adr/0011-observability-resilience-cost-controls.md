# ADR 0011: Privacy-safe observability, durable jobs, and bounded runtime

## Decision

Agent lifecycle and HTTP telemetry use immutable, validated aggregate events. Sinks are best effort and never alter a request or job outcome. Agent jobs and replay events are persisted in SQL with owner/idempotency and monotonic sequence constraints; queued worker recovery remains a future worker-supervision phase. Provider retries, execution deadlines, and token/cost ceilings are centrally configured and expose only stable public failure codes.

## Rationale

This preserves evidence and tenancy boundaries while making restart behavior and operational diagnosis reliable. Telemetry contains no question, answer, retrieved text, identity details, credentials, or provider payloads. Integer micro-cost accounting avoids floating-point drift.

## Consequences

Operators must maintain model accounting rates and monitor bounded worker capacity. Python thread cancellation is cooperative; a process restart can leave a claimed job running until explicit recovery supervision is added.
