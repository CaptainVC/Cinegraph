# Agent jobs API

`POST /api/v1/agent/jobs` requires the existing HTTP-only cookie session and a
canonical `Idempotency-Key` UUID. Its JSON body is exactly:

```json
{"thread_id":"00000000-0000-0000-0000-000000000031","series_id":"00000000-0000-0000-0000-000000000011","question":"Who introduces the family?","spoiler_mode":"relaxed","safe_through_episode_id":null}
```

`spoiler_mode` is `relaxed`, `strict`, or `sequential`. Protected modes require
`safe_through_episode_id`; relaxed mode must leave it null. The episode boundary
is validated against the server catalogue and current entitlement. The client
never supplies candidate episodes: the API compiles the current watch policy and
captures the exact entitled candidate set in the job. A conversation thread is
also bound to that set, preventing a later request from reusing a checkpoint with
a broader history.

The server derives and authorizes candidates. Guest Modern Family requests can
only receive seasons 1 and 2. The response is `202 Accepted`, includes a
`Location` status URL and absolute status/events URLs, and never includes
candidate IDs, scope, prompts, or raw transcript evidence.

`GET /api/v1/agent/jobs/{job_id}` returns the typed lifecycle and, on success,
grounded answer, stable tool names, and citation locators. Safe refusals contain
no answer or evidence. Unknown and cross-profile IDs both return `404`.

Successful grounded results include one same-origin `result.evidence_url`. The
URL is a server-selected batch hydration link; clients do not pass citation IDs.
`GET /api/v1/agent/jobs/{job_id}/evidence` rechecks ownership, current scope and
permission revision, entitlement, source rights/review/index/extraction revisions,
episode/timing and stable IDs before returning a bounded private response:

```json
{"job_id":"00000000-0000-0000-0000-000000000021","items":[{"citation_id":"00000000-0000-0000-0000-000000000022","excerpt":"..."}]}
```

The endpoint returns `404` indistinguishably for unknown, cross-owner, stale,
revoked, malformed, or no-longer-authorized evidence. It is `private, no-store`.
Raw transcript text is never stored in job rows or lifecycle events; it appears
only in this authenticated hydration response. Graph citations additionally
expose bounded, trusted entity IDs/kinds/display names, predicate, polarity,
hop distance, and score in `citation.graph`; labels are projected from validated
GraphRAG records rather than accepted from model-supplied graph data.

`GET /api/v1/agent/jobs/{job_id}/events` is `text/event-stream`. Events have
monotonic numeric IDs and compact JSON data. Send `Last-Event-ID` to replay only
events after that sequence and reconnect safely. The stream follows queued,
running, and terminal events, emits bounded heartbeats while waiting, and closes
after a terminal event or configured duration/event limit. Responses set
`Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`.

Jobs and append-only replay events are durable in SQL as of Phase 34. Owner/key
idempotency, status/event transitions, deterministic event IDs, and sequence
uniqueness are enforced transactionally. At startup, before readiness can pass, the
single recovery supervisor atomically changes every interrupted `running` job back to
`queued`, appends the next contiguous event with `{"status":"queued","recovered":true}`,
and dispatches persisted queued work. It continues bounded scans so temporary local
dispatcher saturation leaves recovered work queued for a later attempt. A process-local
scheduled-job set prevents repeated scans from admitting duplicate callbacks, while the
repository claim remains the final single-winner guard.

Recovery replays the exact frozen job input stored in SQL; it does not restore a model
call at an instruction boundary. The bounded dispatcher and LangGraph checkpoint remain
process-local, so a recovered turn runs again from its persisted request and any
multi-turn checkpoint state lost with the process is not reconstructed. Durable
LangGraph checkpointing is separate work. Stable errors are `401` unauthenticated,
`404` missing/cross-owner, `409` idempotency conflict, `422` malformed request or replay
cursor, and `503` unavailable job system.

The normal state machine is `queued -> running -> succeeded`,
`queued -> running -> safe_refusal`, or `queued -> running -> failed`. Recovery adds
the only backward edge, `running -> queued`, after which the normal claim and terminal
rules apply again. Multiple recovery cycles preserve one contiguous event sequence.
Dispatcher saturation or shutdown performs an atomic `queued -> failed`
rejection for a newly submitted request and emits exactly one matching terminal event;
recovery scans instead leave already-persisted work queued. Reconnect clients use
the last numeric event ID; for example, `Last-Event-ID: 2` emits only events
with IDs greater than 2 before following live events. Terminal event append and
the terminal state are serialized by the repository boundary, so a stream never
closes merely because a separately-read status is terminal.

This recovery design deliberately supports exactly one API process and one recovery
supervisor. PostgreSQL holds a session advisory lock for the supervisor lifetime; a
second API worker or replica therefore fails startup rather than touching live work.
Operators must configure one Uvicorn worker and use a stop-then-start replacement. A
per-job leased multi-worker queue is a later architecture phase.
