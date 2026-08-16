from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cinegraph.domain.enums.enum import ApprovalStatus, MediaActionAuditStage


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    approval_id: UUID
    command_sha256: str
    approved: bool


@dataclass(frozen=True, slots=True)
class MediaActionResult:
    command_id: UUID
    external_reference: str
    provider_state_revision: str
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class MediaActionAuditEvent:
    occurred_at: datetime
    approval_id: UUID
    command_id: UUID
    command_sha256: str
    principal_user_id: UUID
    profile_id: UUID
    provider_connection_id: UUID
    stage: MediaActionAuditStage
    status: ApprovalStatus


@dataclass(frozen=True, slots=True)
class MediaActionWorkflowOutcome:
    approval_id: UUID
    command_sha256: str
    status: ApprovalStatus
    preview: str
    result: MediaActionResult | None
