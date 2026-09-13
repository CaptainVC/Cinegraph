# Private speaker-review first-adjudication observation

Phase 70 observes exactly the first submitted Terra adjudication part through
the pinned `cinegraph-review` SSH identity and forced command
`speaker-review-observe-first-adjudication-v1`.

The operation can check one existing provider Batch and download its first-part
result when complete. It cannot submit provider work, observe a later part,
parse adjudication output, enter final review, promote reviewed files, or ingest
application stores. It does not poll: a still-running Batch returns `waiting`.

## Preconditions and freeze window

The run must be at `adjudication_submitted`, with exactly one adjudication Batch
and input-file identifier and zero completed adjudication parts. The Phase 69
submission intent and receipt, the preceding review evidence chain, the active
release/image/configuration, the private archive, and all five run inventory
digest classes must agree.

Keep the active checkout clean and equal to `origin/main`. Do not edit run,
authorization, intent, receipt, journal, request, output, derived, or private
source files during this operation.

After deploying the accepted release, refresh and verify the review boundary
using the existing pinned public-key files and fingerprints:

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

Bootstrap accepts only finite previously released sudo-policy states. It
installs and verifies the new no-argument helper before adding the seventh exact
sudo grant, then replaces the forced-command dispatcher last.

## Authorize one observation

Create a fresh canonical JSON record, owned by root and mode `0600`, at:

```text
/opt/cinegraph/shared/private-corpus/dev/speaker-review/authorization/<fresh-lowercase-uuid4>.json
```

Use the exact fields: archive SHA-256, run ID, authorization ID, maximum
authorized micro-USD, operation `observe_first_adjudication`, purpose
`speaker_review`, schema version `1`, and season number `2`. Use a new UUID4.
The ceiling must cover the already-recorded actual primary cost plus the
deterministic estimate for all prepared adjudication request parts. Observation
does not create new model work, but retaining this ceiling prevents a request
from being replayed against a differently costed run.

The authorization contains no key, provider identifier, prompt, subtitle,
rationale, output, or path. The OpenAI key remains in `/etc/cinegraph/dev.env`
and reaches the isolated worker only as a read-only Compose secret.

## Invoke from the trusted workstation

```powershell
uv run python scripts/observe_first_private_speaker_review_adjudication.py `
  --archive-sha256 <private-object-sha256> `
  --run-id speaker-review-<16-lowercase-hex> `
  --authorization-id <fresh-observation-uuid4> `
  --maximum-authorized-cost-microusd <approved-cost-microusd> `
  --identity <review-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host>
```

The client pins the host key; disables forwarding, TTY, password fallback, and
ambient SSH configuration; sends one canonical request; and rejects stderr,
oversized output, duplicate or non-canonical JSON, unknown fields, and
inconsistent counts, costs, or status.

## Outcomes

| Status | Meaning | Operator action |
| --- | --- | --- |
| `waiting` | The exact provider Batch is not terminal. No run file changed and no root completion receipt was written. | After an appropriate delay, retry the exact same authorized request and authorization ID. |
| `observed` | Part-one output was downloaded, the run moved to `adjudication_part_completed`, and root published a completion receipt. | Continue only through a later provider-free adjudication-processing boundary. |
| `already_observed` | The completed checkpoint and root receipt revalidated without provider access. | Treat as idempotent success. |
| `failed` | The provider reported a terminal failure and bounded failure evidence was persisted and receipted. | Preserve evidence and investigate; do not submit replacement work through this command. |
| `reconciliation_required` | Provider/local evidence conflicts or an exact safe transition cannot be proven. No completion receipt was published. | Freeze the run and reconcile protected evidence manually; do not delete files or broaden the command. |
| rejection | Authorization, predecessor, runtime, filesystem, cost, worker, or response validation failed. | Preserve all evidence and investigate on the VPS. |

Responses and receipts contain only bounded aggregate values. Provider Batch
and file IDs stay in protected run state and submission journals.

## Recovery and isolation

Retry only the exact same authorized request. Root preserves the original
observation intent until a terminal result can be proven; a different
authorization or cost ceiling conflicts with that create-once binding and fails
closed. A terminal checkpoint without its root receipt is reconstructed from the
intent and physically revalidated inventory; it does not reopen the secret or
contact the provider.

The worker's sole writable host mount is the digest-selected `review-runs`
directory. The key is a mode-`0400` secret file, never an argument or environment
variable. The container is non-root, capability-free, read-only-root,
resource-bounded, and connected only to the Compose `egress` network. That
network is not an OpenAI-only allowlist. Fixed-name cleanup occurs only after
the complete container identity and exact mounts match this boundary.

## Deliberate boundary

Stop after `observed`, `already_observed`, `failed`, or a non-terminal outcome.
Phase 70 never parses the Terra response, submits/observes another part,
performs final or human review, promotes corpus files, or ingests them.
