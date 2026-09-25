# Private next final-review submission

`speaker-review-submit-next-final-review-v1` is the Phase 81 continuation after
the authenticated Phase80 part-one observation. Invoke it through the pinned
SSH client only after the Phase80 result is `observed` or `already_observed`.

The root boundary authenticates the Phase80 receipt, Phase79 receipt, Phase78
predecessor, run state, release/image/configuration/model/prompt/endpoint/
window bindings, exact inventory, and part-two request digest. It then starts
one digest-bound egress Compose worker with a 0400 OpenAI secret. A run with no
part two returns `all_parts_completed` without provider access. An exact
submitted replay is provider-free; ambiguous or partial writes return
`reconciliation_required`.

The aggregate intentionally excludes provider IDs, private paths, prompts,
request payloads, and secret contents. This boundary never observes, parses,
retries, finalizes, promotes, or ingests.
