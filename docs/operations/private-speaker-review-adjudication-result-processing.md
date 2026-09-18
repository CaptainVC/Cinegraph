# Private speaker-review adjudication-result processing

Phase 77 implements the secretless, provider-free transition after every
adjudication part has been observed. It computes decisions locally and either
finalizes a fully resolved run or prepares final-review request parts. It does
not call OpenAI, submit final review, promote reviewed files, ingest data, or
open a human-review workflow.

## Required checkpoint

The selected run must be canonical, private, and in
`adjudication_part_completed` with a positive part count and
`adjudication_completed_part_count == adjudication_part_count`. Every primary
and adjudication part must have exactly one request, intent journal, completion
journal, and output. An API-error artifact is optional per part, but any present
artifact participates in exact custom-ID reconciliation. Batch and input-file
IDs must be complete, unique, and agree with their journals.

The authorization ceiling must be positive and no greater than 5 USD in
micro-USD. Processing also enforces the run's persisted maximum and the
centralized speaker-review maximum. The root host boundary that will validate
and consume this authorization is intentionally deferred to the next phase;
do not expose the container command directly to an operator or SSH account.

## Isolated worker contract

The Compose profile is
`corpus-speaker-review-process-adjudication-results`. Its worker accepts only a
canonical operation request plus six expected SHA-256 digests: run state,
source artifacts, requests, submission journals, provider outputs, and derived
artifacts. It rejects provider-key and proxy environment variables, constructs
the application workflow with an offline gateway, and runs with no network or
secret mount.

The preflight inventory is exact. Unknown requests, journals, outputs, files,
or directories stop processing. A fresh run may contain a deterministic prefix
of the three new adjudication-derived artifacts for crash recovery, but every
existing byte must match recomputation. The postflight inventory verifies the
canonical persisted state, exact expected shape, stable directory identity,
immutable evidence, and unchanged pre-existing derived bytes.

## Outcomes

`completed` means every candidate was resolved and the exact reviewed subtitle
set, review ledger, and calibration sample were written locally.
`final_review_prepared` means unresolved candidates remain and deterministic
final-review request parts were written, while all final-review provider IDs,
completion counters, retry counters, and actual costs remain empty or zero.
`already_processed` is a byte-valid replay of either checkpoint. The response
contains only status, part and candidate counts, decision counts, and integer
micro-USD costs. Actual costs are conservatively rounded up to whole micro-USD,
and the response repeats the request's exact micro-USD authorization ceiling;
the future root coordinator must reject any total above that ceiling.

Any missing, extra, changed, non-canonical, over-budget, symlinked, or
inconsistent evidence fails closed. Preserve the run directory for explicit
reconciliation; never fabricate a state file, request part, ledger, digest, or
receipt. Final-review submission and the production VPS authorization/receipt
boundary are separate operations.
