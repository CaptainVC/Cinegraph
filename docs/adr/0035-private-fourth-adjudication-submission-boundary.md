# ADR-0035: Private fourth-adjudication submission boundary

## Status

Accepted for Phase 75.

## Context

Phase 74 durably observes Terra adjudication part three and stops at
`adjudication_part_completed` with exactly three completed parts. The generic
LangGraph next-part operation is reusable, but the production trust chain must
not infer that any mutable worker checkpoint is authorized. Submission of part
four is another paid, externally visible provider write and therefore needs a
fresh authorization and a root-authenticated immediate predecessor.

Reusing an earlier host command would also blur the audit boundary: its
contract is pinned to an earlier completed count and part. Widening it would make
an old forced command capable of authorizing future corpus-dependent actions,
and a compromised or stale client could cross more than one reviewed phase.

Provider submission is not transactionally atomic with local state writes.
The command must therefore distinguish a safe exact replay from ambiguous
evidence without automatically paying twice.

## Decision

Add `speaker-review-submit-fourth-adjudication-v1` as the twelfth finite command
on the dedicated `cinegraph-review` SSH identity. It has its own strict wire
contract, pinned client, root helper, Compose service/profile, authorization
operation, intent/receipt namespace, runbook, and tests.

The root coordinator accepts only the Phase 74 terminal checkpoint with:

- status `adjudication_part_completed`;
- `adjudication_completed_part_count == 3`;
- `adjudication_part_count > 3` (or exactly three for the aggregate-only path);
- three unique batch/input ID pairs and complete evidence for all three parts; and
- no part-four output or API-error artifacts.

The canonical request contains only archive digest, run ID, fresh UUIDv4,
micro-USD ceiling, fixed operation/purpose/season, and protocol version. The
root intent binds the exact authorization bytes; preparation and Phase 74
intent/receipt digests; active release, immutable image, and configuration;
the exact pre-state and five inventory digest classes; target request digest;
part number/counts; deterministic Terra estimate; and accumulated primary
cost. The entire earlier receipt prefix is revalidated rather than trusting the
Phase 74 files in isolation.

The root and isolated worker independently enforce completed count three and
target part four. The worker invokes the generic application operation
`submit-next-adjudication`, which performs exactly one next-part transition.
It may create only the part-four submission journal pair, append one unique
batch/input ID pair, and move to `adjudication_submitted` while leaving the
completed count at three. It cannot observe or download output, parse verdicts,
enter final review, promote, or ingest.

The worker mounts only the digest-selected run directory read-write and reads
the OpenAI credential from a mode-`0400` Compose secret after request,
inventory, state, journal, cost, and authorization validation. It runs as
UID/GID `10002:10002`, with a read-only root filesystem, no-new-privileges,
all capabilities dropped, bounded processes/memory/CPU, and only the existing
egress network. The egress network is outbound-capable, not an OpenAI-only
allowlist; the root-owned immutable release, Compose file, and Docker daemon
remain in the trusted computing base.

If the authenticated Phase 74 receipt shows `part_count == completed_count == 3`,
the command returns `all_parts_completed` without reading the secret, launching
Compose, or mutating the run. Any Phase 75 root receipt or part-four application
journal in that terminal shape is ambiguous and rejected. The current active
release/image/configuration binding must still match the authenticated chain.
Create-once application journals provide crash recovery. An exact matching
intent/completed pair may repair the narrow state transition without provider
or secret access. Submitted-state replay reconstructs the Phase 74 checkpoint,
recomputes the complete root binding, validates application evidence, and can
repair a missing root receipt provider-free. Intent-only, completed-only,
conflicting, malformed, output-bearing, or otherwise ambiguous evidence
returns `reconciliation_required` and never triggers an automatic paid retry.

After the worker exits, root re-reads the complete inventory twice. Every
preexisting artifact, output, derived file, and journal must be byte-identical.
Only the part-four journal pair and narrow state transition are allowed. Root
then revalidates the active runtime and writes a create-once aggregate-only
receipt that excludes provider IDs and private corpus content.

## Consequences

Part four can be submitted through a separately authorized, auditable,
cost-bounded, crash-safe production boundary without broadening the reusable
LangGraph primitive or any older VPS command. The complete receipt prefix and
immediate Phase 74 checkpoint remain mandatory.

The extra forced command, receipt directory, host-bootstrap state, Compose
service, and tests are deliberate operational duplication: they preserve a
small reviewable capability per externally visible action.

Phase 75 does not observe part four, submit part five or later, parse Terra
output, compute adjudication decisions, enter final review, perform human
review, promote corpus files, or ingest PostgreSQL/Qdrant. Each later action
requires its own reviewed boundary and authorization.

See the [fourth-adjudication submission runbook](../operations/private-speaker-review-fourth-adjudication-submission.md).
