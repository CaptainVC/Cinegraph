# ADR-0037: Provider-free adjudication-result processing checkpoint

## Status

Accepted

## Context

Phase 76 can leave a run with every adjudication part observed, but observation
intentionally does not parse private model output or decide what happens next.
The next transition must be deterministic and replayable without broadening the
provider boundary. It must also support any positive part count rather than
embedding the current four-part corpus shape in application logic.

## Decision

Add the LangGraph operation `process-adjudication-results` and the explicit
`final_review_prepared` run status. The operation accepts only
`adjudication_part_completed` with `completed_count == part_count > 0`, or an
already processed replay checkpoint. It validates the complete primary and
adjudication request, submission-journal, output, optional API-error, provider
identifier, model, prompt, cost, and custom-ID evidence before parsing.

The transition recomputes the primary verdicts and consensus decisions, parses
all adjudication output, applies adjudication, and writes create-once canonical
`adjudication-verdicts.jsonl`, `adjudication-parse-errors.json`, and the
configured final-decision artifact. Existing derived artifacts must match the
same deterministic bytes, so a retry can recover from an interrupted sequence
but cannot bless altered partial output.

If every candidate is resolved, processing renders the reviewed subtitles,
ledger, and calibration sample locally and ends at `completed`. If any
candidate remains unresolved, it estimates the final-review cost against the
persisted run ceiling, the fresh authorization ceiling, and the centralized
configuration ceiling; writes deterministic final-review request parts; and
stops at `final_review_prepared`. Provider identifiers, completed counts, and
actual final-review cost remain empty. A later submission revalidates every
prepared request byte before making a provider call.

Expose the transition through a strict aggregate-only wire contract and a
dedicated Compose worker. The worker uses an offline gateway, rejects ambient
provider credentials and proxy settings, has `network_mode: none`, a read-only
root filesystem, a bounded noexec temporary filesystem, UID/GID `10002:10002`,
dropped capabilities, `no-new-privileges`, and bounded CPU, memory, and PIDs.
It validates a digest-bound exact inventory before processing and verifies that
immutable evidence and pre-existing derived bytes remain unchanged afterward.
Actual costs are rounded up to whole micro-USD in the aggregate response, and
the response carries the exact authorization ceiling so a later root boundary
can independently enforce the request-specific limit.

This phase deliberately does not add a root forced command, pinned SSH client,
host authorization consumption, or create-once root receipt. Those controls
form the next VPS boundary, which must bind the archive digest and run ID to
the digest-selected read-only source mount and writable run mount before
accepting the worker aggregate. It also performs no provider submission,
observation, corpus promotion, ingestion, or human-review action.

## Consequences

Adjudication results can be reduced to a deterministic local checkpoint without
provider access. The workflow is generic over future part counts, interrupted
writes can be retried only when their bytes agree, completed replay validates
the exact reviewed corpus evidence, and final-review submission starts from an
authenticated deterministic request set. Until the follow-up host boundary is
installed, operators must not invoke this worker as a production forced
command.
