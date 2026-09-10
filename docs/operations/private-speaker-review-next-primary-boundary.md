# Private speaker-review next-primary boundary

This operation advances exactly one reviewed Season 2 run from an observed
primary part one to a submitted primary part two. It is available only through
the pinned `cinegraph-review` SSH identity and the forced command
`speaker-review-submit-next-primary-v1`.

Before invocation, the run must have matching Phase 60 preparation, Phase 61
first-submission, and Phase 63 observed receipts. The active release must be a
clean checkout of `origin/main`, and the request must use a fresh UUID4
authorization file with mode `0600`. The v1 operation rejects one-part or fully
observed runs; an exact receipt-bound replay of its own part-two submission is a
provider-free `already_submitted` result.

Treat preparation, submission, observation, and this transition as one release
freeze window. Changing the active release, image digest, or reviewed
configuration deliberately invalidates the evidence chain.

## Host activation

After deploying the accepted release, refresh the existing review identity with
`scripts/bootstrap_review_host.py --refresh-review-code`. The bootstrap accepts
only the finite primary-only, primary-plus-observation, or current three-command
sudo policies. It installs all helpers first, expands the exact sudo rule next,
and replaces the dispatcher last. Run the same bootstrap in `--check` mode after
activation. Do not reuse the corpus-transfer or deployment key.

Create one root-owned, create-once authorization at:

```text
/opt/cinegraph/shared/private-corpus/dev/speaker-review/authorization/<authorization-uuid>.json
```

Its canonical JSON is the exact request accepted by the client: archive SHA-256,
run ID, fresh lowercase UUID4, maximum authorized micro-USD, operation
`submit_next_primary`, purpose `speaker_review`, schema version, and season 2.
Never edit or reuse a prior preparation, submission, or observation authorization.

## Submit part two from the workstation

Use the dedicated review identity and pinned host-key file:

```powershell
uv run python scripts/submit_next_private_speaker_review.py `
  --archive-sha256 <private-object-sha256> `
  --run-id speaker-review-<16-lowercase-hex> `
  --authorization-id <fresh-next-primary-authorization-uuid> `
  --maximum-authorized-cost-microusd <approved-cost-microusd> `
  --identity <review-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host-placeholder>
```

The client permits one pinned identity, strict host-key checking, no forwarding,
no TTY, no password fallback, and only the forced command. Its wire request has
no provider identifier, private path, model override, subtitle content, or key.

The root coordinator creates a durable intent before starting Compose. It
passes only non-secret identifiers, the exact part-two request digest, and the
authorized cost cap. Compose runs `corpus-speaker-review-submit-next-primary`
with the OpenAI key as a secret file and only the exact writable
digest-selected `review-runs` mount. The expected Phase 63 artifact, journal,
and state digests are checked again before the secret is opened. That verified
state is supplied to LangGraph without reloading it, and the expected request
digest is checked against the exact bytes handed to the provider gateway.

Successful output is an aggregate such as `submitted` or
`already_submitted`; provider IDs and private content remain in the mounted
run directory. Do not invoke the operation again after a reconciliation error:
retain the intent and reconcile the provider and journals first. A completed
part-two journal with a missing state publication is repaired locally and does
not submit another Batch.

| Status | Meaning and next action |
| --- | --- |
| `submitted` | Exactly part two was created and the run returned to `primary_submitted`; use the [separately authorized part-two observation](private-speaker-review-next-primary-observation.md). |
| `already_submitted` | Matching journals/state or the root receipt were revalidated; no second provider request was created. |
| `reconciliation_required` | The provider boundary may be ambiguous. Stop automation and retain every intent, journal, state file, and root record for explicit reconciliation. |

The operation never polls the new Batch, downloads output, parses results,
adjudicates, finalizes, promotes corpus data, or ingests it into PostgreSQL or
Qdrant. Those remain separately authorized transitions. Observation of the
submitted part is documented in the
[part-two observation runbook](private-speaker-review-next-primary-observation.md).
