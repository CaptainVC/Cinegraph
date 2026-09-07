# Speaker-review filesystem security

The speaker-review workflow processes private screenplay PDFs and SRT files outside
Git. This runbook defines which paths and bytes it may trust, how resumable artifacts
are protected, and which host assumptions operators must preserve.

## Security invariants

The corpus root must be an existing physical directory. The workflow rejects a root
or ancestor reached through a symbolic link, junction, or other reparse point. A run
has exactly this layout:

```text
<corpus-root>/review-runs/speaker-review-<16 lowercase hex>/
```

Run IDs are deterministic fingerprints of the candidate set and review policy. A
CLI, human-review operation, or LangGraph resume must validate both that layout and
the `run_id` in `run-state.json`; an arbitrary directory is not a valid run.

All source locators are canonical root-relative POSIX paths. Empty segments,
backslashes, drive prefixes, absolute paths, dot segments, traversal, trailing dot or
space, control characters, and configured length-limit violations fail closed.
Directories must remain physical. Source leaves must be regular files with one hard
link and no symlink/reparse attribute.

Bounded reads compare the file identity, size, modification timestamp, and link count
before opening, on the open descriptor, and after reading. A size violation or change
during the read invalidates the operation. This protects the bytes used for candidate
generation, source promotion, run-state loading, and provider requests.

## Source manifest and compatibility

New `source-manifest.json` files contain a filesystem schema version and, for each
source basename, a root-relative locator, exact byte length, and SHA-256 digest.
Load and promotion re-read the source through the confinement boundary and require
both recorded values to match. Source names are unique under case-insensitive
comparison so a run cannot behave differently across Windows and Linux.

Older manifests containing absolute paths may be resumed only when every resolved
file is physically beneath the corpus root derived from the canonical run directory.
The same regular-file, link, bound, and stable-read checks still apply. An old run
outside the canonical layout must not be moved or accepted by weakening validation;
prepare a new run from the private corpus instead.

## Private artifacts and provider handoff

Run directories are mode `0700` and artifacts are mode `0600` on POSIX. Immutable
artifacts use exclusive create and permit only byte-identical retries. Mutable state
uses a private temporary file, flush/fsync, atomic replacement, and parent-directory
sync. Existing symlink, reparse, hardlink, directory, or other non-regular leaves are
rejected.

Before a paid Batch submission, the workflow makes one bounded stable read of the
request JSONL. The same byte object is hashed into the durable intent journal and
passed to the OpenAI adapter with a validated basename. The adapter uploads those
bytes directly and disables SDK retries for both creation calls. It never receives or
reopens the local path.

Python's portable file-mode API cannot create an owner-only NTFS ACL. Windows runs
therefore inherit the directory's existing ACL and are supported only for automated
tests or non-sensitive development unless an administrator has independently granted
exclusive access to the executing identity and verified inheritance. Production
private-corpus review runs are supported on the dedicated Linux VPS worker, where the
mode checks above are enforced. Do not treat a successful Windows test run as proof
of private ACL configuration.

CLI summaries and normal status output expose the run ID and aggregate state, not an
absolute corpus path, source text, API key, request body, or provider payload. Keep
the run directory, journals, hashes, provider identifiers, and generated workbench
outside Git and public logs.

## Supported threat model

These controls defend against malformed user/config input, path escape, unsafe legacy
manifests, linked inputs/artifacts, ordinary file replacement during a read, accidental
overwrite, and request mutation between journalling and SDK upload.

They assume the workflow has an exclusive service identity and that another process
with that same OS identity is not concurrently replacing already-validated parent
directories. Portable Python cannot provide descriptor-relative `openat`/Windows
handle traversal uniformly for that stronger attacker. Root/Administrator compromise,
kernel or filesystem compromise, and offline disk modification are also out of scope.

On a VPS, dedicate an unprivileged worker account to review processing, keep the
corpus and run tree inaccessible to the web application account, do not share write
access with interactive users, and serialize review operations for a run. The OpenAI
key belongs in a private runtime environment file, never in the corpus, bundle,
container image, repository, command line, or process log.

## Operator response

If validation fails, do not edit a manifest, replace a hash, delete a submission
intent, follow a linked path, or rerun against a copied arbitrary directory. Preserve
the private evidence and determine whether the source, run artifact, permissions, or
layout changed. Prepare a fresh deterministic run only from a verified corpus root.

An intent without a completed submission journal is a provider-reconciliation event,
not a filesystem-repair event. Follow the
[submission recovery runbook](speaker-review-submission-recovery.md) and do not create
a second paid request automatically.
