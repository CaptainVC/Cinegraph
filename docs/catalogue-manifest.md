# Catalogue manifest contract

The catalogue manifest is the canonical identity map between private corpus files and
Cinegraph's series, season, and episode graph. Keep the real manifest below the
gitignored `knowledge/` directory, for example `knowledge/catalogue.json`.

The current `schema_version` is `1`. Every series, season, and episode UUID is an
immutable product identifier: do not regenerate an ID after any transcript has been
indexed. Renaming a title is safe; reusing an ID for different content is not.

The loader rejects unknown fields, unsupported schema versions, malformed UUIDs,
non-positive positions or runtimes, untrimmed required names, and duplicate IDs.
It sorts series, seasons, and episodes into canonical order and emits a SHA-256 digest
of that normalized representation. Formatting and input order therefore do not alter
the digest.

Use [`catalogue.manifest.example.json`](examples/catalogue.manifest.example.json) as a
shape reference. Synopsis and runtime are optional, but each episode title is required.
Set `reviewed_subtitle_filename` for any episode that can be included by a review
ledger; this explicit mapping prevents ingestion from guessing episode identity from
release-specific filenames.
Real SRT, script PDF, manifest, and derived index artifacts remain outside Git.
