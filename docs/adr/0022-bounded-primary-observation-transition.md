# ADR-0022: Bounded primary-review observation transition

- Status: accepted for implementation
- Date: 2026-09-08

## Context

After the first primary speaker-review Batch has been submitted, the application
must learn whether that one provider job is still running, failed, or completed.
The existing `SpeakerReviewWorkflow.advance()` operation is not a suitable
production boundary for this check. A single call can download a result and then
submit another primary part, submit adjudication, finalize output, or enter later
review stages. Exposing it to a timer or remote command would combine observation,
new spend, and decision-making under one authorization.

The review pipeline needs smaller transitions so every provider action is
independently testable, recoverable, and authorized. Observation itself must not
grant authority for another paid submission.

## Decision

Add a dedicated `observe_primary` LangGraph operation with the fixed route
`load -> observe_primary -> end`. It accepts only a persisted
`primary_submitted` run. It retrieves the active primary Batch once and:

- returns the unchanged state while the provider status is non-terminal;
- records a durable failed state for a configured terminal provider failure; or
- downloads the active part's output and optional error artifact, increments the
  completed-primary-part count, persists `primary_part_completed`, and stops.

The operation never invokes the existing broad `advance()` method. It cannot
submit the next primary part, parse verdicts, run consensus, submit adjudication,
submit final review, finalize reviewed subtitles, or ingest them. Replaying a
`primary_part_completed` or terminal run ends after load without provider access.
The explicit intermediate status also prevents legacy advancement from silently
continuing the run.

Add a separate `corpus-speaker-review-observe-primary` Compose profile and
worker. The worker receives only the fixed, writable `review-runs` mount and four
non-secret authorization bindings. It validates the run and cost ceiling before
reading the OpenAI secret or constructing the provider client. The secret is a
Compose secret file, the container has egress only, and the response is an exact
aggregate allowlist with counts and a bounded status. Provider IDs, paths,
subtitle content, request bodies, payloads, and credentials never cross the
worker boundary.

The observation protocol is versioned independently from primary submission.
Its statuses are `waiting`, `observed`, `already_observed`, `failed`, and
`reconciliation_required`. Output artifacts remain private run evidence. An
existing differing output artifact is a reconciliation condition and is never
overwritten.

## Consequences

One invocation can perform at most one provider status lookup for one active
primary part, plus downloads for that same completed part. It cannot cause new
model spend. A completed part becomes an explicit checkpoint from which a later,
separately authorized operation may submit the next part or process the final
primary outputs.

This phase deliberately does not add a forced SSH command, root coordinator,
timer, or active-run queue. Those boundaries must first validate the same release,
image, configuration, authorization, and immutable evidence rules established by
the primary-submission coordinator. Until that subsequent phase is deployed, the
container profile is an internal primitive and is not an operator-facing remote
operation.

Operational constraints and expected outcomes are documented in the
[primary-observation runbook](../operations/private-speaker-review-primary-observation.md).
