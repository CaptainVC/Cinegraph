# ADR-0036: Private fourth-adjudication observation boundary

## Status

Accepted

## Context

Phase 75 submits Terra adjudication part four and leaves the review run in an
authenticated `adjudication_submitted` checkpoint with three completed parts.
Provider status is asynchronous, so observation must be separately authorized
and must not be able to submit another part, parse private output, or enter a
downstream review stage.

## Decision

Expose the forced command `speaker-review-observe-fourth-adjudication-v1`
(`observe_fourth_adjudication`) through its own pinned client, host coordinator,
Compose profile, isolated worker, and aggregate-only receipt directory. The
root coordinator accepts only the Phase 75 submitted state (`completed_count ==
3`, `part_count > 3`, and exactly four batch/input IDs including the active
part), then authenticates the Phase 75 submission receipt and its complete
Phase 74 prefix chain before reading any secret or starting a worker.

The worker uses the existing generic next-part observation primitive only with
the Phase 76 constants pinned to part four. It performs at most one provider
retrieve with the centralized zero-retry transport and a finite timeout. A
successful terminal provider snapshot downloads only the part-four output and
transitions to `adjudication_part_completed` with count four. Incomplete work
returns `waiting` without mutation. A terminal failure writes only the
terminal-error evidence and a failed checkpoint; any crash or missing root
receipt stops closed and requires explicit reconciliation rather than
fabricating evidence.

Replay is provider-free and requires the authenticated root intent/receipt,
the full predecessor chain, and matching immutable inventory digests. Receipts
contain only deterministic aggregate metadata and hashes; provider payloads,
secrets, parsed adjudication, part four, final review, promotion, and ingestion
remain outside this boundary.

The worker is constrained to the digest-selected run mount and mode-0400
Compose secret, runs as UID/GID `10002:10002`, has read-only rootfs, a noexec
temporary filesystem, no added capabilities, `no-new-privileges`, PID limit
128, and only the egress network. The root double-reads inventory and validates
runtime identity before publishing a create-once receipt.

## Consequences

Observation can be safely retried by an operator while preserving the exact
one-retrieve-per-command-invocation boundary. Waiting is idempotent and side-effect free;
successful and failed terminal outcomes are replayable only from authenticated
receipts. Any ambiguous state requires explicit reconciliation, keeping
private Terra data and downstream publication out of the release boundary.
