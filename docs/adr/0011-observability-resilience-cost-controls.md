# ADR 0011: Privacy-safe observability, durable jobs, and bounded runtime

The recovery deferral in this record is superseded by ADR 0012 for the supported
single-process runtime. Durable checkpoints and leased multi-worker execution remain
deferred.

## Decision

Agent lifecycle and HTTP telemetry use immutable, validated aggregate events. Sinks are best effort and never alter a request or job outcome. Agent jobs and replay events are persisted in SQL with owner/idempotency and monotonic sequence constraints. ADR 0012 subsequently added queued and interrupted-job recovery for the supported singleton supervisor. Provider retries, execution deadlines, and token/cost ceilings are centrally configured and expose only stable public failure codes.

## Rationale

This preserves evidence and tenancy boundaries while making restart behavior and operational diagnosis reliable. Telemetry contains no question, answer, retrieved text, identity details, credentials, or provider payloads. Integer micro-cost accounting avoids floating-point drift.

## Consequences

Operators must maintain model accounting rates and monitor bounded worker capacity.
Python thread cancellation is cooperative; graceful shutdown drains admitted callbacks,
while an abrupt process exit relies on ADR 0012 startup recovery.
