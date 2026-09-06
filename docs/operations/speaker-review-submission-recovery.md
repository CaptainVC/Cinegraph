# Speaker-review submission recovery

The speaker-review workflow can submit multiple paid Batch requests: primary
opinions, adjudication, final review, and targeted retry parts. A local crash can
happen after the provider accepts a request but before `run-state.json` records its
Batch ID. Repeating a submission without checking that gap can duplicate paid work.

## Submission journal

Each run/stage/part has a durable submission intent. The workflow writes the intent
before invoking the provider. The record binds the request content and submission
configuration to that exact logical part. After a successful provider response, the
workflow records the returned submission identifiers before advancing run state.

On resume:

- A matching completed journal entry supplies the existing Batch submission. The
  workflow does not submit it again, even if the later run-state write failed.
- An intent without a valid completed entry is ambiguous. The workflow stops with
  a reconciliation-required error and makes no new submission.
- Changed request bytes, mismatched metadata, or malformed journal records are
  rejected. A journal entry is never silently repurposed for another request.

This provides an at-most-one automatic submission attempt per journalled part. It
does not claim transactional exactly-once execution across a filesystem and a
remote provider. In particular, a remote timeout is not proof that the request was
rejected.

The adapter disables SDK retries for file upload and Batch creation and applies a
centralized 60-second timeout. Read-only polling keeps the caller's normal SDK
configuration. The [OpenAI Python SDK documentation](https://developers.openai.com/api/reference/python#retries)
describes default retries for connection errors and selected HTTP failures; allowing
those retries during creation would undermine the journal's one-attempt boundary.

## Handling an ambiguous attempt

Keep the run directory, intent, request part, and existing provider artifacts intact.
Do not delete the intent or create a new run solely to bypass the error. Do not
submit the same part from the provider console.

An operator must inspect the provider's Batch history in a private authenticated
session and correlate the run, stage, part, and request with the journal. A provider
record may take time to become visible. Do not infer that a Batch does not exist
from one empty listing immediately after a timeout.

There is deliberately no automatic repair command in this phase. Once the remote
outcome is established, a later reviewed reconciliation operation must validate and
bind the remote identifiers before normal advancement can resume. If the outcome
cannot be established, the run remains blocked instead of risking duplicate spend.

## Privacy and compatibility

Journal files remain beneath the ignored private review run and contain no subtitle
text or absolute corpus paths. Provider identifiers and request hashes are private
operational metadata; do not copy them to GitHub comments or public logs.

Existing runs whose active Batch IDs were already saved can continue their normal
polling path. New submission attempts use the journal. A pre-upgrade crash in the
old unjournalled submit/save gap cannot be detected retrospectively: reconcile such
a run before attempting a new submission.

This change addresses the provider submission gap. It does not complete the broader
source-path confinement, immutable resume-artifact verification, canonical reviewed
output promotion, or Season 2 VPS review-worker work. A paid Season 2 run remains
deferred until those boundaries are ready.
