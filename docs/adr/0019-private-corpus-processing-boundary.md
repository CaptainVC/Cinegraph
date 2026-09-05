# ADR-0019: Private-corpus processing boundary

- Status: accepted for implementation
- Date: 2026-09-04

## Context

The Phase 52 transfer publishes an immutable, root-private object, but publication
must not make private files readable by the serving API or by an operator's shell.
Processing also needs a bounded retry model: a workstation may disconnect while a
long-running operation is in progress, and an exact retry must not create duplicate
or divergent writes.

## Decision

Expose one separate forced-command operation, `process-v1`, for the dedicated
`cinegraph-corpus` identity. The local `scripts/process_private_corpus.py` entry point
wraps `process_private_corpus_client.py`; it snapshots the selected bundle, builds one
canonical encrypted-stdin request, and invokes the remote command without a shell.
The bundle bytes are not retransmitted: the request contains only the snapshot's
archive SHA-256, operation, purpose, schema version, and season number. The only
accepted purpose is `reviewed_ingestion`, the only accepted season is Season 1, and
the operations are `validate` and `ingest-reviewed`.

The root-owned no-argument shell helper takes the transfer, deployment, and processing
locks in that order, then invokes `scripts/run_private_corpus_processing.py` under an
isolated Python environment. The processor verifies the active release,
requires the private Dev environment's release SHA and exact image reference to match
it, and verifies the local image's OCI source/revision/version labels. It then
revalidates the exact object and its catalogue binding, and for ingestion materializes
a deterministic digest-named workspace owned by UID/GID 10001. The worker receives
that workspace as a read-only mount in the locked, unprivileged offline Compose
service `corpus-reviewed-ingestion`. The service has no OpenAI, identity, or
PostgreSQL credential and no provider egress; only its narrowly scoped Qdrant
connection settings are supplied. Its aggregate-only result is validated by the root
processor.

`validate` performs all object and catalogue checks without Qdrant mutation.
`ingest-reviewed` is the sole Phase 55 path that may index reviewed Season 1 content.
Its receipt is keyed by the exact archive digest and its deterministic Qdrant IDs make
an interrupted or repeated attempt safe to retry. Each successful episode upsert is
followed by a scoped deletion of older source versions for that episode and language,
so a new corpus revision converges without leaving stale active points. A completed
retry returns `already_applied`; public status contains only mode, purpose, season,
counts, bytes, and status. Private paths, member names, content, credentials, and
provider output are not returned.

Processing is synchronous. Disconnecting the workstation or losing SSH can abort the
operation; there is no detached receive or background processing job. Temporary
staging state is cleaned on normal failure. A completely materialized, verified
workspace may remain after a worker failure and is reverified before reuse; crash
residue remains root-private for explicit recovery. Final workspaces and receipts are
not automatically pruned.

## Consequences

The boundary is intentionally narrow and Season 1-specific. Season 2 speaker review
is deferred to Phase 58; no Phase 55 command accepts it. Phase 56 is limited to
hardening the accepted worker output contract. Processing does not transfer
or replace objects, alter the source bundle, restart the application, or run database
migrations. The deployment's normal runtime remains unable to read the private object
store.

The operator must use the reviewed exact object digest and a pinned known-hosts file.

The restricted ingestion worker uses a centrally controlled FastEmbed profile (batch
size 8 and one inference thread) and a 1536 MB default container envelope. This was
added after the 768 MB envelope produced an exit-137 OOM with no process output;
the corrected profile completed with its aggregate result. An exit-137 failure is
not accepted as a successful receipt and should be retried only after inspecting
the worker/container resource event. Worker stderr remains fail-closed.
After a safe code upgrade, the Hostinger-console bootstrap supports
`--refresh-corpus-code`, followed by `--check`; those commands must pass before a
processing request is attempted.
