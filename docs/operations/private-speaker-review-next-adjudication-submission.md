# Private speaker-review next-adjudication submission

Phase 71 submits exactly Terra adjudication part two after the separately
authorized Phase 70 observation has durably completed part one. It is a paid
provider write, so each invocation requires an explicit root-owned authorization
record and uses the dedicated review SSH identity.

## Preconditions

- Dev is deployed from an immutable release whose commit equals `origin/main`.
- The review host bootstrap check reports `review-observe-ready` (the label is
  retained for compatibility while all eight review commands are installed).
- Phase 60-70 preparation, submission, observation, and processing receipts are
  present and root-owned.
- The run is at `adjudication_part_completed`, completed count `1`, total count
  greater than `1`, with no part-two output.
- The operator has independently selected the expected archive digest and run
  ID and accepts the requested maximum cost.

## Authorize and invoke

Generate a new UUIDv4. Write the exact canonical request below as a root-owned,
mode-`0600` file under the review authorization directory. Do not reuse an
authorization from a different command, run, archive, or cost ceiling.

```json
{"archive_sha256":"<64 lowercase hex>","authorization_id":"<uuid-v4>","maximum_authorized_cost_microusd":5000000,"operation":"submit_next_adjudication","purpose":"speaker_review","run_id":"speaker-review-<16 lowercase hex>","schema_version":1,"season_number":2}
```

Invoke through the pinned client (replace the paths and host with operator-owned
values):

```zsh
uv run python scripts/private_speaker_review_next_adjudication_submission_client.py \
  --archive-sha256 <digest> \
  --run-id <run-id> \
  --authorization-id <uuid-v4> \
  --maximum-authorized-cost-microusd 5000000 \
  --identity <review-private-key> \
  --known-hosts <pinned-known-hosts> \
  --host <review-host>
```

The response is canonical aggregate JSON and never contains provider batch/file
IDs, prompts, transcript text, verdicts, rationales, secret values, or private
paths.

## Outcomes

- `submitted`: part two was submitted or an exact completed application journal
  repaired the state without another provider call; the root receipt was written.
- `already_submitted`: the submitted checkpoint and complete root binding were
  replayed provider-free; a missing root receipt may have been repaired.
- `reconciliation_required`: evidence is ambiguous (for example intent-only);
  no automatic provider retry is permitted.

On success, confirm the state is `adjudication_submitted`, completed count is
still `1`, the ID arrays contain exactly two unique entries, the part-two
intent/completed journals exist, and no part-two output exists. Do not edit
private run files or attempt a manual resubmission.

## Failure handling

All public errors are deliberately generic. Preserve the authorization, root
intent/receipt directory, application journals, active release SHA, and host
logs before investigating. Never delete an intent to force a retry: the
provider call may have succeeded even if the caller did not receive a response.

Part two must be observed by the next separately authorized phase. Part three
or later remains blocked at the VPS boundary until its immediate observed-part
receipt can be authenticated.
