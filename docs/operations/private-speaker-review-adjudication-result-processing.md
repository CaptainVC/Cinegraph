# Private speaker-review adjudication-result processing

Phase 77 implements the secretless, provider-free transition after every
adjudication part has been observed. It computes decisions locally and either
finalizes a fully resolved run or prepares final-review request parts. It does
not call OpenAI, submit final review, promote reviewed files, ingest data, or
open a human-review workflow.

## Required checkpoint

The selected run must be canonical, private, and in
`adjudication_part_completed` with a positive part count and
`adjudication_completed_part_count == adjudication_part_count`. Every primary
and adjudication part must have exactly one request, intent journal, completion
journal, and output. An API-error artifact is optional per part, but any present
artifact participates in exact custom-ID reconciliation. Batch and input-file
IDs must be complete, unique, and agree with their journals.

The authorization ceiling must be positive and no greater than 5 USD in
micro-USD. Processing also enforces the run's persisted maximum and the
centralized speaker-review maximum. The root host boundary validates and
atomically claims this authorization before the worker starts. A claim can be
replayed only with the same canonical request and archive/run binding; changing
any binding fails closed. Operators must use the pinned-SSH client and must not
expose the container command directly.

## Isolated worker contract

The Compose profile is
`corpus-speaker-review-process-adjudication-results`. Its worker accepts only a
canonical operation request plus six expected SHA-256 digests: run state,
source artifacts, requests, submission journals, provider outputs, and derived
artifacts. It rejects provider-key and proxy environment variables, constructs
the application workflow with an offline gateway, and runs with no network or
secret mount.

The preflight inventory is exact. Unknown requests, journals, outputs, files,
or directories stop processing. A fresh run may contain a deterministic prefix
of the three new adjudication-derived artifacts for crash recovery, but every
existing byte must match recomputation. The postflight inventory verifies the
canonical persisted state, exact expected shape, stable directory identity,
immutable evidence, and unchanged pre-existing derived bytes.

## Outcomes

`completed` means every candidate was resolved and the exact reviewed subtitle
set, review ledger, and calibration sample were written locally.
`final_review_prepared` means unresolved candidates remain and deterministic
final-review request parts were written, while all final-review provider IDs,
completion counters, retry counters, and actual costs remain empty or zero.
`already_processed` is a byte-valid replay of either checkpoint. The response
contains only status, part and candidate counts, decision counts, and integer
micro-USD costs. Actual costs are conservatively rounded up to whole micro-USD,
and the response repeats the request's exact micro-USD authorization ceiling;
the root coordinator independently rejects any total above that ceiling.

Any missing, extra, changed, non-canonical, over-budget, symlinked, or
inconsistent evidence fails closed. Preserve the run directory for explicit
reconciliation; never fabricate a state file, request part, ledger, digest, or
receipt. The root receipt contains only non-sensitive binding identifiers,
authorization-claim and intent digests, six post-evidence digests and counts,
the aggregate, and status. Final-review submission remains a separate
operation.

## VPS boundary (Phase 78)

Invoke this transition only through the workstation client
`scripts/private_speaker_review_adjudication_result_processing_client.py` and
its pinned SSH command `speaker-review-process-adjudication-results-v1`. The
forced dispatcher accepts no arguments and the root helper accepts no
operator-supplied paths. It binds the current immutable release, source
archive, run directory, six inventory digests, and the complete predecessor
receipt chain before starting the coordinator.

The coordinator claims the fresh authorization atomically and writes a
minimal canonical receipt. A replay is accepted only when the authorization,
request, archive, run, release, and predecessor binding are byte-identical.
The isolated Compose worker uses a read-only source mount, an exact run-scoped
writable mount, no network, no provider credentials, and bounded output. If a
worker fails, the helper removes it only after its complete container identity
matches the expected service, image, command, security settings, and mounts;
otherwise it leaves the container for investigation. The boundary never
submits final review, promotes a corpus, or ingests data.

## Refresh and verify the VPS boundary

After deploying the accepted release, install the new helper and finite sudo
grant before using the command, then verify the complete host contract:

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

Bootstrap accepts only reviewed finite predecessor policies or this Phase 78
policy. It installs all helpers first, validates the finite sudoers file, and
replaces the forced-command dispatcher last.

## Authorize and invoke one run

Create a fresh canonical authorization at
`/opt/cinegraph/shared/private-corpus/dev/speaker-review/authorization/<uuid4>.json`
owned by root with mode `0600`. It must contain exactly the request fields for
operation `process_adjudication_results`, purpose `speaker_review`, schema
version `1`, season `2`, the archive digest, run ID, fresh lowercase UUID4, and
approved micro-USD ceiling. Do not reuse an authorization from any submission
or observation operation.

From the pinned workstation, invoke:

```powershell
uv run python scripts/process_private_speaker_review_adjudication_results.py `
  --archive-sha256 <private-object-sha256> `
  --run-id speaker-review-<16-lowercase-hex> `
  --authorization-id <fresh-processing-uuid4> `
  --maximum-authorized-cost-microusd <approved-cost-microusd> `
  --identity <review-private-key> `
  --known-hosts <pinned-known-hosts-file> `
  --host <dev-host>
```

Repeat only the byte-identical request after an interruption. Never delete or
edit a claim, intent, receipt, run artifact, journal, provider output, or
derived file to make a retry pass. An ambiguous pending file, orphan record,
changed predecessor, changed runtime, or mismatched terminal inventory requires
explicit reconciliation.
