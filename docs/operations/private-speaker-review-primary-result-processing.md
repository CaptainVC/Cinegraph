# Private speaker-review primary-result processing

Phase 67 adds a bounded, provider-free LangGraph transition for a fully observed
Luna primary-review result set. It deliberately stops before any Terra request
is submitted.

## Preconditions

The private run must contain every primary request, completed submission journal,
observed output, optional API-error artifact, candidate file, source manifest,
and a `primary_part_completed` state whose completed count equals its total part
count. State must still have empty adjudication/final-review fields and match the
central schema, model, prompt, and cost policy.

The isolated worker additionally requires exact SHA-256 bindings, computed by a
trusted root coordinator, for:

1. `run-state.json`;
2. immutable candidate, source-manifest, and primary-request artifacts;
3. completed primary-submission journals;
4. primary outputs and optional API-error evidence; and
5. any deterministic derived files present during crash recovery or replay.

Direct Compose invocation remains unsupported. Phase 68 supplies the required
forced SSH command, source/run mounts, authorization chain, serialization locks,
and root-owned receipt evidence; use its
[VPS boundary runbook](private-speaker-review-primary-result-processing-boundary.md).

## Outcomes

| Result | Durable behavior |
| --- | --- |
| `adjudication_prepared` | Luna output and cost are validated; consensus decisions and deterministic Terra request parts are written; no provider call occurs. |
| `completed` | Every candidate met consensus; reviewed SRT output, decision ledger, and calibration sample are written locally. |
| `already_processed` | An exact completed or adjudication-prepared checkpoint and its full inventory were revalidated without a provider call. |
| failure | Invalid state, cost, inventory, link, owner/mode, digest, replay, or derived content fails closed with one generic stderr marker. |

The stdout aggregate contains only the run ID, operation/purpose/season, run
status, candidate and part counts, consensus count, adjudication part count, and
unresolved count. Provider IDs, model output, source text, evidence, paths,
prompts, and credentials remain private.

## Container boundary

`corpus-speaker-review-process-primary-results` runs as UID/GID `10002` with:

- `network_mode: none` and no OpenAI secret;
- read-only root filesystem, all capabilities dropped, and
  `no-new-privileges`;
- bounded memory, CPU, PIDs, and a no-exec/nosuid temporary filesystem; and
- only the future root-selected read-only source mount and exact writable
  digest-selected review-runs mount.

The offline gateway throws if any code attempts to submit, retrieve, or download
a provider resource. Do not replace this operation with the legacy broad
`advance` command.

## Recovery rule

Create-once derived files may be reused only when their bytes equal the newly
computed deterministic bytes. Missing files can be completed while state is
still at the pre-processing checkpoint. Unexpected files, changed input bytes,
partial terminal output, or a conflicting checkpoint require operator
reconciliation; never delete evidence to make the job appear retryable.

## Next transition

Phase 68 exposes this exact transition through the root-owned VPS command and
receipt chain. A later separately authorized transition will submit the first
prepared Terra adjudication part. This phase does not observe Terra, invoke Sol
final review, promote the corpus, or ingest it.
