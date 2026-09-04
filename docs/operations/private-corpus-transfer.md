# Private corpus bundle boundary

Phase 51 defines the deterministic bundle. Phase 52 adds a separate synchronous
operator-to-Dev transport and publishes a verified bundle into an immutable,
root-private host object. Phase 55 adds the separate synchronous processing boundary;
publication is not review, ingestion, or activation.

## Build and stage

The builder requires a knowledge root and every selected file. It never archives an
arbitrary directory. The two purposes are:

- `reviewed_ingestion`: catalogue-selected `*.reviewed.srt` files and the matching
  `review-ledger.json`.
- `speaker_review`: the configured root-level season script PDF and the exact
  catalogue-derived `*.script-aligned.srt` files beneath that season directory.

The public catalogue must be supplied from outside the private knowledge root. It is
validated and used to derive the exact season file list, but is never placed in the
private bundle. For example:

```powershell
uv run python scripts/build_private_corpus_bundle.py `
  --knowledge-root C:\private\knowledge `
  --output C:\private\staging\reviewed.zip `
  --purpose reviewed_ingestion `
  --catalogue "E:\AI Projects\Cinegraph\knowledge\catalogue.json" `
  --season 1

uv run python scripts/stage_private_corpus_bundle.py `
  --bundle C:\private\staging\reviewed.zip `
  --destination C:\private\incoming\reviewed
```

The destination parent must already be a physical, current-user-owned private
directory (`0700` on POSIX), and the destination itself must not exist.

The manifest is versioned and canonical. It records only POSIX relative member
paths, byte sizes, lowercase SHA-256 hashes, aggregate counts/bytes, purpose, and
the selected catalogue digest. ZIP timestamps and member order are fixed, so the
members use no compression, so the same source bytes produce the same archive
regardless of enumeration order or host platform. Success and failure output is
aggregate-only and does not print private paths or content.

The builder writes a private temporary archive, verifies it completely, and then
publishes it with a no-replace rename. Both the requested output and temporary path
must be outside Git or provably ignored and untracked; Git failures are fail-closed.

The verifier treats both ZIP metadata and the manifest as hostile. It validates the
complete member set, canonical JSON, path and name policy, limits, regular-file
metadata, sizes, and hashes before extraction. Staging requires a fresh nonexistent
destination, snapshots the received archive into a private temporary directory,
re-verifies content while extracting, and publishes with a no-replace rename.
Failures remove temporary state. On POSIX, staged directories are `0700` and files
are `0600`; Linux publication uses the kernel's atomic `RENAME_NOREPLACE` contract.

## Explicit prohibitions

Never transfer the whole `Cinegraph Data` directory or any arbitrary corpus root.
Do not use GitHub Actions, artifacts, or releases; GHCR images; deployment-key
reuse; direct Docker named-volume copying; or live-volume mutation as a corpus
transfer mechanism. Do not put private SRT/PDF files, review ledgers, keys, or
derived artifacts in Git, images, logs, or releases. A bundle is an audited handoff
artifact only; the Phase 52 procedure below is the only approved transport and
host-publication path.

## Dev host bootstrap

Generate a dedicated corpus-transfer Ed25519 identity outside the repository. Never
reuse the deployment identity and never put the corpus private key in GitHub. Through
the authenticated Hostinger console, place only its public line in a root-controlled
temporary file. From a clean root-owned checkout exactly matching live `main`, compare
the independently recorded corpus and deployment public fingerprints, then run:

```bash
python3 -B -m scripts.bootstrap_corpus_host \
  --public-key-file <root-owned-corpus-public-key-file> \
  --expected-key-fingerprint SHA256:<corpus-public-fingerprint> \
  --expected-deploy-key-fingerprint SHA256:<deployment-public-fingerprint>

python3 -B -m scripts.bootstrap_corpus_host \
  --public-key-file <root-owned-corpus-public-key-file> \
  --expected-key-fingerprint SHA256:<corpus-public-fingerprint> \
  --expected-deploy-key-fingerprint SHA256:<deployment-public-fingerprint> \
  --check
```

Existing differing files, accounts, groups, modes, ownership, symlinks, authorization,
sudoers, or key identities fail closed. After a reviewed helper change reaches live
`main`, `--refresh-corpus-code` may atomically refresh the corpus dispatcher, receive
and processing helpers, and exact no-argument sudoers contract; it also creates only
the newly reviewed root-private processing directories when upgrading the legacy
transfer-only boundary. Run `--check` immediately afterward. The corpus authorization,
existing objects, deployment bootstrap, and deployment key remain unchanged.

## Operator transfer

Create a known-hosts file containing exactly one independently pinned canonical
Ed25519 host-key line. On Windows, use the Python client rather than PowerShell or cmd
redirection so all ZIP bytes remain unchanged:

```powershell
uv run python scripts/transfer_private_corpus.py `
  --bundle C:\private\staging\reviewed.zip `
  --identity C:\private\ssh\cinegraph-corpus-dev `
  --known-hosts C:\private\ssh\known-hosts `
  --host dev.example.invalid
```

The example values are placeholders. Do not paste a real host, key, digest, private
path, or output into Git, a PR, or public logs. The client snapshots and verifies the
bundle, computes the bytes actually sent, and invokes OpenSSH without a shell. The
remote command is always the metadata-free literal `receive-v1`; the bounded header
and binary archive travel only on encrypted stdin.

Success is `installed` or `already_present` plus aggregate purpose/season/count/bytes.
An exact retry always resends and reverifies the archive and does not alter an existing
object. A disconnect, timeout, short/long stream, digest mismatch, catalogue mismatch,
low capacity, archive violation, race, or corrupt replay fails without replacement.

## Recovery and scope

The transfer and deployment locks are taken in that order. No detached receive job is
created: closing the workstation or losing SSH aborts the receive. Normal failure
removes only the transaction whose inode the receiver created. After host power loss,
inspect root-private transaction residue through the provider console and move a
strictly verified residue to the dedicated quarantine directory before deletion; do
not use globs or recursive deletion against a computed path.

Final objects are append-only and are not automatically pruned. Phase 55 processing
must name an exact object digest and reverify catalogue binding. It is limited to
reviewed-ingestion Season 1; Phase 56 hardens live worker output and Season 2 speaker
review is deferred to Phase 57. See
`docs/operations/private-corpus-processing.md` for the processing request, offline
Compose worker, deterministic workspace, and retry rules. Phase 52 does not
transfer real data by itself, configure Prod, expose staged objects to containers,
copy into a Docker volume, run speaker review or ingestion, call OpenAI, mutate
PostgreSQL/Qdrant, restart the app, change guest entitlements, or configure TLS/DNS.
Actual Hostinger bootstrap, forced-command probing, and a synthetic then real transfer
are explicit post-merge operator acceptance actions.
