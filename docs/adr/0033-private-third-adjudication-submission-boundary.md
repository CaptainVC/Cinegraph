# ADR-0033: Private third-adjudication submission boundary

## Status

Accepted for Phase 73.

## Context

Phase 72 durably observes Terra adjudication part two and stops at
`adjudication_part_completed` with exactly two completed parts. The generic
LangGraph next-part operation is reusable, but the production trust chain must
not infer that any mutable worker checkpoint is authorized. Submission of part
three is another paid, externally visible provider write and therefore needs a
fresh authorization and a root-authenticated immediate predecessor.

Reusing the Phase 71 host command would also blur the audit boundary: its
contract is pinned to completed count one and part two. Widening it would make
an old forced command capable of authorizing future corpus-dependent actions,
and a compromised or stale client could cross more than one reviewed phase.

Provider submission is not transactionally atomic with local state writes.
The command must therefore distinguish a safe exact replay from ambiguous
evidence without automatically paying twice.

## Decision

Add `speaker-review-submit-third-adjudication-v1` as the tenth finite command
on the dedicated `cinegraph-review` SSH identity. It has its own strict wire
contract, pinned client, root helper, Compose service/profile, authorization
operation, intent/receipt namespace, runbook, and tests.

The root coordinator accepts only the Phase 72 terminal checkpoint with:

- status `adjudication_part_completed`;
- `adjudication_completed_part_count == 2`;
- `adjudication_part_count > 2`;
- two unique batch/input ID pairs and complete evidence for both parts; and
- no part-three output or API-error artifacts.

The canonical request contains only archive digest, run ID, fresh UUIDv4,
micro-USD ceiling, fixed operation/purpose/season, and protocol version. The
root intent binds the exact authorization bytes; preparation and Phase 72
intent/receipt digests; active release, immutable image, and configuration;
the exact pre-state and five inventory digest classes; target request digest;
part number/counts; deterministic Terra estimate; and accumulated primary
cost. The entire earlier receipt prefix is revalidated rather than trusting the
Phase 72 files in isolation.

The root and isolated worker independently enforce completed count two and
target part three. The worker invokes the generic application operation
`submit-next-adjudication`, which performs exactly one next-part transition.
It may create only the part-three submission journal pair, append one unique
batch/input ID pair, and move to `adjudication_submitted` while leaving the
completed count at two. It cannot observe or download output, parse verdicts,
enter final review, promote, or ingest.

The worker mounts only the digest-selected run directory read-write and reads
the OpenAI credential from a mode-`0400` Compose secret after request,
inventory, state, journal, cost, and authorization validation. It runs as
UID/GID `10002:10002`, with a read-only root filesystem, no-new-privileges,
all capabilities dropped, bounded processes/memory/CPU, and only the existing
egress network. The egress network is outbound-capable, not an OpenAI-only
allowlist; the root-owned immutable release, Compose file, and Docker daemon
remain in the trusted computing base.

Create-once application journals provide crash recovery. An exact matching
intent/completed pair may repair the narrow state transition without provider
or secret access. Submitted-state replay reconstructs the Phase 72 checkpoint,
recomputes the complete root binding, validates application evidence, and can
repair a missing root receipt provider-free. Intent-only, completed-only,
conflicting, malformed, output-bearing, or otherwise ambiguous evidence
returns `reconciliation_required` and never triggers an automatic paid retry.

After the worker exits, root re-reads the complete inventory twice. Every
preexisting artifact, output, derived file, and journal must be byte-identical.
Only the part-three journal pair and narrow state transition are allowed. Root
then revalidates the active runtime and writes a create-once aggregate-only
receipt that excludes provider IDs and private corpus content.

## Consequences

Part three can be submitted through a separately authorized, auditable,
cost-bounded, crash-safe production boundary without broadening the reusable
LangGraph primitive or any older VPS command. The complete receipt prefix and
immediate Phase 72 checkpoint remain mandatory.

The extra forced command, receipt directory, host-bootstrap state, Compose
service, and tests are deliberate operational duplication: they preserve a
small reviewable capability per externally visible action.

Phase 73 does not observe part three, submit part four or later, parse Terra
output, compute adjudication decisions, enter final review, perform human
review, promote corpus files, or ingest PostgreSQL/Qdrant. Each later action
requires its own reviewed boundary and authorization.

See the [third-adjudication submission runbook](../operations/private-speaker-review-third-adjudication-submission.md).
