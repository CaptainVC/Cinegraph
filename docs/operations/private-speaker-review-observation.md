# Private speaker-review observation

Phase 63 exposes the bounded Phase 62 observer through the dedicated review SSH
identity. One invocation checks the first Season 2 primary Batch part submitted
by Phase 61. It cannot submit, advance, observe a later part, adjudicate,
finalize, promote, or ingest.

## Preconditions

The review host bootstrap/check must include both exact review helpers, the
observation receipt directory, and the unchanged review public key. The active
release must be clean, immutable, and equal to `origin/main`. Its pinned image,
configuration hash, Phase 60 preparation receipt, Phase 61 submission receipt,
workflow submission journals, and run state must agree.

Treat preparation, submission, and all pending observations as one deployment
freeze window. Do not promote another Dev release while the Batch is pending:
the preparation receipt deliberately binds the active release, image, and
configuration, so a deployment change makes later polling fail closed. Finish
or explicitly reconcile the run before resuming Dev promotion.

Create a new root-owned authorization file containing the exact canonical Phase
63 request:

```text
/opt/cinegraph/shared/private-corpus/dev/speaker-review/authorization/<observation-authorization-id>.json
```

The authorization UUID should be unique to this operation. The file is root-owned,
mode `0600`, and create-once. It contains only archive SHA-256, run ID,
authorization UUID, maximum cost in micro-USD, operation, purpose, schema version,
and season. Never reuse or edit the Phase 61 submission authorization.

Do not put the authorization, private digest, provider IDs, output JSONL, run
paths, or OpenAI key in GitHub, tickets, command transcripts, or shared logs.

## Observe from the workstation

Use the same dedicated review identity and pinned host-key file provisioned for
primary submission:

```powershell
uv run python scripts/observe_private_speaker_review.py `
  --archive-sha256 <private-object-sha256> `
  --run-id speaker-review-<16-lowercase-hex> `
  --authorization-id <observation-authorization-uuid> `
  --maximum-authorized-cost-microusd <approved-cost-microusd> `
  --identity <review-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host-placeholder>
```

The client uses strict host-key checking, one identity, no forwarding, no TTY,
no password fallback, and the exact forced command. Its canonical request contains
no path, model override, provider ID, payload, or credential.

## Outcomes and replay

| Status | Meaning and next action |
| --- | --- |
| `waiting` | The provider job is still non-terminal. No completion receipt exists; the same exact authorization may be used for a later bounded observation. |
| `observed` | The active part was downloaded and state reached `primary_part_completed`. A root completion receipt now binds the post-state and artifacts. |
| `already_observed` | A matching root receipt and postconditions were revalidated; no container or provider client was started. |
| `failed` | The provider reported a configured terminal failure. The failed state and any terminal error artifact are retained; no replacement Batch is submitted. |
| `reconciliation_required` | Evidence is ambiguous or conflicting. Stop automation and reconcile from retained root/run evidence. |

Do not delete or rewrite an intent, receipt, workflow journal, output, error file,
or run state to make a replay pass. A crash after output download but before the
state write may safely re-enter the observer because identical create-once output
is accepted. A crash after the legal state transition but before the root receipt
is repaired by receipt validation only; it does not call the provider again.

## Security boundary

The root coordinator has no OpenAI secret. It binds the request digest to the
exact nested directory:

```text
<runs-root>/sha256-<archive-digest>/review-runs/<run-id>
```

The observation container receives only that `review-runs` mount, the approved
non-secret bindings, and `/run/secrets/openai_api_key`. It has egress only, a
read-only root filesystem, no privileges, bounded resources, and no source corpus,
database, Qdrant, application knowledge, or preparation receipt mount. All errors
returned to the workstation are generic and bounded.

This phase is manually invoked. Do not wrap the command in an unattended loop.
The later scheduler phase will define a single-run queue, polling cadence, maximum
age, and terminal reconciliation behavior.
