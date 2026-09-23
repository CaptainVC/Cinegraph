# Private speaker-review final-review submission

Phase 79 exposes `speaker-review-submit-final-review-v1` for one paid request.
The operator sends the canonical request through the pinned SSH client. The
root helper verifies the archive/run/authentication chain, clean release and
tracked transitive import closure, Phase 78 processing intent and receipt,
state and all inventory digests, exact file identity, owner, mode and links,
and the release/config/model/prompt/endpoint/window/budget binding.

The isolated Compose worker then validates a fresh
`final_review_prepared` checkpoint, recomputes all final-review requests and
conservative integer micro-USD cost, and reads the 0400 OpenAI secret only
after those checks. It makes exactly one `graph.final_review` call with the
verified state. The only allowed fresh mutations are `run-state.json` and the
part-one intent/completed journals. A submitted checkpoint is replayed
provider-free and must match those journals exactly.

The boundary rejects completed, failed, generic `needs_human`, retry,
observation, finalization, promotion, ingestion, and part-two requests. A
missing or intent-only journal returns `reconciliation_required` and never
reads the secret or retries the provider call. Provider identifiers, paths,
prompts, payloads, and secret values are never returned in the aggregate.

The maximum is enforced against prior actual primary plus adjudication cost
plus the recomputed final-review estimate, and against both the request and
the centralized configured ceilings. Decimal conversion rounds upward to
integer micro-USD and rejects non-finite values.
