# ADR-0026: Private speaker-review next-primary observation boundary

## Status

Accepted for Phase 66.

## Context

Phase 65 can submit exactly primary part two, but it deliberately cannot poll or
download provider output. The original Phase 63 observer is an independently
authorized, part-one-only operation. Reusing that public command or letting a
client supply a part number would widen a narrowly reviewed provider boundary.

The observation container and root coordinator also share the review-run files
with the same container UID. Revalidating a path and then asking LangGraph to
load it again creates a time-of-check/time-of-use window: the pathname could
change after the root-selected checkpoint was verified but before the provider
adapter consumes its state.

## Decision

Add one fourth forced SSH command:
`speaker-review-observe-next-primary-v1`. It always targets primary part two;
the wire request contains no part selector, provider identifier, model override,
filesystem path, or private corpus content.

Before invoking the egress worker, the root coordinator validates and binds:

- the fresh observation authorization and approved micro-USD ceiling;
- the Phase 60 preparation, Phase 61 first submission, Phase 63 part-one
  observation, and Phase 65 part-two submission intent/receipt chain;
- the digest-selected review run, exact part-two request, completed submission
  journals, and immutable run-state fields;
- the active clean `origin/main` release, immutable image reference and OCI
  labels, and reviewed configuration digest; and
- the complete pre-observation artifact, journal, evidence, and state digests.

The root passes only the fixed target part and those expected digests into the
existing observation Compose service. The worker reopens every allowlisted file
with no-follow, single-link, owner/mode, size, and stable-inode checks before it
opens the OpenAI secret. It then calls the LangGraph `observe-primary` transition
with the already verified in-memory `SpeakerReviewRunState`, so the graph does
not reload the state path. The provider adapter therefore receives the batch
identity from the same verified snapshot.

The transition may report `waiting`, `observed`, `failed`, or
`reconciliation_required`. Waiting and reconciliation results do not create a
completion receipt. Terminal results create a root-owned, fsync-backed,
part-specific receipt (`.part-0002`) containing pre/post evidence digests but no
provider IDs or private content. Exact replay revalidates the whole predecessor
chain and returns `already_observed` without a provider call. A terminal
filesystem transition with a missing receipt may repair only that receipt after
the immutable bindings have been revalidated.

The worker retains the observation service's least-privilege runtime: UID/GID
`10002`, read-only root filesystem, all Linux capabilities dropped,
`no-new-privileges`, PID limit 128, only the Dev egress network, one exact
writable digest-selected `review-runs` mount, one read-only secret mount, and an
ephemeral `/tmp`. Root cleanup removes a container only when its complete
Compose identity matches this contract.

## Consequences

Observation of each primary part now requires a distinct root authorization and
auditable command. Future primary parts cannot be reached through this v1
command. Submission, adjudication, final review, parsing, corpus promotion, and
ingestion remain unavailable.

The stronger checkpoint handoff also hardens the original part-one observer:
root supplies its expected target and digests, the worker verifies them before
secret access, and LangGraph consumes the verified in-memory state.

Operator automation must treat `reconciliation_required` as a stop condition.
It must never delete or edit intent, receipt, journal, or state evidence to force
a retry.

See the [operator runbook](../operations/private-speaker-review-next-primary-observation.md).
