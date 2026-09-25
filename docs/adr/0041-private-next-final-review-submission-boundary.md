# ADR-0041: Private next final-review submission boundary

Phase 81 adds `speaker-review-submit-next-final-review-v1` for exactly one
additional final-review Batch part. The root coordinator authenticates the
Phase80 observed/already-observed receipt, the Phase79 submission receipt, the
Phase78 predecessor, the release and runtime bindings, the complete immutable
inventory, and the exact part-two request digest before starting Compose.

If the corpus has only one final-review part, the command returns
`all_parts_completed` without reading the secret, invoking Compose, or mutating
the run. Otherwise one isolated egress worker may submit part two. Create-once
root claims, intents, and receipts make retries provider-free and route
ambiguous or partial mutations to reconciliation.

The command cannot observe, parse, retry, finalize, request human review,
promote, or ingest. It returns only aggregate status, counts, and costs.
