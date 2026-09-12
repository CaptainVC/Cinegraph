# ADR-0029: Private first-adjudication submission boundary

## Status

Accepted for Phase 69.

## Context

ADR-0028 exposes provider-free primary-result processing on the Dev VPS. When
the Luna reviewers do not reach high-confidence consensus, that transition
creates one or more deterministic Terra adjudication request parts and stops at
`adjudication_prepared`. A general workflow `advance` call is too broad for the
next trust boundary: depending on state, it can retrieve provider output,
submit another paid part, interpret results, or enter later review stages.

Submitting the first adjudication part is paid external work. It must require a
new authorization, use only the exact already-reviewed request bytes, preserve
provider identifiers inside the private workspace, and make ambiguous provider
outcomes explicit instead of retrying them automatically.

## Decision

Add a narrow LangGraph operation, `submit-first-adjudication`, backed by a
workflow transition that accepts only the exact `adjudication_prepared`
checkpoint. It submits adjudication part one and may transition only to
`adjudication_submitted`. An exact already-submitted first part is an idempotent
replay; the operation cannot observe that Batch, submit part two, invoke final
review, promote a corpus, or ingest application stores.

Expose the transition as the sixth finite forced SSH command,
`speaker-review-submit-first-adjudication-v1`, on the dedicated
`cinegraph-review` identity. Its bounded canonical request contains only the
archive digest, run ID, fresh authorization UUID, approved micro-USD ceiling,
fixed operation, purpose, season, and protocol version. Its bounded aggregate
contains counts, costs, and status only—never provider Batch/file identifiers,
request text, prompts, rationales, credentials, or host paths.

The root coordinator validates the active immutable release, image and
configuration; the complete preparation and two-part primary
submission/observation chain; and the Phase 68 processing intent and receipt.
The processing receipt must describe the current `adjudication_prepared` state.
State, immutable artifacts, existing journals, provider outputs/API-error
evidence, and deterministic derived files are bound as five independent digest
sets. The derived set includes every prepared adjudication request part and may
not change during submission.

Before egress, root writes a create-once intent. The worker receives only the
digest-selected review-runs directory read-write and the OpenAI key as a
read-only Compose secret file. It recomputes the five inventory digests,
deterministically estimates adjudication cost, and enforces actual primary cost
plus estimated adjudication cost against both the fresh authorization ceiling
and configured run ceiling. The SHA-256 of part one's request bytes is passed
into the workflow and checked immediately before `_submit_part` calls the
provider.

The worker runs as UID/GID `10002`, with a read-only root filesystem,
no-new-privileges, all capabilities dropped, bounded resources, and only the
existing `egress` network. This network permits outbound access; it is not a
destination allowlist restricted to OpenAI. The root-owned release and Compose
definition plus the host Docker daemon are part of the trusted computing base;
this boundary does not claim a separate live-container inspection before
egress. A successful transition may change only run state and the create-once
adjudication part-one submission intent/completion journals. Provider
identifiers remain in those protected files and never enter the root receipt or
client response.

Root independently verifies the post-state, unchanged evidence groups, exact
new journals, active release/image/configuration binding, and aggregate before
publishing a create-once receipt. A completed workflow journal can repair a
missing root receipt only by passing the idempotent worker again without a
second provider call. A workflow intent without a matching completion journal
is an ambiguous external outcome: it returns `reconciliation_required` and is
never automatically resubmitted.

## Consequences

The first Terra adjudication Batch can be submitted through a narrowly scoped,
auditable and retry-safe production boundary. Authorization and cost policy are
fresh for this paid action, while the exact request bytes remain bound to the
provider call.

This phase does not observe adjudication output. Observation of part one,
submission and observation of later parts, final review, residual human review,
corpus promotion, and ingestion remain separate explicit transitions.

See the [first-adjudication submission runbook](../operations/private-speaker-review-first-adjudication-submission-boundary.md).
