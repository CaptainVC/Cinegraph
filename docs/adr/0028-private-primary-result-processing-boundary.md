# ADR-0028: Private primary-result processing VPS boundary

## Status

Accepted for Phase 68.

## Context

ADR-0027 defines a provider-free LangGraph transition that interprets a fully
observed Luna primary-review result set. Running its container directly would
still leave the caller responsible for choosing private mounts, supplying
pre-state digests, serializing against deployment and corpus transfer, and
proving which authorization and predecessor receipts were used. Those are host
trust-boundary responsibilities and cannot be delegated to the worker that
writes the private run.

The transition may either prepare deterministic Terra adjudication requests or,
when every candidate has high-confidence consensus, finalize reviewed subtitle
artifacts locally. It therefore needs read-only access to the exact materialized
source snapshot as well as write access to only the corresponding review-runs
directory. It must not receive provider credentials or network access.

## Decision

Expose one additional forced SSH command,
`speaker-review-process-primary-results-v1`, through the existing dedicated
`cinegraph-review` identity. The command accepts one canonical, bounded request
containing only the archive digest, run ID, fresh authorization UUID, approved
cost ceiling, fixed operation/purpose/season, and protocol version. The SSH
client pins the host key, disables forwarding, TTY, password fallback, and
ambient configuration, and accepts only the strict aggregate defined by the
Phase 67 wire contract.

A root-only coordinator validates the separate root-owned authorization and the
complete preparation, part-one submission/observation, part-two submission, and
part-two observation receipt chain. It binds the active clean release, immutable
image, reviewed configuration, fully observed run state, and five independent
SHA-256 sets: state, immutable request/preparation artifacts, submission
journals, provider outputs plus API-error evidence, and pre-existing derived
artifacts.

Before launching the worker, the coordinator writes a create-once root-owned
intent. It mounts only the digest-selected materialized source workspace
read-only at `/review-workspace` and the corresponding review-runs directory
read-write at `/review-workspace/review-runs`. The Compose service remains UID
and GID `10002`, read-only-root, capability-free, no-new-privileges, bounded,
secretless, and `network_mode: none`. The worker receives the five expected
digests and the approved cost ceiling as non-secret environment values.

The coordinator independently validates the post-state and complete inventory,
revalidates the immutable source, predecessor evidence, release/image/config
binding, and then writes a create-once completion receipt. Exact replay or
receipt repair is permitted only when the intent, terminal checkpoint, derived
bytes, and every binding revalidate. A terminal checkpoint without its receipt
must also pass the same provider-free application worker again in idempotent
validation mode; file shape alone is never enough to create a root attestation.
Orphaned, conflicting, partial-terminal, unexpected, or changed evidence fails
closed and requires reconciliation.

The root helper acquires locks in the common transfer, deployment, speaker-review
order. It verifies the root-owned installed boundary, clean release equal to
`origin/main`, tracked helper/coordinator contracts, exact one-off container
identity, mount directions, lack of network and secret, and bounded runtime.
Bootstrap installs the helper before expanding the finite sudo policy and
replaces the dispatcher last.

## Consequences

Primary interpretation can now run on the Dev VPS through an auditable,
idempotent boundary without exposing OpenAI credentials or granting a general
shell. A successful aggregate contains counts and statuses only; it cannot carry
source text, model output, provider identifiers, rationales, host paths, prompts,
or credentials.

This boundary spends no provider money and never submits Terra work. The next
phase must add a separately authorized, egress-only transition for one prepared
Terra adjudication part. Observation of that part, additional adjudication
parts, final review, human escalation, corpus promotion, and ingestion remain
separate future decisions.

See the [VPS processing runbook](../operations/private-speaker-review-primary-result-processing-boundary.md).
