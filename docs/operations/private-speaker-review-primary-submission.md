# Private speaker-review primary submission

Phase 61 is the explicit paid transition after Phase 60 preparation. It
submits only the first primary Batch part for a prepared Season 2 run. It does
not upload the private bundle, read the source corpus, poll a Batch, adjudicate
results, or promote reviewed subtitles.

## Preconditions

The dedicated review identity, root helper, root authorization directory, and
the pinned active release must pass the host bootstrap/check procedure. A
matching Phase 60 preparation receipt and run must already exist. Keep the
authorization and all host evidence on the VPS; do not put private digests,
provider identifiers, request JSONL, paths, or credentials in tickets, GitHub,
shell history, or shared logs.

The root authorization file is the exact canonical submission request, stored
as:

```text
/opt/cinegraph/shared/private-corpus/dev/speaker-review/authorization/<authorization-id>.json
```

It contains exactly the fields accepted by
`private_speaker_review_submission_contract.py`: archive SHA-256, authorization
UUID, maximum authorized cost in micro-USD, operation, purpose, run ID, schema
version, and season. The file is root-owned, mode `0600`, and is never edited
after approval. The request sent by the workstation must match it byte for byte
after canonical validation.

## Submit

Use the pinned-SSH workstation client with placeholders for local key files and
the host. The client sends only the digest/run/authorization/cost request; it
does not transfer a bundle or request artifacts:

```powershell
uv run python scripts/submit_private_speaker_review.py `
  --archive-sha256 <private-object-sha256> `
  --run-id speaker-review-<16-lowercase-hex> `
  --authorization-id <approved-authorization-uuid> `
  --maximum-authorized-cost-microusd <approved-cost-microusd> `
  --identity <review-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host-placeholder>
```

The successful response is aggregate-only. It contains the run ID, part count,
rounded estimate in micro-USD, and one of `submitted`, `already_submitted`, or
`reconciliation_required`. It never contains a Batch ID, input file ID, source
filename, request body, absolute path, or secret.

## Durable state and replay

Before starting Compose, the root coordinator writes a create-once intent under
the root-owned `submission-receipts` directory. It binds the authorization,
preparation receipt, exact prepared artifact hashes, run, part count, and cost.
The workflow then writes its own primary intent before invoking the provider.
After the provider returns, it writes the completed journal and updates
`run-state.json`; only after those records pass strict inventory and hash checks
does the root coordinator create the final submission receipt.

The safe replay matrix is:

| Evidence | Coordinator action |
| --- | --- |
| Matching final receipt and matching post-submit evidence | Return `already_submitted`; start no worker. |
| Matching workflow intent and completed journal, root receipt missing | Start the worker only to repair state/receipt; the worker reuses the completed journal. |
| Workflow intent without completed journal | Return `reconciliation_required`; do not retry the provider. |
| Orphan completed journal | Return `reconciliation_required`; preserve the orphan for operator reconciliation. |
| Root intent, no workflow intent, same authorization | Safe retry of the missing worker start. |
| Different authorization or changed binding | Reject; do not overwrite or submit. |

Do not delete a run directory, journal, authorization, intent, receipt, or
provider evidence to make a retry pass. A malformed or changed record is a
fail-closed reconciliation event. Reconciliation must establish the provider
outcome before a separately reviewed operation changes the durable workflow
state.

## Boundary checks

The submit Compose service has egress only, runs unprivileged with a read-only
root filesystem, and receives the OpenAI key as a secret file. The coordinator
passes only the `review-runs` read-write mount and four non-secret request
variables. It does not mount `/private-corpus`, pass `OPENAI_API_KEY`, or pass
the request artifacts as command-line arguments. Worker stderr, malformed or
oversized output, timeout, changed state, unexpected files, and receipt
conflicts are generic failures with no private detail in the response.

The root helper is synchronous and externally time-bounded. If it is
interrupted, inspect the retained root and run evidence from the provider
console before attempting any follow-up action. Never use a glob or recursive
delete against a computed private path.
