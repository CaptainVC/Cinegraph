# Private speaker-review primary observation

Phase 62 introduces the bounded transition that checks one already-submitted
primary speaker-review Batch part. It cannot create a Batch or advance into
another review stage.

## Preconditions

The run must be in the canonical digest-bound layout created by the private
speaker-review preparation and primary-submission boundaries:

```text
<speaker-review-runs-root>/sha256-<archive-digest>/review-runs/<run-id>
```

The run state must be `primary_submitted`, identify exactly the active submitted
part, and remain within both the approved micro-USD ceiling and the centralized
speaker-review maximum. Observation uses the existing provider identifier only
inside the private run state. Do not copy provider identifiers, output JSONL,
subtitle content, paths, or the OpenAI key into tickets, GitHub, or shared logs.

The profile is not yet a workstation-facing VPS command. A later phase will add
the root coordinator and forced-command dispatch after it can bind observation to
the active immutable release and create root-owned intent/receipt evidence.

## Outcomes

Each invocation performs at most one provider status lookup:

| Result | Durable behavior |
| --- | --- |
| `waiting` | The active Batch is non-terminal; the run state is unchanged. |
| `observed` | The active part's output and optional error file are stored, the completed count advances by one, and state becomes `primary_part_completed`. |
| `already_observed` | The explicit checkpoint already exists; no provider client or secret is needed. |
| `failed` | The provider reported a configured terminal failure and the run is durably failed; no replacement is submitted. |
| `reconciliation_required` | Persisted evidence is missing, changed, or ambiguous; automation must stop. |

The safe aggregate includes only operation, purpose, season, run ID, estimated
primary cost, total primary part count, observed part count, and status. It never
includes a Batch/file ID, provider response, artifact name, source locator, prompt,
or credential.

## Container boundary

`corpus-speaker-review-observe-primary` runs as UID/GID `10002`, with a read-only
root filesystem, dropped capabilities, no-new-privileges, bounded CPU/memory/PIDs,
and only the egress network. The only writable bind is the exact `review-runs`
root at `/review-workspace/review-runs`. The source corpus, database, Qdrant,
knowledge volume, preparation receipt, and application credentials are not
mounted. `OPENAI_API_KEY` is supplied only through `/run/secrets/openai_api_key`.

Never invoke the generic `advance` command as a substitute. It has intentionally
broader semantics and can cross later paid boundaries. Do not delete or replace a
private output artifact to force a retry; differing evidence requires explicit
reconciliation.

## Next transition

If only one primary part exists, the checkpoint is ready for a future bounded
primary-result processing operation. If more parts exist, a future separately
authorized operation may submit exactly the next part and return the run to
`primary_submitted`. Adjudication, final review, promotion, and ingestion remain
separate later transitions.
