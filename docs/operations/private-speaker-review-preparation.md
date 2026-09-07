# Private speaker-review preparation

Phase 60 provides an offline VPS boundary for validating and preparing the private
Modern Family Season 2 speaker-review corpus. It never contacts OpenAI and cannot
create a paid Batch job.

## Safety boundary

The `speaker-review-v1` forced command accepts only an installed archive SHA-256,
the fixed `speaker_review` purpose, Season 2, a protocol version, and one of
`validate`, `prepare`, or `status`. `status` also requires the deterministic run ID.
The caller cannot select a server path, model, prompt, command, season, or network
destination.

The isolated preparation container receives a read-only source mount and a separate
writable `review-runs` mount. It has `network_mode: none` and receives no OpenAI,
database, identity, Qdrant, or application environment secret. A rejecting provider
gateway is wired even though preparation should never call a gateway.

## Preconditions

The exact Season 2 `speaker_review` bundle must first be installed with the private
corpus transfer flow. The live Dev checkout, image digest, catalogue, corpus host
bootstrap, pinned corpus SSH identity, and pinned known-hosts entry must all be
current. After deploying code that changes this boundary, refresh and verify the
root-owned host files from the Hostinger console:

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

Use placeholders in documentation, issues, pull requests, and logs. Never publish
real hosts, private paths, keys, object digests, corpus filenames, or worker output
artifacts.

## Validate

Validation proves that the local bundle digest identifies the exact immutable object
already installed on the VPS. The bundle bytes are inspected locally but are not sent
again.

```powershell
uv run python scripts/review_private_speaker.py `
  --bundle <private-season2-speaker-review-bundle.zip> `
  --operation validate `
  --identity <corpus-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host-placeholder>
```

## Prepare

Preparation runs the deterministic candidate extraction, prompt construction, cost
estimate, and immutable run-artifact creation through LangGraph. It does not submit
the prepared request parts.

```powershell
uv run python scripts/review_private_speaker.py `
  --bundle <private-season2-speaker-review-bundle.zip> `
  --operation prepare `
  --identity <corpus-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host-placeholder>
```

Record the aggregate run ID in a private operator record. Do not copy the digest,
paths, filenames, or generated request artifacts into GitHub or shared logs. An exact
repeat returns `already_prepared` only after the stored receipt is revalidated.

## Status

Status revalidates the receipt, immutable source workspace, and prepared run artifacts;
it does not start the worker or contact a provider.

```powershell
uv run python scripts/review_private_speaker.py `
  --archive-sha256 <private-object-sha256> `
  --run-id speaker-review-<16-lowercase-hex> `
  --operation status `
  --identity <corpus-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host-placeholder>
```

## Failure and recovery

The operation is synchronous. A timeout, disconnect, container failure, unexpected
stderr, malformed or oversized output, source change, or receipt conflict returns a
generic rejection and does not create a success receipt. The source object is never
modified. Root-private staging residue after an abrupt host failure must be inspected
through the provider console and handled only after its exact physical path and owner
are verified; do not use a glob or recursively delete a computed root.

Paid submission, polling, adjudication, final review, human resolution, reviewed SRT
promotion, ingestion, and detached execution are outside this phase.
