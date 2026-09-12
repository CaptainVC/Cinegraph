# Private first-adjudication submission boundary

Phase 69 submits exactly the first prepared Terra adjudication part through the
pinned `cinegraph-review` SSH identity and forced command
`speaker-review-submit-first-adjudication-v1`.

This is a paid provider boundary. It cannot observe a Batch, submit a later
part, process adjudication output, invoke final review, promote reviewed files,
or ingest PostgreSQL/Qdrant.

## Preconditions and freeze window

The run must be at `adjudication_prepared` with a valid Phase 68 root intent and
completion receipt. Preparation, both Luna primary submissions and
observations, provider-free result processing, and the active release, image,
configuration, archive, run, cost ceiling, and source manifest must form one
consistent evidence chain.

The run must contain the exact deterministic adjudication request parts named
by state and no adjudication submission journal or provider identifier. Keep
the active checkout clean and equal to `origin/main`. Do not edit source, run,
authorization, intent, journal, output, derived, or receipt files during this
operation.

After deploying the accepted release, refresh and verify the review boundary
with the existing pinned public-key files and fingerprints:

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
installs and verifies the new no-argument root helper before adding its one
exact sudo grant, then replaces the forced-command dispatcher last.

## Authorize one submission

Create a fresh canonical JSON record owned by root with mode `0600` at:

```text
/opt/cinegraph/shared/private-corpus/dev/speaker-review/authorization/<fresh-lowercase-uuid4>.json
```

The exact fields are archive SHA-256, run ID, authorization ID, maximum
authorized micro-USD, operation `submit_first_adjudication`, purpose
`speaker_review`, schema version `1`, and season number `2`. Use a new UUID4.
The ceiling must cover actual primary usage plus the deterministic estimate for
all prepared adjudication requests.

The authorization contains no API key, provider identifier, request text,
subtitle, prompt, rationale, or filesystem path. The OpenAI key remains in the
VPS environment file and reaches the container only through the read-only
Compose secret file.

## Invoke from the trusted workstation

```powershell
uv run python scripts/submit_first_private_speaker_review_adjudication.py `
  --archive-sha256 <private-object-sha256> `
  --run-id speaker-review-<16-lowercase-hex> `
  --authorization-id <fresh-submission-uuid4> `
  --maximum-authorized-cost-microusd <approved-cost-microusd> `
  --identity <review-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host>
```

The client pins the host key, disables forwarding, TTY, password fallback and
ambient SSH configuration, sends one canonical request over stdin, and rejects
remote stderr, oversized output, duplicate/non-canonical JSON, unknown fields,
or inconsistent counts and costs.

## Outcomes

| Status | Meaning | Operator action |
| --- | --- | --- |
| `submitted` | Exactly adjudication part one was submitted and both workflow and root completion evidence were published. | Continue only through a separately authorized observation boundary. |
| `already_submitted` | The exact submitted checkpoint and root receipt revalidated; no provider submission was repeated. | Treat as idempotent success. |
| `reconciliation_required` | A provider submission intent exists without a trustworthy completion record, so the external outcome is ambiguous. | Reconcile against protected provider/VPS evidence; never delete the intent or retry blindly. |
| rejection | Authorization, predecessor, runtime, filesystem, cost, worker, or response validation failed. | Preserve all evidence and investigate on the VPS. |

Responses and root receipts contain bounded counts, cost totals, and status
only. Provider Batch/file IDs remain exclusively in the protected run state and
workflow completion journal.

## Recovery and isolation

Retry only the exact same authorized request. The root coordinator revalidates
all five pre-state digest groups and the full predecessor receipt chain before
any worker launch. A terminal submitted state without a root receipt must pass
the worker's idempotent application validation and may not create another
Batch. A root receipt without its intent, changed deterministic request, stale
runtime, conflicting journal, or partial state fails closed.

The worker's sole writable mount is the digest-selected review-runs directory.
Its key is a mode-`0400` secret file, not an environment variable or argument.
The container is non-root, capability-free, read-only-root, resource-bounded,
and attached only to the Compose `egress` network. That network is not an
OpenAI-only destination allowlist. Before a run, the host helper removes a
fixed-name stale worker only after its complete identity matches this boundary.
After a failed run it re-attests that identity before cleanup, so a colliding
container is rejected rather than deleted.

## Deliberate boundary

Stop after `submitted` or `already_submitted`. Phase 69 never observes Terra,
submits part two, interprets adjudication, performs final/human review, promotes
the corpus, or ingests it into the application.
