# Observability, resilience, and cost operations

## Data contract

Runtime telemetry is emitted as one JSON object per line on the `cinegraph.runtime`
logger. Allowed fields are UTC time, stage/outcome, opaque correlation/request/job IDs,
bounded duration, stable failure code, aggregate model/tool call counts, aggregate token
counts, estimated cost in integer micros, citation count, and explicitly allowlisted
low-cardinality attributes. IDs are log-correlation values only and must never become
metric labels.

Never add questions, prompts, answers, retrieved text, transcripts, cast/plot text,
profile or account details, cookies, session/CSRF/API keys, IP or user-agent values,
provider payloads, exception messages, or stack traces to runtime events. Event
validation fails closed and sink failures are isolated from request and job outcomes.

The lifecycle stages are `queued`, `running`, zero or one aggregate `model` event per
agent invocation, and `terminal`. Retrying an idempotent submission does not emit a
second queued lifecycle. Crash recovery emits a new queued lifecycle for the same job
ID, matching its durable recovered event. Terminal outcomes use only stable codes:

- `execution_timeout`
- `provider_unavailable`
- `budget_exceeded`
- `agent_execution_failed`
- `agent_dispatch_unavailable`

## Service targets and alerting

Initial product targets are 99.9% HTTP availability, p95 non-generation API latency
below two seconds, and queued-to-running latency below five seconds during normal
capacity. Derive counters and histograms from stage/outcome and durations, never from
opaque IDs. Before production, alert on sustained server-error rate, readiness failure,
queued jobs without a running event, running jobs without a terminal event, budget
failure spikes, and provider-unavailable spikes. Exact thresholds belong in the
deployment phase after traffic baselines exist.

## Retry, deadline, and budget behavior

LangChain is the single retry layer and provider SDK retries are disabled. Only
classified connection, timeout, rate-limit, and provider-server failures are retried.
Authentication, authorization, policy, validation, idempotency, client, and unknown
failures are not retried. Python cannot safely cancel an in-flight synchronous model
call, so the aggregate deadline is cooperative and checked before any answer is
projected.

Each nested Terra/Luna response is accounted through callbacks attached to the model
instances and a context-local per-invocation ledger. The ledger retains provider usage
even when the just-completed response crosses a budget. Limits cover attempted calls,
input/output/total tokens, and estimated cost. The emitted `model_calls` value counts
responses with valid accounted usage; failed provider attempts can be higher. Missing
usage or unknown pricing fails closed. Rates in `agent_runtime_controls.py` are
operator-maintained accounting assumptions, not claims about current provider prices.

## Persistence and readiness

Migration `0004_agent_jobs` stores jobs and append-only replay events. Owner-scoped
idempotency is unique, lifecycle status and event writes share one transaction, event
sequence is contiguous and unique per job, and event IDs are deterministic. The API
maps repository outages to a sanitized 503. Readiness requires both a successful SQL
query against `agent_jobs`, the configured Qdrant collection, and a live recovery
supervisor that completed its startup recovery pass.

The bounded dispatcher and LangGraph checkpoint are still process-local. When the one
supported API process starts, it transactionally requeues interrupted `running` jobs,
adds a contiguous recovery event, and scans persisted `queued` work for bounded local
dispatch. SQL or Qdrant unavailability detected before admission keeps work queued;
an outage after execution starts follows the bounded runtime failure policy and may
produce a terminal failure. If a callback stops after a terminal SQL write fails, its
job is tracked and atomically requeued when SQL recovers. Recovery reruns the frozen job
input; it does not resume an interrupted model call or reconstruct a lost multi-turn
checkpoint.

Run exactly one API process with one Uvicorn worker. Stop the old process completely
before starting its replacement. The production PostgreSQL adapter holds a session
advisory lock until graceful shutdown has stopped recovery scans and drained admitted
callbacks; a second supervisor fails startup. The scanner verifies its dedicated
PostgreSQL session identity and stops fail-closed if that lock-bearing connection is
lost; it cannot be restarted inside the same process, so the process supervisor must
replace the instance. Per-job leases and ownership heartbeats for multi-worker
operation are not part of this design.

Graceful shutdown intentionally waits without an in-process timeout for already running
callbacks so shared dependencies and the singleton lease cannot be released underneath
live work. The host process supervisor supplies the hard stop deadline; if it must kill
a hung process, the database releases the advisory session and the next process performs
normal startup recovery.

## Incident checklist

1. Confirm readiness separately for SQL and Qdrant from the host without copying any
   corpus or credential material into logs.
2. Correlate the opaque request and job IDs across HTTP and runtime JSON events.
3. Identify the last lifecycle stage and stable failure code; never request exception
   bodies or user content for routine triage.
4. For provider incidents, confirm the classified error rate and retry exhaustion.
5. For budget incidents, verify configured ceilings, usage metadata availability, and
   operator-maintained rates before changing limits.
6. For stuck jobs, preserve the SQL job/event rows, confirm only one API process is
   alive, and restart that supervised process. Verify the next event is the contiguous
   recovered `queued` event; do not mutate rows or create work outside the idempotent
   submission API.
