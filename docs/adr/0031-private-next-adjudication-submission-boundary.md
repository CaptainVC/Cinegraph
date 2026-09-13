# ADR-0031: Private next-adjudication submission boundary

## Status

Accepted for Phase 71.

## Context

Phase 70 observes the first submitted Terra adjudication part and stops at the
explicit `adjudication_part_completed` checkpoint. The general speaker-review
`advance` operation is too broad for the following paid transition: it can
observe provider output, submit several later parts over time, parse verdicts,
enter final review, finalize decisions, and approach corpus publication.

The application transition should work for any completed adjudication part
`k`, because request partition counts are data-dependent. The current VPS trust
chain, however, has a root receipt only for observation of part one. Allowing a
host command to submit part three or later before a separately authorized
part-`k` observation boundary exists would trust mutable worker-owned files as
the sole proof of the predecessor.

## Decision

Add the narrow LangGraph operation `submit-next-adjudication`, backed by
`SpeakerReviewWorkflow.submit_next_adjudication_part`. It accepts only
`adjudication_part_completed` with `0 < k < adjudication_part_count`, validates
the exact `k` batch/input identifiers, every completed output, and all matching
create-once submission journals, then submits exactly request part `k + 1`.
The resulting checkpoint is `adjudication_submitted`; the completed count stays
at `k` and exactly one batch/input identifier is appended. Even when `k + 1` is
the final part, this operation does not observe or interpret it.

An exact submitted replay validates the active request and journal binding with
a provider-disabled gateway. A matching application intent/completed pair can
repair a crash between provider submission and state persistence without a
second provider call. Intent-only, completed-only, conflicting, malformed, or
output-bearing active-part evidence requires reconciliation.

Expose the transition on the VPS as the eighth finite command on the dedicated
`cinegraph-review` SSH identity:
`speaker-review-submit-next-adjudication-v1`. For this phase, the privileged
coordinator deliberately requires `adjudication_completed_part_count == 1` and
therefore submits only part two. The application and isolated worker remain
generic, but the host boundary will not support part three until an authenticated
part-two observation receipt exists.

The canonical request contains only the archive digest, run ID, fresh
authorization UUID, micro-USD ceiling, fixed operation, purpose, season, and
protocol version. The root intent binds the authorization bytes; Phase 70
intent and receipt; preparation receipt; active release, immutable image, and
configuration; exact pre-state; five inventory digest classes; the target
request digest; completed/total counts; and the deterministic total Terra cost
estimate. Existing root receipts are accepted only after recomputing that full
binding and revalidating the active runtime.

The Compose worker mounts only the digest-selected `review-runs` parent
read-write and receives the OpenAI key as a mode-`0400` secret. It validates all
five inventory digests plus the exact target request digest, run-directory
identity, journals, outputs, state shape, and cost ceiling before reading the
secret. The cost check recomputes the complete adjudication estimate from every
prepared Terra request part, matching the Phase 69/70 authorization semantics.

The worker runs as UID/GID `10002`, with a read-only root filesystem,
no-new-privileges, all capabilities dropped, bounded processes/memory/CPU, and
only the existing `egress` network. That network is outbound-capable and is not
an OpenAI-only destination allowlist; the root-owned immutable release, Compose
definition, and Docker daemon remain in the trusted computing base.

After the worker exits, root re-inventories the run. Every preexisting artifact,
output, derived file, and journal must be byte-identical. Only the target
part-two intent/completed journals and the narrowly defined state transition are
allowed. Root then validates the new application journals and exact worker
aggregate before writing a create-once aggregate-only receipt. Submitted-state
replay can repair a missing root receipt without provider or secret access.

## Consequences

Part two can be submitted through a separately authorized, auditable, bounded,
and crash-safe production boundary. The reusable application primitive does
not need to be redesigned when later-part observation receipts are introduced.

This phase does not observe part two, submit part three from the VPS, parse
Terra output, calculate adjudication decisions, enter final review, perform
human review, promote corpus files, or ingest PostgreSQL/Qdrant.

See the [next-adjudication submission runbook](../operations/private-speaker-review-next-adjudication-submission.md).
