# Private speaker-review next-primary submission

Phase 64 adds the internal transition that submits at most one subsequent
primary Batch part from an observed checkpoint. Phase 65 exposes its first
part-one-to-part-two use through the separately authorized
[`speaker-review-submit-next-primary-v1`](private-speaker-review-next-primary-boundary.md)
VPS boundary.

## Preconditions

The run must be in the canonical private `review-runs/<run-id>` layout and its
state must be `primary_part_completed`. The completed count must be positive and
no greater than the total part count. Batch and input-file ID arrays must contain
exactly one pair per completed part, with the singleton fields referring to the
last completed part.

Before the secret is opened, the worker verifies every completed part's request,
output, intent journal, and completed journal. Journal bindings must match the
request SHA-256, run ID, stage, part number, prompt version, endpoint, completion
window, and the provider IDs stored only in private state. If a next part exists,
its request artifact must also pass the bounded, link-safe snapshot policy.

The canonical estimated primary cost must fit both the operation's approved
micro-USD ceiling and the centralized maximum. The request includes a lowercase
archive SHA-256 and canonical UUIDv4 so a future root host boundary can bind the
worker invocation to its exact evidence. These values are not returned.

## Outcomes

| Result | Durable behavior |
| --- | --- |
| `submitted` | Exactly one next primary request was submitted, one ID pair was appended privately, state returned to `primary_submitted`, and execution stopped. |
| `already_submitted` | A valid next-part submission already exists; no provider access or secret read is required for a persisted post-submit replay. A matching workflow completed journal may also repair a missing state update without another provider submission. |
| `all_parts_completed` | Every primary part is observed. The state and artifacts are unchanged and neither secret nor provider is accessed. |
| `reconciliation_required` | Submission evidence is ambiguous or conflicts with its immutable binding. Do not retry automatically. |

Errors are deliberately generic. The aggregate never contains provider Batch or
file IDs, private paths, source/subtitle text, JSONL payloads, prompts, or secrets.

## Container boundary

`corpus-speaker-review-submit-next-primary` runs as UID/GID `10002`, with a
read-only root filesystem, dropped capabilities, no-new-privileges, bounded
CPU/memory/PIDs, and only the egress network. Its Phase 65 host invocation adds
only the exact digest-bound `review-runs` directory at
`/review-workspace/review-runs`. It has no corpus-source, PostgreSQL, Qdrant,
knowledge, preparation-receipt, or application-credential mount.

`OPENAI_API_KEY` is a Compose secret at `/run/secrets/openai_api_key`; it is never
an environment variable or command argument. The worker validates run, cost,
checkpoint evidence, and the next request before reading it.

## Prohibited shortcuts and next transition

Do not substitute the generic `advance` operation. Do not delete an intent,
completed journal, provider output, or state file to force a retry. Ambiguous
evidence requires explicit reconciliation because the provider may already have
accepted the paid submission.

Phase 65 supplies the root helper and forced SSH command with a fresh
authorization bound to Phase 60 preparation, Phase 61 submission, Phase 63
observation, the active release/image/configuration, and exact pre/post artifact
inventories. Observation of the newly submitted part and final primary-result
processing remain later, separate transitions.
