# ADR 0032: Private next adjudication observation boundary

Status: Accepted for Phase 72.

Phase 72 observes exactly Terra adjudication part two after the Phase 71
submit-next receipt has durably authenticated it. The host boundary accepts
only a submitted state with two batch/input IDs and `completed_count == 1`.
It binds the complete immutable run inventory, request hash, release/image/
configuration, predecessor intent and receipt, and a fresh one-shot
authorization before starting one isolated provider worker.

An incomplete provider result returns `waiting` without filesystem mutation.
A successful result downloads only part two and durably records its output,
journals, and `adjudication_part_completed` state (`completed_count == 2`).
Replay validates an existing root receipt without provider access. A terminal
checkpoint without its root receipt requires operator reconciliation because
the submission intent cannot authenticate newly downloaded output bytes.
Provider reads use zero SDK retries and finite timeouts. Only the selected run
is mounted writable, and the root verifies the post-worker inventory.
Parsing results, submitting part three, final review, promotion, and
ingestion remain outside this boundary.
