# Private corpus bundle boundary

Phase 51 defines a deterministic, fail-closed bundle contract for moving a small,
explicitly selected private corpus set between environments. It does not transport,
install, or connect to a server. Phase 52 owns transport and installation.

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
artifact only; Phase 52 will define the approved transport and install procedure.
