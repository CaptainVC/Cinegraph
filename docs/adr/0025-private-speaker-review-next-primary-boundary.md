# ADR-0025: Private speaker-review next-primary host boundary

Phase 65 adds one separately authorized VPS operation: after Phase 63 has
observed primary part one, the root-only review coordinator may submit primary
part two. The forced SSH command is `speaker-review-submit-next-primary-v1`.

The coordinator binds the fresh UUID4 authorization, Phase 60 preparation
receipt, Phase 61 submission receipt, Phase 63 observation receipt, active
release SHA, immutable image reference, configuration hash, archive/run
identity, cost ceiling, exact part-two request SHA, and pre/post artifact,
journal, and state hashes. A one-part run is rejected by this v1 boundary.

The worker has only the exact Dev egress network, OpenAI Compose secret, and the
digest-selected review-runs directory. It submits one Batch at most and never
observes output, parses results, adjudicates, finalizes, or ingests. The expected
request, Phase 63 artifact-set, journal-set, and run-state hashes are passed into
the worker and rechecked before the secret is opened. The root-verified state is
then carried through the LangGraph transition without a second filesystem load,
and the request hash is checked against the exact bytes used by the workflow
before the provider call.

Root intent and receipt publication is create-once and fsync-backed. A
completed part-two journal can repair a missing state publication without a
provider retry; an unresolved intent, receipt conflict, artifact drift, image
drift, release drift, or mismatched authorization fails closed.

See the [next-primary runbook](../operations/private-speaker-review-next-primary-boundary.md).
