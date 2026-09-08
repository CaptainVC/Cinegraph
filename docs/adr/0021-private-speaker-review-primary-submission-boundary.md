# ADR-0021: Private speaker-review primary submission boundary

- Status: accepted for implementation
- Date: 2026-09-07

## Context

Phase 60 prepares a deterministic Season 2 speaker-review run without provider
access. Creating the first paid Batch is a separate action: it needs an
explicit human authorization, a provider credential, and durable evidence that
the prepared run and cost ceiling are the ones that were approved. A
workstation retry must not turn a disconnect or a host crash into a second paid
submission.

The existing workflow already keeps a per-part intent/completed journal. The
host boundary also needs an immutable root authorization and a receipt that
binds the preparation receipt, artifact inventory, exact run/cost/auth request,
post-submit inventory, journals, and run state.

## Decision

Add a root-only `speaker-review-submit-primary-v1` coordinator,
`scripts/run_private_speaker_review_submission.py`. It is standard-library
only and is executed with `python3 -I -S -B`; it receives one canonical bounded
JSON request over standard input and emits only the public submission aggregate
or a generic rejection.

The root-controlled authorization is an exact copy of the submission request at
`authorization/<authorization-id>.json`. It is owned by root, mode `0600`,
canonical, create-once evidence. The coordinator requires the request,
authorization, and Phase 60 preparation receipt to agree on archive digest, run,
purpose, season, operation, and maximum cost. The preparation result's exact
micro-USD estimate is checked against that ceiling. The prepared artifact
inventory and per-file hashes are recorded in a create-once root submission
intent before any worker is started.

The dedicated Compose worker receives only the prepared `review-runs`
directory as a read-write bind mount. It receives the four exact non-secret
request environment values as explicit Compose arguments. The provider key is
available only to the Compose service's root-controlled secret file; it is not
present in the coordinator environment, command arguments, request JSON,
worker output, or receipts. The source corpus and preparation receipt are not
mounted into the submit worker.

After the worker exits, the coordinator requires the exact base artifact set,
the primary-part intent and completed journals, a `primary_submitted` run state,
and matching hashes before writing the final root receipt. Receipts and
journals are create-once; the coordinator never deletes run evidence or
overwrites a differing record.

Replay rules are deliberately asymmetric:

- A matching final receipt is revalidated and returned without starting a
  worker.
- Matching completed workflow journals may start the worker only to repair the
  run-state/receipt gap; the workflow must reuse the completed journal and may
  not call the provider again.
- An unresolved workflow intent, an orphan completed journal, or an ambiguous
  provider result returns `reconciliation_required` and never retries the
  provider automatically.
- A root intent with the same authorization and no workflow intent may safely
  retry the missing worker start.
- A different authorization, altered preparation binding, malformed receipt,
  changed artifact, or changed journal rejects without mutation.

## Consequences

The paid transition has an explicit root authorization and two durable host
evidence records (intent and completion). A crash can leave evidence behind,
but cannot silently authorize a different run or make an automatic duplicate
provider call. Reconciliation after an unresolved provider boundary remains an
operator action; this phase does not invent a provider lookup or expose provider
identifiers across the workstation contract.

The dedicated submit identity, forced command, Compose service, and secret
mount remain separate from the offline preparation boundary. The operational
sequence and crash handling are documented in the
[primary-submission runbook](../operations/private-speaker-review-primary-submission.md).
