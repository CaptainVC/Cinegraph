from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from cinegraph.common.error_messages import MediaActionErrorMessages
from cinegraph.domain.enums.enum import ApprovalStatus
from cinegraph.domain.exceptions.errors import InvalidModelError


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    approval_id: UUID
    command_id: UUID
    command_sha256: str
    idempotency_key: str
    principal_user_id: UUID
    profile_id: UUID
    provider_connection_id: UUID
    provider_connection_revision: str
    preview: str
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    executed_at: datetime | None = None
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise InvalidModelError(MediaActionErrorMessages.APPROVAL_TRANSITION_INVALID)
        if self.expires_at <= self.created_at:
            raise InvalidModelError(MediaActionErrorMessages.APPROVAL_TRANSITION_INVALID)
        if len(self.command_sha256) != 64:
            raise InvalidModelError(
                MediaActionErrorMessages.APPROVAL_COMMAND_MISMATCH
            )

    def decide(self, approved: bool, occurred_at: datetime) -> "ApprovalRequest":
        if self.status is not ApprovalStatus.PENDING or occurred_at > self.expires_at:
            raise InvalidModelError(MediaActionErrorMessages.APPROVAL_TRANSITION_INVALID)
        return replace(
            self,
            status=ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
            decided_at=occurred_at,
        )

    def mark_executed(self, occurred_at: datetime) -> "ApprovalRequest":
        if self.status is not ApprovalStatus.APPROVED:
            raise InvalidModelError(MediaActionErrorMessages.APPROVAL_TRANSITION_INVALID)
        return replace(
            self,
            status=ApprovalStatus.EXECUTED,
            executed_at=occurred_at,
        )

    def mark_verified(self, occurred_at: datetime) -> "ApprovalRequest":
        if self.status is not ApprovalStatus.EXECUTED:
            raise InvalidModelError(MediaActionErrorMessages.APPROVAL_TRANSITION_INVALID)
        return replace(
            self,
            status=ApprovalStatus.VERIFIED,
            verified_at=occurred_at,
        )
