# Private corpus processing

Phase 55 processes one already-installed Phase 52 private object. It does not move
the object, expose it to the API, or modify the source object. The approved boundary
is the synchronous `process-v1` request over encrypted SSH stdin.

## Preconditions

Use a clean, root-controlled release at the exact live `main` tip. The corpus host
bootstrap must already have passed `--check`. After a reviewed processing-code update,
run the safe upgrade and verification sequence from the Hostinger console:

```bash
python3 -B -m scripts.bootstrap_corpus_host \
  --public-key-file <root-owned-corpus-public-key-file> \
  --expected-key-fingerprint SHA256:<corpus-public-fingerprint> \
  --expected-deploy-key-fingerprint SHA256:<deployment-public-fingerprint> \
  --refresh-corpus-code

python3 -B -m scripts.bootstrap_corpus_host \
  --public-key-file <root-owned-corpus-public-key-file> \
  --expected-key-fingerprint SHA256:<corpus-public-fingerprint> \
  --expected-deploy-key-fingerprint SHA256:<deployment-public-fingerprint> \
  --check
```

Use placeholders for hostnames, keys, digests, and private paths in runbooks and
examples. Never put their real values in Git, pull requests, or public logs.

## Validate the exact object

Validation is a no-mutation gate. It verifies the object, manifest, complete member
set, active catalogue binding, and the exact reviewed-ingestion Season 1 selection.
It does not invoke Qdrant writes.

```powershell
uv run python scripts/process_private_corpus.py `
  --bundle <private-reviewed-season1-bundle.zip> `
  --operation validate `
  --identity <corpus-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host-placeholder>
```

The client snapshots and verifies the local bundle, then sends only the bounded
canonical request. The bundle bytes are not sent again; their digest identifies the
exact immutable object already installed by the transfer boundary. A successful
response is aggregate-only and has status `validated`.

## Ingest reviewed Season 1

Run only after validation succeeds and the exact object is identified by its digest:

```powershell
uv run python scripts/process_private_corpus.py `
  --bundle <private-reviewed-season1-bundle.zip> `
  --operation ingest-reviewed `
  --identity <corpus-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host-placeholder>
```

The root-owned shell helper holds all three locks and invokes the isolated
`scripts/run_private_corpus_processing.py` processor. The processor rechecks the
active release and object, copies verified members into a deterministic digest-named,
UID/GID-10001 workspace, and mounts that workspace read-only into the
`corpus-reviewed-ingestion` Compose one-shot. The service runs offline, as an
unprivileged user, with no OpenAI, identity, or PostgreSQL credential and no provider
egress. Compose progress output is disabled for this machine-readable boundary. The
worker suppresses only the exact Qdrant internal-HTTP API-key and Hugging Face progress
configuration warnings expected from this service; any other worker stderr remains a
failure. Its Qdrant connection (including its API key when configured) is the narrowly
supplied service configuration; the worker's result is aggregate-only.

Before either operation, the processor requires `CINEGRAPH_RELEASE_SHA` in the
root-private Dev environment to equal the active checkout and verifies that the exact
local image digest carries the matching OCI source, revision, and version labels.
This prevents a clean checkout from invoking worker code or a catalogue from another
release.

The helper records a digest-keyed receipt after a successful run. Deterministic
Qdrant IDs make retries safe: an exact retry returns `already_applied` and does not
create a second logical set of points. A successful revised episode upsert removes
older Qdrant source versions only after the new points are acknowledged, scoped to the
same episode and language. A changed object, catalogue, release, worker
result, or receipt is rejected.

## Locks, interruption, and recovery

The lock order is invariant: transfer, then deployment, then processing. Do not run
processing concurrently with transfer or deployment. Processing is synchronous and
does not survive an operator disconnect; closing the workstation or losing SSH can
abort it. Normal failure removes only temporary staging owned by that attempt. A
fully materialized verified workspace may remain after a worker failure and is
reverified before an exact retry. After host power loss, inspect root-private residue
through the provider console and quarantine only a strictly verified exact residue.
Do not use globs or recursive deletion against a computed path.

Final objects, workspaces, and receipts are append-only and are not automatically
pruned. Keep operational output to the aggregate response; do not print private paths,
member names, content, keys, digests, or credentials.

## Scope

Phase 55 covers reviewed-ingestion Season 1 only. Phase 56 hardens the worker's
machine-readable runtime after live acceptance; Season 2 speaker review and its
processing path are deferred to Phase 57. Processing does not transfer bundles,
change guest entitlements, restart the API, run migrations, or alter the source object.
