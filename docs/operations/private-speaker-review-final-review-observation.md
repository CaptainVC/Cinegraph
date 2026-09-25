# Private speaker-review final-review part-one observation

Phase 80 exposes the finite SSH command
`speaker-review-observe-final-review-part-one-v1`. It is authorized by the
Phase79 submission authorization and accepts only the matching archive, run,
and final-review request ceiling. Before Compose or secret access, the root
coordinator verifies the Phase79 authorization claim, intent and receipt,
application submission journals, exact submitted inventory, and the linked
Phase78 predecessor chain. It also checks the pinned release, image,
configuration, model, prompt, endpoint, window, and cumulative budget.

The isolated egress worker retrieves final-review part one once. A waiting
result leaves the run inventory unchanged. An observed result requires the
part-one output file to be durable and `final_review_completed_part_count` to
be exactly one while `run_status` remains `final_review_submitted`. A terminal
failure retains count zero. Ambiguous journals, partial changes, and terminal
state without a matching root receipt require reconciliation. Exact receipt
replay is provider-free.

The boundary cannot submit again, observe another part, retry, parse or
finalize output, perform human review, promote corpus files, or ingest data.
The aggregate contains only costs, counts, and the bounded status; provider
IDs, paths, prompts, response payloads, and secret contents remain private.

Use the pinned SSH client from the approved operator host:

```text
uv run python scripts/observe_final_private_speaker_review.py \
  --archive-sha256 <archive-sha256> \
  --run-id <run-id> \
  --authorization-id <phase79-authorization-uuid> \
  --maximum-authorized-cost-microusd <authorized-micro-usd> \
  --identity <private-key> \
  --known-hosts <pinned-known-hosts> \
  --host <review-host>
```
