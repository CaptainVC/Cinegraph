# ADR-0018: Isolated private-corpus VPS handoff

- Status: accepted for implementation
- Date: 2026-09-03

## Context

Phase 51 creates deterministic, hostile-input-verified, season-scoped private corpus
bundles but deliberately provides no transport. Private SRT, PDF, ledger, and derived
bytes must reach Dev without entering GitHub, images, deployment credentials, command
logs, or the live application volume. Interrupted transfers must be safely retryable.

## Decision

Use a second `cinegraph-corpus` Linux identity and a dedicated Ed25519 key whose
private half remains only on the operator workstation. Its root-owned authorization
forces one separate dispatcher. The dispatcher accepts exactly the static command
`receive-v1` and streams stdin to one exact no-argument sudo helper. The existing
`cinegraph-deploy` identity, key, dispatcher, helper, and sudoers contract are not
modified or reused.

The binary protocol carries a bounded canonical JSON header and exact ZIP bytes over
pinned SSH stdin. Size and digest are not command arguments. The root receiver writes
an exclusive private spool, enforces disk/inode reserves, verifies size/digest/EOF,
applies the complete Phase 51 archive contract, and independently binds the manifest
and exact member selection to the locked active public catalogue. Schema v1 is
accepted only for the canonical Modern Family series and guest Seasons 1 and 2.

Installation is append-only and content-addressed. A transaction is completely
extracted, reverified, receipted, fsynced, and atomically renamed without replacement
under the root-only Dev private-corpus object store. Exact replays receive and verify
all bytes again, then verify the complete existing tree without mutation. Different
objects coexist for recovery. Transfer never updates an active pointer or invokes
Compose, Docker, OpenAI, PostgreSQL, Qdrant, review, or ingestion.

An operator runs a separate Hostinger-console bootstrap/check command from a clean,
root-controlled checkout at exact live `main`. It accepts only the corpus public key
and independently recorded corpus/deployment public fingerprints, refuses identical
identities, creates only the bounded account/files/directories, and permits helper
replacement only through explicit reviewed refresh mode.

## Relationship to Phase 55

Phase 55 consumes an exact installed object through the separate synchronous
`process-v1` boundary. The local processing client sends a canonical request—not the
bundle bytes again—over encrypted stdin; the root processing boundary revalidates the
object and catalogue binding, uses a
deterministic read-only workspace, and runs only the unprivileged offline reviewed-
ingestion Compose service. It does not mutate the source object. Validation performs
no Qdrant mutation, while exact ingestion retries are safe because Qdrant IDs are
deterministic and receipts are digest-keyed. Processing takes locks after transfer and
deployment in that order, and workstation disconnect can abort the synchronous run.
Only reviewed-ingestion Season 1 is in scope; Season 2 speaker review is deferred to
Phase 57. The safe code refresh is `--refresh-corpus-code` followed immediately by
`--check`.

## Consequences

The serving API cannot read staged objects. Phase 55 defines the explicit,
unprivileged, exact-digest reviewed-ingestion one-shot and revalidates the then-active
catalogue. Supporting any additional series requires a new bundle schema containing
an immutable series identity; those corpora must remain authenticated-only.

The synchronous transfer does not survive operator disconnect. Normal interruption
cleans its inode-owned transaction; ambiguous crash residue stays root-private for
explicit quarantine/recovery. Final objects are never automatically removed.
