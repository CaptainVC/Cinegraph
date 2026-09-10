# Private speaker-review next-primary observation

This boundary observes exactly submitted primary part two for one prepared
Season 2 speaker-review run. It is available only through the pinned
`cinegraph-review` SSH identity and forced command
`speaker-review-observe-next-primary-v1`.

It does not submit a Batch, choose a different part, parse output, adjudicate,
finalize, promote corpus data, or ingest into PostgreSQL/Qdrant.

## Preconditions and freeze window

The run must have matching Phase 60 preparation, Phase 61 primary-submission,
Phase 63 part-one-observation, and Phase 65 part-two-submission evidence. Its
state must be `primary_submitted`, with exactly one of two primary parts already
completed and complete part-one/part-two submission journals.

Treat all four predecessor phases and this observation as one release freeze
window. The active checkout must be a clean, root-owned release equal to
`origin/main`; its release SHA, immutable image digest/OCI labels, and reviewed
configuration must match the predecessor receipts. Do not edit the private run,
authorization, intent, journal, or receipt files.

After deploying the accepted release, refresh the review boundary and verify it:

```bash
sudo python3 /opt/cinegraph/current/scripts/bootstrap_review_host.py \
  --public-key-file <review-public-key> \
  --expected-key-fingerprint <review-key-fingerprint> \
  --corpus-public-key-file <corpus-public-key> \
  --expected-corpus-key-fingerprint <corpus-key-fingerprint> \
  --expected-deploy-key-fingerprint <deploy-key-fingerprint> \
  --refresh-review-code --apply

sudo python3 /opt/cinegraph/current/scripts/bootstrap_review_host.py \
  --public-key-file <review-public-key> \
  --expected-key-fingerprint <review-key-fingerprint> \
  --corpus-public-key-file <corpus-public-key> \
  --expected-corpus-key-fingerprint <corpus-key-fingerprint> \
  --expected-deploy-key-fingerprint <deploy-key-fingerprint> \
  --check
```

Bootstrap accepts only the known one-, two-, three-, or four-command sudo policy
states. It installs the new helper before expanding sudo and replaces the
dispatcher last, so the new command is never reachable before its implementation.

## Authorize one observation

Create one root-owned canonical JSON authorization with mode `0600` at:

```text
/opt/cinegraph/shared/private-corpus/dev/speaker-review/authorization/<fresh-uuid4>.json
```

The exact fields are: archive SHA-256, run ID, fresh lowercase UUID4,
maximum authorized micro-USD, operation `observe_next_primary`, purpose
`speaker_review`, schema version `1`, and season number `2`. Reusing a Phase 61,
63, or 65 authorization is rejected.

The authorization is a cost ceiling and identity binding. It contains no API
key, provider Batch/file ID, model choice, subtitle content, or host path.

## Invoke from the trusted workstation

```powershell
uv run python scripts/observe_next_private_speaker_review.py `
  --archive-sha256 <private-object-sha256> `
  --run-id speaker-review-<16-lowercase-hex> `
  --authorization-id <fresh-observation-uuid4> `
  --maximum-authorized-cost-microusd <approved-cost-microusd> `
  --identity <review-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host-placeholder>
```

The client requires strict host-key checking, no forwarding, no TTY, no password
fallback, and the exact forced command. Any remote stderr, oversized output,
non-canonical JSON, unknown field, or inconsistent status/count pair is rejected.

## Expected outcomes

| Status | Durable effect | Operator action |
| --- | --- | --- |
| `waiting` | No root completion receipt; run and evidence are unchanged. | Retry the same exact authorization later. |
| `observed` | Part-two output and updated state are verified; a `.part-0002` receipt is created once. | Continue only with a separately designed adjudication/advance phase. |
| `already_observed` | Existing terminal state and receipt chain were revalidated; no provider request occurred. | Treat as successful idempotent replay. |
| `failed` | Terminal provider failure evidence and state are verified; a `.part-0002` receipt is created once. | Diagnose from protected VPS evidence; do not mutate it. |
| `reconciliation_required` | Provider outcome may be ambiguous; no completion receipt is created. | Stop automation and preserve all evidence for explicit reconciliation. |

No aggregate response or root receipt contains provider identifiers, private
subtitle text, prompts, model output, secret material, or private filesystem
paths.

## Security checks and recovery

Before secret access, the worker must match the root-bound target part, state
SHA, part-two request SHA, non-journal artifact digest, and journal digest. It
uses the verified in-memory state in LangGraph instead of loading the mutable
path again. Any mismatch fails before a provider call.

If the client reports rejection, preserve the authorization, root intent,
predecessor receipts, run state, workflow journals, and any provider output.
Check the active release/image/configuration and host bootstrap contract. Never
delete an intent or fabricate a receipt to retry. Receipt repair is safe only
when the coordinator recognizes an already-terminal transition and revalidates
every immutable predecessor binding.
