# ADR-0030: Private first-adjudication observation boundary

## Status

Accepted for Phase 70.

## Context

ADR-0029 permits one separately authorized provider write: submission of the
first prepared Terra adjudication request part. The resulting run is fixed at
`adjudication_submitted`, with one protected Batch identifier and its matching
input-file identifier. The general speaker-review `advance` operation is too
broad for the next trust boundary because it can observe more than one part,
interpret adjudication results, submit later paid work, enter final review, or
approach corpus promotion.

Observation is read-only at the provider, but it still crosses the private
corpus and credential boundary. A retry must not poll, submit, parse, adjudicate,
promote, or ingest. It must also distinguish a still-running Batch from a
durably downloaded first-part result and from conflicting local/provider
evidence.

## Decision

Add `observe-first-adjudication` as a narrow LangGraph operation backed by
`SpeakerReviewWorkflow.observe_first_adjudication`. It accepts only the exact
first-part `adjudication_submitted` checkpoint. One invocation performs one
provider status lookup. A non-terminal status is a no-op. A successful status
downloads only `adjudication-part-0001-output.jsonl` and the optional API-error
file, then advances to the explicit `adjudication_part_completed` checkpoint.
A terminal provider failure advances only to `failed`. Exact completed replay
validates the first-part evidence without provider access.

Expose this transition as the seventh finite command on the dedicated
`cinegraph-review` SSH identity:
`speaker-review-observe-first-adjudication-v1`. Its canonical request is bounded
to the archive digest, run ID, fresh authorization UUID, micro-USD ceiling,
fixed operation, purpose, season, and protocol version. Its response exposes
only counts, costs, run status, and operation status. Provider IDs, prompts,
request/output text, rationales, secrets, and filesystem paths remain private.

The root coordinator revalidates the full Phase 69 evidence chain, including
the prior authorization, intent, receipt, exact request digest, active immutable
release/image/configuration, and the earlier corpus-review receipts. The
deterministic adjudication estimate is taken from the validated Phase 69 receipt,
not invented from mutable run state. Root binds the run-state file plus
immutable artifacts, journals, provider outputs/errors, and derived artifacts
as five independent digest groups. A create-once root intent is durable before
the worker can reach the provider.

The dedicated Compose worker receives only the digest-selected `review-runs`
directory read-write and the OpenAI key as a mode-`0400` secret file. Before
reading that secret it revalidates all six root bindings: the five inventory
digests and the exact part-one request digest. It independently recomputes the
adjudication estimate from all prepared request parts and enforces actual
primary cost plus that estimate against both the fresh authorization and the
central run ceiling.

The worker runs as UID/GID `10002`, with a read-only root filesystem,
no-new-privileges, all capabilities dropped, bounded resources, and only the
existing `egress` network. That network permits outbound traffic and is not an
OpenAI-only destination allowlist. The root-owned immutable release, Compose
definition, and Docker daemon are therefore part of the trusted computing base;
this decision does not claim independent live-container inspection before the
status request.

After the worker exits, root allows only the exact first-part output/error files,
the terminal error artifact where applicable, and the narrow state transition.
It revalidates the active runtime and private source binding before publishing a
create-once completion receipt containing only aggregate data and post-state
digests. A waiting or reconciliation outcome retains the intent but publishes no
completion receipt. An exact terminal replay repairs a missing root receipt
without provider access.

## Consequences

Operators can safely check the first Terra adjudication part through a bounded,
auditable and idempotent interface. Repeated checks are explicit, separately
authorized invocations rather than an unbounded polling loop. Successful output
is durably frozen at a checkpoint that later provider-free processing can
consume.

This phase does not parse adjudication output, submit or observe later
adjudication parts, run final review, perform residual human review, promote a
corpus, or ingest PostgreSQL/Qdrant.

See the [first-adjudication observation runbook](../operations/private-speaker-review-first-adjudication-observation.md).
