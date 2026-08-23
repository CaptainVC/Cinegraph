# Agent jobs API

`POST /api/v1/agent/jobs` requires the existing HTTP-only cookie session and a
canonical `Idempotency-Key` UUID. Its JSON body is exactly:

```json
{"thread_id":"00000000-0000-0000-0000-000000000031","series_id":"00000000-0000-0000-0000-000000000011","question":"Who introduces the family?"}
```

The server derives and authorizes candidates. Guest Modern Family requests can
only receive seasons 1 and 2. The response is `202 Accepted`, includes a
`Location` status URL and absolute status/events URLs, and never includes
candidate IDs, scope, prompts, or raw transcript evidence.

`GET /api/v1/agent/jobs/{job_id}` returns the typed lifecycle and, on success,
grounded answer, stable tool names, and citation locators. Safe refusals contain
no answer or evidence. Unknown and cross-profile IDs both return `404`.

`GET /api/v1/agent/jobs/{job_id}/events` is `text/event-stream`. Events have
monotonic numeric IDs and compact JSON data. Send `Last-Event-ID` to replay only
events after that sequence and reconnect safely. The stream follows queued,
running, and terminal events, emits bounded heartbeats while waiting, and closes
after a terminal event or configured duration/event limit. Responses set
`Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`.

The queue, repository, and checkpoint are process-local in Phase 32 and are not
crash durable; a later persistence adapter will replace them. Stable errors are
`401` unauthenticated, `404` missing/cross-owner, `409` idempotency conflict,
`422` malformed request or replay cursor, and `503` unavailable job system.

The complete state machine is `queued -> running -> succeeded`,
`queued -> running -> safe_refusal`, or `queued -> running -> failed`.
Dispatcher saturation or shutdown performs an atomic `queued -> failed`
rejection and emits exactly one matching terminal event. Reconnect clients use
the last numeric event ID; for example, `Last-Event-ID: 2` emits only events
with IDs greater than 2 before following live events. Terminal event append and
the terminal state are serialized by the repository boundary, so a stream never
closes merely because a separately-read status is terminal.
