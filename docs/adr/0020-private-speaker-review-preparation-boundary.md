# ADR-0020: Private speaker-review preparation boundary

- Status: accepted for implementation
- Date: 2026-09-07

## Context

Season 2 source scripts and aligned subtitles are private corpus inputs. They must
be reviewed before ingestion, but the serving API must never receive filesystem
access to them. Preparing review candidates is deterministic and offline; creating
OpenAI Batch jobs is paid, networked, and requires a secret. Combining those actions
would make validation or a retry capable of spending money without a separate human
decision.

The existing `process-v1` boundary is intentionally restricted to reviewed Season 1
ingestion. Widening it would mix a Qdrant-writing operation with a provider-facing
review workflow and weaken both contracts.

## Decision

Add a separate forced SSH command, `speaker-review-v1`, for the existing restricted
`cinegraph-corpus` identity. It accepts only canonical, bounded JSON over encrypted
standard input. Phase 60 permits three exact operations:

- `validate` revalidates the immutable object, install receipt, active catalogue,
  purpose, and Season 2 selection without creating a workspace.
- `prepare` copies the verified inputs into a deterministic private source workspace
  and invokes the existing LangGraph speaker-review workflow in prepare-only mode.
- `status` reads a root-owned preparation receipt for an exact object digest and
  deterministic run identifier.

All arbitrary paths and all paid or state-advancing verbs are rejected. Responses
are canonical aggregate JSON containing only counts, run state, and rounded estimated
cost. They never contain archive digests, paths, filenames, source text, request
bodies, provider identifiers, credentials, or exception messages.

The root helper takes locks in the invariant order transfer, deployment, then
speaker review. It verifies the clean active `main` release, immutable image binding
and OCI labels, and the installed object before running an image-pinned Compose
one-shot. The container uses dedicated UID/GID 10002, has no network, ports, named
volumes, provider secret, database configuration, Qdrant configuration, or access to
the application's knowledge volume. Its root filesystem is read-only, capabilities
are dropped, privilege escalation is disabled, temporary storage and process count
are bounded, and CPU and memory have explicit ceilings.

Source files are copied once to a root-controlled, group-readable directory and are
mounted read-only at `/private-corpus`. A distinct per-object directory owned by the
unprivileged worker is mounted at `/private-corpus/review-runs`. This lets the
existing path-confined workflow write immutable run artifacts without granting write
access to the source PDF, subtitles, transfer manifest, or install receipt. The
worker uses centralized model and review configuration plus a rejecting gateway;
any attempt to submit, retrieve, or download provider data fails locally. LangGraph
remains the routing adapter while the filesystem state and receipt are the durable
authority.

After successful preparation, the root processor revalidates the source workspace
and the completed run, then atomically writes a receipt binding the object,
catalogue, release/image provenance, review configuration fingerprint, run ID, and
public aggregate. It also binds the exact prepared artifact inventory and a digest
over every artifact name and byte, so a changed candidate, request part, source
manifest, run state, or unexpected file invalidates replay and status. An exact retry
returns `already_prepared` without rerunning the worker. Conflicting or malformed
state fails closed and is never overwritten.

## Consequences

Phase 60 cannot incur OpenAI cost and cannot mutate Qdrant, PostgreSQL, the serving
API, or the immutable source object. It also does not submit, poll, reconcile,
adjudicate, finalize, apply human resolutions, promote reviewed subtitles, or run as
a detached/background job. Those capabilities require later, separately reviewed
boundaries.

The operation is synchronous. A workstation disconnect can interrupt preparation;
no success receipt is written until the worker output and persisted artifacts pass
verification. Root-private crash residue is retained for explicit inspection rather
than removed through an ambiguous computed path.
