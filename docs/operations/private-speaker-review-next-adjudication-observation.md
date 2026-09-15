# Private speaker-review next adjudication observation

The Phase 72 command is `speaker-review-observe-next-adjudication-v1`.
It is a one-shot operation for Terra part two only: the root coordinator
requires `adjudication_submitted`, exactly two batch/input IDs, and one
completed part, and verifies the Phase 71 intent/completed journals and root
receipt before authorizing the isolated worker.

`waiting` is safe to retry and does not mutate the run. On success the worker
retrieves the active batch once, downloads only its output/error artifacts,
and transitions the run to `adjudication_part_completed` with two completed
parts. A retry after a durable success is provider-free and returns
`already_observed` after validating its existing root receipt. If a terminal
state exists without that receipt, the command requires operator reconciliation:
the earlier intent alone cannot authenticate the downloaded bytes. This applies
to both successful and failed observations.

Status retrieval and each output download use zero SDK retries and a centralized
60-second timeout. The worker receives only the selected run directory as a
writable mount. Transfer, deployment, and review locks cover the operation;
the root coordinator checks the resulting inventory before publishing a receipt.

Stop and investigate any reconciliation, inventory, request-hash, runtime,
authorization, or symlink rejection. Do not parse Terra output, submit part
three, enter final review, promote, or ingest from this command.
