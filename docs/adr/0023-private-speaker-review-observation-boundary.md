# ADR-0023: Private speaker-review observation boundary

- Status: accepted for implementation
- Date: 2026-09-08

## Context

ADR-0022 introduced a non-submitting container operation that can observe one
active primary-review Batch part. Phase 63 exposes only the first part submitted
by the Phase 61 boundary; later-part submission and observation remain separate
future transitions. The worker intentionally cannot prove which
archive directory the host mounted, whether the run was previously authorized
and submitted, or whether its code and image match the active immutable release.
Exposing it directly would leave those host-authority decisions outside the
audited boundary.

Observation is safe to repeat while a provider job is pending, but a completed
download mutates private evidence and run state. The host therefore needs durable
intent and completion evidence without treating an ordinary `waiting` result as
final or accidentally preventing a later observation using the same authorization.

## Decision

Extend the dedicated `cinegraph-review` forced-command identity with exactly one
additional command, `speaker-review-observe-primary-v1`. The dispatcher maps it
to a separate no-argument root helper. The existing primary-submit command and
helper remain independent. Sudo policy names both exact helpers and grants no
shell, arbitrary arguments, environment preservation, or broad root access.

The observation helper validates root ownership and modes, obtains the existing
transfer, deployment, and speaker-review locks in that order, verifies the clean
active release against `origin/main`, and executes a standard-library-only root
coordinator with `python3 -I -S -B` in an empty environment. On failure it may
remove only the exact observation service/container; it never deletes run or
receipt evidence.

The root coordinator accepts one bounded canonical request over standard input.
It requires an exact root-owned authorization plus matching Phase 60 preparation
and Phase 61 primary-submission receipts. Those records bind archive digest, run,
season, purpose, cost ceiling, configuration, release, image, and the completed
primary-submission journal. The digest selects the fixed nested run layout; no
caller path or provider identifier is accepted.

Before starting the worker, the coordinator writes a create-once observation
intent containing hashes of the authorization, prerequisite receipts, pre-state,
and complete private artifact inventory. It then mounts only the digest-bound
`review-runs` directory into the dedicated observation service and passes four
non-secret values. The OpenAI key remains a Compose secret file.

The coordinator accepts only these outcomes:

- `waiting`: state and inventory must be unchanged. The intent remains, but no
  completion receipt is written, so the same authorization may safely observe
  again later.
- `observed`: exactly the active primary output and optional API-error artifact
  may be added; the completed count advances once and state becomes
  `primary_part_completed`.
- `failed`: only the configured terminal-error artifact may be added; completed
  count is unchanged and state becomes `failed`.
- `reconciliation_required`: no later action is inferred and no evidence is
  deleted or overwritten.

After an `observed` or `failed` result, strict postconditions are recorded in a
create-once root receipt. A matching receipt is revalidated without starting a
container. If the worker completed but the root receipt was not written, the
coordinator validates the legal state/artifact delta against the retained intent
and repairs only the missing receipt. Changed base artifacts, illegal state
deltas, different authorization, orphan receipts, unexpected filenames, or
runtime drift fail closed.

## Consequences

The workstation can request one status observation without learning provider
IDs, source paths, subtitle text, prompts, or provider payloads. Network access
and the provider secret remain confined to the unprivileged observation worker.
Repeated pending checks are safe and cannot cause model submissions or later
workflow transitions. This v1 host command rejects any state beyond the first
submitted primary part even though the inner observer can support a later part.
Because prerequisite evidence binds the active release, operators must freeze
Dev promotion from preparation through terminal observation; release drift is a
deliberate rejection, not an automatic migration of a paid in-flight run.

This phase does not add an automatic timer or active-run queue. Scheduling is a
later decision because unattended polling needs its own cadence, deadline, and
reconciliation policy. Submitting another primary part, processing primary
verdicts, adjudicating, final reviewing, promoting, and ingesting remain separate
authorized transitions.

See the [observation runbook](../operations/private-speaker-review-observation.md)
for authorization, invocation, and recovery rules.
