# ADR-0024: Bounded next-primary submission transition

- Status: accepted for implementation
- Date: 2026-09-09

## Context

The Phase 63 host boundary can persist an observed primary Batch part as the
explicit `primary_part_completed` checkpoint. The legacy
`SpeakerReviewWorkflow.advance()` method cannot safely consume that checkpoint:
one invocation may observe a Batch, submit another primary part, parse all
primary output, submit adjudication, finalize reviewed subtitles, or enter later
review stages. Those effects have different authority and recovery rules.

Partitioned primary review must submit every request part before combined result
processing is meaningful. Processing only part of the candidate set could create
incomplete consensus and an invalid adjudication queue. The application therefore
needs one transition that can perform a single subsequent paid Batch creation and
then stop.

## Decision

Add the dedicated LangGraph operation `submit-next-primary`. It routes only
through `load -> submit_next_primary -> end` and invokes
`SpeakerReviewWorkflow.submit_next_primary_part()` rather than `advance()`.

The transition accepts an exact `primary_part_completed` state. Completed counts,
Batch/input ID arrays, and legacy singleton IDs must describe every completed
part with no active part. When another part exists, the operation submits exactly
the next numbered primary request, appends one Batch/input ID pair, changes the
state back to `primary_submitted`, preserves the completed count, saves the state,
and stops. Existing per-part intent/completed journals provide create-once
semantics: a matching completion can repair state without a second provider
submission, while an unresolved intent or changed request requires reconciliation.

A valid post-checkpoint `primary_submitted` state is an idempotent replay only
when at least one part was previously completed and the arrays contain exactly
those completed parts plus one active submission. This deliberately rejects the
Phase 61 first-submission state. If all parts are complete, the operation is an
identity and does not access the provider.

Add a versioned internal worker contract and the isolated
`corpus-speaker-review-submit-next-primary` Compose profile. Before reading the
OpenAI secret or constructing a client, the worker validates:

- the canonical run and request contract;
- the operation-specific authorization UUID, archive digest, and micro-USD cap;
- the centralized run cost ceiling;
- every completed output and matching immutable submission journal;
- the exact next request artifact; and
- the state shape required for a one-part transition.

The worker accepts only the digest/run/authorization/cost bindings in its
environment, expects only the fixed `review-runs` mount, and reads the API key
from a Compose secret file. Its aggregate output contains counts, cost, run ID,
season, purpose, operation, and one of `submitted`, `already_submitted`,
`all_parts_completed`, or `reconciliation_required`. Provider IDs, source paths,
request/output content, prompts, and credentials never cross the boundary.

## Consequences

One invocation can create at most one paid primary Batch and cannot poll,
download provider output, parse verdicts, decide consensus, submit adjudication,
submit final review, finalize, promote, or ingest. A multi-part run alternates
between separately bounded observation and next-part submission checkpoints.

This phase deliberately provides only the application/LangGraph/container
primitive. [ADR-0025](0025-private-speaker-review-next-primary-boundary.md) adds
the root coordinator, forced SSH command, and authorization/receipt store for
the first part-one-to-part-two use. Primary-result processing remains a separate
zero-new-spend transition after all parts are observed.

See the [next-primary transition runbook](../operations/private-speaker-review-next-primary-submission.md).
