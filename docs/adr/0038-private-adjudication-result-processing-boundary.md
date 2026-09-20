# ADR-0038: Root boundary for adjudication-result processing

## Status

Accepted

## Decision

Expose Phase 77 only through the pinned-SSH command
`speaker-review-process-adjudication-results-v1`. The forced dispatcher accepts
no arguments and maps that command to one root-owned helper. The helper checks
the immutable release, acquires transfer/deployment/review locks in order, binds
the archive digest to its root-owned source object and matching writable
`review-runs` directory, and launches the isolated no-network Compose service.

The coordinator validates the canonical request against the root authorization
and then creates an immutable, authorization-ID-scoped claim. The claim binds
the operation, archive, run, cost ceiling, request digest, and authorization
digest. An exact retry may reuse it; a different request cannot. The immutable
intent additionally binds the active release/image/configuration tuple, source
manifest and preparation receipt, both Phase 76 fourth-observation records,
per-file hashes, directory identities, and the six pre-transition inventory
digests: state, artifacts, requests, submission journals, provider outputs, and
derived files.

The worker receives only non-secret request values and those six digests. The
source snapshot is read-only and the only writable host mount is the exact
archive-bound run directory at
`/review-workspace/review-runs/<run-id>`. The container has no network,
provider secret, proxy environment, privileges, or added capability. Its root
filesystem is read-only and its memory, CPU, process count, output, and runtime
are bounded.

Root independently reloads canonical state, recomputes cost in integer
micro-USD with conservative rounding, checks the configured and authorized
ceilings, and derives the expected aggregate. Existing artifacts, requests,
journals, outputs, and derived bytes cannot change. Only deterministic
final-review request parts and completion artifacts may be added, as constrained
by the validated application state machine. The boundary accepts only
`final_review_prepared`, `completed`, or the exact replay result
`already_processed`.

Intent and final receipt publication use create-once files, hard-link
publication, file and directory fsync, and exact recovery of only the known
published-plus-pending inode shape. A terminal filesystem checkpoint without a
receipt is re-run through the provider-free worker in idempotent mode before a
receipt is created. Standalone pending files, orphan receipts, mutated evidence,
and conflicting claims fail closed. Receipts contain no subtitle, transcript,
provider payload, credential, or filesystem path.

The root helper validates the clean `origin/main` release and every tracked
file before Python imports it. Timeout cleanup first authenticates the exact
Compose project, service, image, command, user, security options, resource
limits, empty network set, environment, and three mounts. A container whose
identity differs is preserved for investigation rather than force-removed.

## Consequences

The VPS boundary exposes neither Docker, arbitrary commands, private evidence,
nor provider credentials to the review SSH identity. This phase is strictly
provider-free: it does not submit final review, perform human review, promote a
corpus, or ingest data. Those remain separate authorized transitions.
