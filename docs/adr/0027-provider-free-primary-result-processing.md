# ADR-0027: Provider-free primary-result processing checkpoint

## Status

Accepted for Phase 67.

## Context

Phase 66 can durably observe the final authorized Luna primary-review part, but
the next legacy workflow step is too broad: it parses output, prepares Terra
adjudication, and immediately crosses another paid provider boundary. Result
interpretation must be independently reviewable, retryable, and unable to use
the OpenAI credential or network.

The input is private and untrusted. A forged run state, missing part, changed
submission journal, malformed usage record, or filesystem alias could otherwise
produce an incomplete consensus or under-report cost. Processing can also create
private derived artifacts, so retries must prove that existing bytes are the
same deterministic result rather than overwrite them.

## Decision

Add the `process-primary-results` LangGraph operation and the explicit
`adjudication_prepared` run checkpoint. The transition accepts only a fully
observed `primary_part_completed` state, or an exact replay of a checkpoint it
created. It loads every primary output, parses the two configured Luna opinions,
computes consensus and actual token cost, and does one of two things:

- if every candidate meets the centralized consensus policy, it writes the
  reviewed outputs and completes the run locally; or
- if any candidate remains unresolved, it writes deterministic Terra request
  parts and stops at `adjudication_prepared` without submitting them.

The transition validates the full part count and provider-ID tuple shape,
configured schema/models/prompt, empty downstream state, candidate count,
finite non-negative costs, both the central run budget and the separately
authorized ceiling, exact replay counters, and the absence of unexpected
adjudication request parts. Provider usage is required on every output record;
missing, negative, boolean, per-record oversized, or aggregate oversized token
counts fail closed before derived files are written.

Derived JSON and JSONL files retain create-once semantics. A matching partial
set left before the state checkpoint can be completed deterministically; a
different byte or inconsistent checkpoint requires operator reconciliation.
This phase does not weaken the existing atomic run-state replacement rule.

Add an isolated `corpus-speaker-review-process-primary-results` service running
as UID/GID `10002`, with no network, no secret, a read-only container root,
dropped capabilities, no-new-privileges, and bounded CPU, memory, PIDs, and
temporary storage. Its worker requires root-computed SHA-256 bindings for five
separate classes: run state, immutable preparation/request artifacts,
submission journals, observed outputs plus API-error evidence, and derived
artifacts. Every file is reopened with no-follow, single-link, owner/mode,
size, total-inventory, and stable-inode checks. LangGraph consumes the verified
in-memory state, avoiding a second pathname load.

The worker's stdout is a strict small aggregate. It cannot contain provider
identifiers, source text, output payloads, filesystem paths, prompts, rationales,
or credentials.

## Consequences

Primary interpretation and Terra submission are now distinct security and cost
boundaries. Phase 68's root coordinator validates the predecessor
authorization/receipt chain, mounts the immutable source workspace read-only and
the exact digest-selected review-runs workspace read-write, passes all five
pre-state digests, serializes execution, and creates root-owned intent/receipt
evidence; see [ADR-0028](0028-private-primary-result-processing-boundary.md).

No Terra batch is submitted in this phase. Adjudication observation, final
review, human escalation, corpus promotion, and ingestion remain later explicit
transitions. Operators must not edit or delete private artifacts to force a
retry after reconciliation is required.

See the [transition runbook](../operations/private-speaker-review-primary-result-processing.md).
