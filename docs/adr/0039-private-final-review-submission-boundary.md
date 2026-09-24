# ADR-0039: Private final-review submission boundary

## Decision

Phase 79 adds the finite command `speaker-review-submit-final-review-v1`.
It accepts only an authenticated Phase 78 `final_review_prepared` checkpoint
and submits exactly final-review part one through the existing LangGraph
operation. The worker accepts an exact `final_review_submitted` replay without
provider access.

The root/VPS boundary binds archive, run, authorization, release image and
configuration, the Phase 78 processing intent/receipt chain, complete
artifact/request/journal/output/derived inventories, and the exact part-one
request digest. Root publishes intent and receipt records atomically; recovery
is create-once and hard-link safe.

## Explicit exclusions

This command cannot observe or download provider output, parse results, retry,
submit part two, finalize, promote, ingest, or read a generic `needs_human`
checkpoint. An intent-only or conflicting journal is
`reconciliation_required`; the worker never retries such a request.

Only a bounded aggregate is public. Provider IDs, paths, prompts, secret
contents, and provider payloads remain private.
