# ADR 0012: Single-supervisor agent-job recovery

## Decision

The deployed API initially runs as exactly one supervised process with one Uvicorn
worker. During application composition, before readiness can pass, its recovery
supervisor atomically changes interrupted `running` jobs to `queued` and appends a
contiguous recovery event. It then admits durable queued jobs to the bounded in-process
dispatcher and continues bounded scans for work that could not previously be admitted.

Each process keeps a synchronized set of locally scheduled job IDs to prevent repeated
scans from filling dispatcher capacity with duplicate callbacks. SQL claim transitions
remain the authoritative single-winner boundary. The production PostgreSQL adapter
holds a session advisory lock for the supervisor lifetime, so a second API supervisor
fails startup instead of requeuing live work. The scanner revalidates the lock-bearing
database session and stops fail-closed if that session is lost. A recovered job
re-executes its exact persisted input from the beginning.

## Rationale

The SQL job and event store already survives a process failure, but an in-process
dispatcher does not. Startup recovery closes that reliability gap for the first VPS
deployment without pretending that the application has a distributed queue. Atomic
state plus event writes preserve SSE replay, and readiness prevents traffic before the
initial recovery pass succeeds.

## Consequences

Normal lifecycle paths may contain the recovery edge `running -> queued`, followed by a
new `running` and one terminal event. Graceful shutdown stops the scanner and drains
admitted callbacks before releasing the PostgreSQL supervisor lock and closing shared
dependencies. Multiple Uvicorn workers, replicas, rolling overlap, and another live
consumer remain unsupported; the singleton advisory lock deliberately rejects them.

The dispatcher and LangGraph checkpoint remain process-local. Recovery reruns a stored
turn; it cannot resume an interrupted provider call or reconstruct checkpoint history
lost with the process. A durable LangGraph checkpointer and per-job leased multi-worker
queue remain separate future decisions.
