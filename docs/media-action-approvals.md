# Authorized media actions

Media writes are provider-neutral commands. Authorization checks the authenticated
principal, target profile, provider owner, and permitted command kind before an
approval is created and again immediately before execution.

The LangGraph workflow proposes an immutable preview, persists an approval request,
and interrupts. Approval is bound to a SHA-256 digest of the exact command parameters.
Resume fails closed if the command, profile, provider connection revision, or
authorization changed. Approved commands execute with an idempotency key, verify the
external state, and emit immutable proposed, approved/rejected, executed, and verified
audit events.

All writes require approval in this initial policy. Destructive commands are absent
from the command model. The default in-memory checkpointer and approval adapter are for
tests and development; a deployed multi-process runtime must supply durable adapters.
