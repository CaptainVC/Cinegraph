# ADR-0040: Private final-review observation boundary

## Decision

Phase 80 adds `speaker-review-observe-final-review-part-one-v1` as a separate
root-coordinated observation command. It first authenticates the exact Phase79
submission claim, intent and receipt, application journals, submitted run
inventory, and Phase78 predecessor chain. A single isolated egress worker may
retrieve only part one using those digest, image, configuration, model, prompt,
endpoint, window, and budget bindings.

Waiting leaves the run untouched. Success is accepted only when part-one output
is durable, the completed count is one, and the run remains
`final_review_submitted`. Failure retains a zero completed count. Root intents
and receipts are write-once; exact receipt replay needs no provider access and
ambiguous or partial transitions require reconciliation.

## Explicit exclusions

This command cannot submit, observe another part, retry, parse, finalize, ask a
human to decide, promote, or ingest. It returns only aggregate status, counts,
and cost values. Provider identifiers, paths, prompts, payloads, and secret
contents remain private.
