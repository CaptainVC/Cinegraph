# Private speaker-review fourth-adjudication observation

Phase 76 observes only Terra adjudication part four after the Phase 75
submission. It is a read-only provider operation and does not parse output,
submit part five, enter final review, promote corpus files, or ingest data.

## Preconditions

The review host must already be bootstrapped and the release must contain the
Phase 76 helper and Compose service. Obtain a new authorization for the exact
request fields and use the Phase 75 run ID and archive digest. The run must be
in `adjudication_submitted` with `adjudication_completed_part_count == 3`,
`adjudication_part_count > 3`, and exactly four unique batch and input IDs. Do
not copy private provider payloads or secrets into the release.

## Refresh and verify the host boundary

After the Phase 76 release is active, install the thirteenth reviewed forced
command using the existing pinned public-key files and fingerprints, then run
the non-mutating check:

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

The check must confirm the cumulative sudoers policy, forced dispatcher,
root-owned helper, and receipt directory. Stop if the active release is dirty,
behind `origin/main`, or the check fails. The review account must not receive
an interactive shell, Docker-group membership, arbitrary sudo arguments, or a
generic review command.

## Authorize and invoke

Generate a fresh lowercase UUIDv4. On the VPS, create this exact canonical JSON
as `/opt/cinegraph/shared/private-corpus/dev/speaker-review/authorization/<uuid-v4>.json`,
owned by `root:root`, mode `0600`, with one trailing newline:

```json
{"archive_sha256":"<64 lowercase hex>","authorization_id":"<uuid-v4>","maximum_authorized_cost_microusd":5000000,"operation":"observe_fourth_adjudication","purpose":"speaker_review","run_id":"speaker-review-<16 lowercase hex>","schema_version":1,"season_number":2}
```

Never reuse an authorization UUID for a different request or copy another
phase's authorization. The request is bound to this operation, archive, run,
and ceiling. After the root intent exists, a safe retry of a `waiting` result
must use this exact same request and authorization; minting a replacement UUID
for the same run will be rejected as a binding change.

The operator needs the pinned SSH identity, known-hosts file, archive SHA-256,
run ID, authorization UUID, and the authorized micro-USD ceiling:

```bash
uv run python scripts/observe_fourth_private_speaker_review_adjudication.py \
  --archive-sha256 <archive-sha256> \
  --run-id <speaker-review-run-id> \
  --authorization-id <authorization-uuid> \
  --maximum-authorized-cost-microusd <ceiling> \
  --identity <root-controlled-private-key> \
  --known-hosts <root-controlled-known-hosts> \
  --host <review-host>
```

The client sends only the canonical request over host-key-pinned SSH. The
forced dispatcher accepts only
`speaker-review-observe-fourth-adjudication-v1` and the root helper acquires
transfer, deployment, and review locks in that order.

## Outcomes

The aggregate response contains no provider payload. `waiting` means the
active batch is not complete and the run and inventory were not changed.
`observed` means only `adjudication-part-0004-output.jsonl` (and a provider
error artifact when present) was downloaded and the state advanced to
`adjudication_part_completed` with count four. `already_observed` is the
provider-free replay result from the authenticated root receipt. `failed`
records a terminal provider failure at count three. `reconciliation_required`
means the provider or filesystem result was ambiguous; stop and investigate.

There is no polling loop and no retry of the provider retrieve. The centralized
observation transport has zero retries and the worker timeout is finite. A
`waiting` result may be invoked again later with the exact same canonical
request. Do not rerun a terminal or partially-written operation after an error
unless the root receipt and immutable inventory are reconciled first.

## Verification and recovery

The root coordinator double-reads the complete flat inventory, verifies the
Phase 75 submission receipt and Phase 74 prefix chain, validates the active
release/image/configuration, and publishes a create-once aggregate receipt
under the private observation-receipts directory. A successful replay must
find both the observation intent and its matching receipt; a terminal state
without the receipt is intentionally closed and requires explicit operator
reconciliation. Never fabricate a receipt from a generic intent or from an
untrusted worker response.

After a successful observation, leave the run at the part-completed
checkpoint. Part-five submission, final review, promotion, and ingestion are
separate future authorizations and commands.
