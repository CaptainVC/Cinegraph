# Private speaker-review fourth-adjudication submission

Phase 75 exposes `speaker-review-submit-fourth-adjudication-v1`, a one-shot paid
submission of exactly Terra adjudication part four. It is intentionally a
different command, contract, receipt namespace, Compose service, and root
helper from the generic application transition. The application primitive can
submit the next part, but this VPS boundary accepts only the authenticated
Phase 74 checkpoint with exactly three completed parts.

This command does not observe the submitted batch, download provider output,
parse adjudication decisions, enter final review, promote reviewed subtitles,
or ingest PostgreSQL/Qdrant.

## Preconditions

- Dev is running an immutable image whose release commit equals `origin/main`.
- The review-host bootstrap check succeeds after installing the twelfth forced
  command. Refresh the reviewed host files after deploying this release.
- The complete preparation-through-Phase-74 root intent/receipt chain is
  present, root-owned, and unmodified.
- The selected run is `adjudication_part_completed`, has
  `adjudication_completed_part_count == 3`, and contains exactly three unique
  batch/input ID pairs. More than three total parts enables the paid part-four
  submission; exactly three total parts takes the authenticated provider-free
  `all_parts_completed` path.
- Completed request, journal, and output evidence for parts one through three is
  present. Part-four output and API-error artifacts must not exist.
- The operator independently selected the expected archive digest and run ID,
  generated a fresh UUIDv4, and accepts the maximum cost ceiling.
- The dedicated review SSH private key and pinned `known_hosts` file are
  available on the invoking machine. Do not use an unpinned host key.

## Refresh and verify the host boundary

After the Phase 75 release is active, run the reviewed bootstrap using the
existing pinned public-key files and fingerprints:

```bash
sudo python3 /opt/cinegraph/current/scripts/bootstrap_review_host.py \
  --public-key-file <review-public-key> \
  --expected-key-fingerprint <review-key-fingerprint> \
  --corpus-public-key-file <corpus-public-key> \
  --expected-corpus-key-fingerprint <corpus-key-fingerprint> \
  --expected-deploy-key-fingerprint <deploy-key-fingerprint> \
  --refresh-review-code

sudo python3 /opt/cinegraph/current/scripts/bootstrap_review_host.py \
  --public-key-file <review-public-key> \
  --expected-key-fingerprint <review-key-fingerprint> \
  --corpus-public-key-file <corpus-public-key> \
  --expected-corpus-key-fingerprint <corpus-key-fingerprint> \
  --expected-deploy-key-fingerprint <deploy-key-fingerprint> \
  --check
```

The check must confirm the dispatcher, helper, cumulative sudoers policy, and
root-owned receipt directory. Do not invoke the paid command if bootstrap
verification fails or the active checkout is dirty or behind `origin/main`.

The installed boundary must expose only the exact forced command. It must not
grant an interactive shell, arbitrary sudo arguments, Docker membership, or a
general review command.

## Authorize and invoke

Generate a new lowercase UUIDv4. Create this exact canonical JSON document as
`/opt/cinegraph/shared/private-corpus/dev/speaker-review/authorization/<uuid-v4>.json`
on the VPS, owned by `root:root`, mode `0600`, with a single trailing newline:

```json
{"archive_sha256":"<64 lowercase hex>","authorization_id":"<uuid-v4>","maximum_authorized_cost_microusd":5000000,"operation":"submit_fourth_adjudication","purpose":"speaker_review","run_id":"speaker-review-<16 lowercase hex>","schema_version":1,"season_number":2}
```

The authorization is bound to this operation, archive, run, and ceiling. Never
reuse an authorization UUID or copy an authorization from another phase.

Invoke through the pinned client, replacing only operator-owned values:

```zsh
uv run python scripts/private_speaker_review_fourth_adjudication_submission_client.py \
  --archive-sha256 <digest> \
  --run-id <run-id> \
  --authorization-id <uuid-v4> \
  --maximum-authorized-cost-microusd 5000000 \
  --identity <review-private-key> \
  --known-hosts <pinned-known-hosts> \
  --host <review-host>
```

The response is canonical aggregate JSON. It contains status, part counts, and
micro-USD totals only; it never exposes provider batch/file IDs, transcript or
prompt content, model output, secret values, or private filesystem paths.

## What the boundary validates

Before any secret read or provider call, root authenticates the canonical
request and authorization bytes, archive/run identity, the entire predecessor
receipt chain, active release/image/configuration, exact pre-state, all five
inventory digest classes, the part-four request digest, deterministic Terra
estimate, actual primary cost, and both configured and operator cost ceilings.

The unprivileged UID/GID `10002:10002` worker receives only the digest-selected
run directory as writable storage and the OpenAI key as a mode-`0400` Compose
secret. It has a read-only root filesystem, a bounded temporary filesystem,
dropped capabilities, no-new-privileges, resource limits, and only the egress
network. The immutable image, Compose definition, Docker daemon, and VPS root
remain part of the trusted computing base; the egress network is not a
destination allowlist.

The worker calls the generic LangGraph `submit-next-adjudication` transition,
but both the root and worker independently pin the starting completed count to
three and the target to part four. The only permitted durable changes are:

- create the part-four submission-intent journal;
- create the matching part-four submission-completed journal;
- append one unique batch/input ID pair; and
- move to `adjudication_submitted` while keeping completed count at three.

Root inventories the result twice, rejects any unrelated mutation, validates
the application journals and aggregate, verifies the active runtime again,
and then writes a create-once aggregate-only root receipt.

## Outcomes

- `submitted`: part four was submitted, or an exact completed application
  journal repaired state after a crash, and the root receipt was written.
- `already_submitted`: the exact submitted checkpoint and binding were replayed
  provider-free; a missing root receipt may have been reconstructed.
- `reconciliation_required`: evidence is incomplete or ambiguous, such as an
  intent without a trustworthy completed journal. No automatic paid retry is
  attempted and no success receipt is written.
- `all_parts_completed`: the authenticated run has exactly three total parts;
  no secret is read, no worker is launched, and the run is unchanged.

On success, verify `run_status == "adjudication_submitted"`, completed count is
still `3`, total count is greater than `3`, ID arrays contain exactly four
unique entries, the part-four journal pair exists, and part-four output does
not exist. Retain the authorization, root intent, and root receipt as audit
evidence.

## Failure and reconciliation

All public failures are deliberately generic. Preserve the authorization,
root intent/receipt directory, application journals, active release SHA, and
host logs before investigating. Do not delete or rewrite an intent, completed
journal, state file, or receipt to force a retry: a provider write may have
succeeded even when the client received no response.

Stop on any reconciliation, inventory, request-hash, runtime, authorization,
cost, ownership, mode, hard-link, or symlink rejection. Resolve ambiguity with
a separately reviewed recovery procedure before creating any later
authorization. Part four must be observed by a future distinct command and
authorization; this Phase 75 boundary is the required stop point.
