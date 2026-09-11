# Private speaker-review primary-result processing VPS boundary

Phase 68 exposes the provider-free primary-result transition through the pinned
`cinegraph-review` SSH identity and exact forced command
`speaker-review-process-primary-results-v1`.

It parses the complete observed Luna result set and either prepares deterministic
Terra requests or completes unanimous high-confidence review locally. It cannot
call OpenAI, submit or observe a Batch, choose another corpus or run, promote
private data, or ingest into application stores.

## Preconditions and freeze window

The requested run must have matching root-owned evidence for preparation,
primary part-one submission and observation, primary part-two submission and
observation, and a `primary_part_completed` state whose completed count equals
its total part count. Its source manifest, candidates, requests, completed
submission journals, outputs, optional API-error evidence, release SHA, image,
configuration, archive digest, run ID, and approved cost ceiling must all agree.

Treat preparation through processing as one immutable evidence chain. Keep the
active checkout clean and equal to `origin/main`; do not edit private source,
run, authorization, intent, journal, output, derived, or receipt files.

After deploying the accepted release, refresh the review host boundary and then
run its check mode with the existing review, corpus, and deployment public-key
files and pinned fingerprints:

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

Bootstrap accepts only the finite previously released sudo-policy states. It
installs and verifies the new root helper before adding its one exact sudo grant,
then replaces the forced-command dispatcher last.

## Authorize one processing attempt

Create a fresh canonical JSON record owned by root with mode `0600` at:

```text
/opt/cinegraph/shared/private-corpus/dev/speaker-review/authorization/<fresh-lowercase-uuid4>.json
```

The exact fields are archive SHA-256, run ID, authorization ID, maximum
authorized micro-USD, operation `process_primary_results`, purpose
`speaker_review`, schema version `1`, and season number `2`. Use a new UUID4;
reusing any submission or observation authorization is rejected.

The authorization carries no API key, provider Batch/file identifier, model
output, subtitle content, prompt, or filesystem path. Its cost value is a
fail-closed ceiling on the already observed primary usage; processing itself is
provider-free.

## Invoke from the trusted workstation

```powershell
uv run python scripts/process_private_speaker_review_results.py `
  --archive-sha256 <private-object-sha256> `
  --run-id speaker-review-<16-lowercase-hex> `
  --authorization-id <fresh-processing-uuid4> `
  --maximum-authorized-cost-microusd <approved-cost-microusd> `
  --identity <review-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host>
```

The client rejects remote stderr, oversized output, duplicate/non-canonical
JSON, unknown fields, invalid counts/status combinations, host-key mismatch,
and any attempt to widen the fixed remote command.

## Expected outcomes

| Status | Durable effect | Next action |
| --- | --- | --- |
| `adjudication_prepared` | Primary verdicts/decisions and one or more deterministic Terra request parts are verified and receipted. | Use a future separately authorized Terra submission boundary. |
| `completed` | Every candidate met consensus; reviewed SRT, decision ledger, and calibration evidence are verified and receipted. | Continue only through a future corpus-promotion boundary. |
| `already_processed` | The terminal checkpoint, predecessor chain, intent, receipt, source, and complete inventory revalidated. | Treat as an idempotent success. |
| rejection | Nothing is declared successful; evidence is preserved. | Inspect protected VPS evidence and reconcile explicitly. |

The response and root receipt contain bounded counts and status only. Private
text, model responses, rationales, provider IDs, prompts, secrets, and private
paths remain inside the protected workspace.

## Isolation and recovery

The root coordinator computes separate pre-state digests for run state,
immutable artifacts, journals, outputs/API errors, and derived files. The worker
must observe all five exact sets before LangGraph runs. Its outer source mount is
read-only; only the exact nested run directory is writable. The service has no
network and no OpenAI secret.

If execution is interrupted after its intent is written, repeat only the exact
same authorized request. The coordinator may repair a receipt only when it can
prove the deterministic terminal transition and every immutable binding. A
terminal run without a receipt is passed through the offline LangGraph worker's
idempotent application-level validation again before root publishes the repaired
receipt; matching filenames and counts are not sufficient.
Never delete or edit an intent, receipt, journal, output, or derived file to make
a retry appear valid. Orphaned receipts, conflicting bytes, unexpected files,
partial terminal artifacts, or a changed runtime require operator
reconciliation.

## Deliberate boundary

This command stops at `adjudication_prepared` or consensus-only `completed`.
It does not submit Terra adjudication, observe adjudication, invoke final review,
request human corrections, promote reviewed files, or ingest PostgreSQL/Qdrant.
