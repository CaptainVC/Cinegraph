from collections.abc import Callable
from uuid import UUID

from cinegraph.application.models.media_action import (
    ApprovalDecision,
    MediaActionAuditEvent,
    MediaActionResult,
)
from cinegraph.application.policy.tool_authorization_policy import (
    ToolAuthorizationPolicy,
)
from cinegraph.common.error_messages import MediaActionErrorMessages
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config import (
    DEFAULT_MEDIA_ACTION_CONFIGURATION,
    MediaActionConfiguration,
)
from cinegraph.domain.enums.enum import ApprovalStatus, MediaActionAuditStage
from cinegraph.domain.models.identity import SessionPrincipal
from cinegraph.domain.models.media_action import ApprovalRequest, MediaCommand
from cinegraph.ports.approval import ApprovalRepository
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.media import MediaProvider
from cinegraph.ports.observability import MediaActionAuditSink


class MediaActionService:
    def __init__(
        self,
        approvals: ApprovalRepository,
        provider: MediaProvider,
        audit_sink: MediaActionAuditSink,
        clock: Clock,
        authorization: ToolAuthorizationPolicy | None = None,
        configuration: MediaActionConfiguration = DEFAULT_MEDIA_ACTION_CONFIGURATION,
        identifier_factory: Callable[[], UUID] = IdentifierGenerator.new_id,
    ) -> None:
        self._approvals = approvals
        self._provider = provider
        self._audit_sink = audit_sink
        self._clock = clock
        self._authorization = authorization or ToolAuthorizationPolicy(configuration)
        self._configuration = configuration
        self._identifier_factory = identifier_factory

    def propose(
        self,
        principal: SessionPrincipal,
        command: MediaCommand,
    ) -> ApprovalRequest:
        self._authorization.require_authorized(principal, command)
        self._require_connection_revision(command)
        existing = self._approvals.find_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            if existing.command_sha256 != command.parameter_sha256:
                raise ValueError(MediaActionErrorMessages.IDEMPOTENCY_KEY_REUSED)
            return existing
        assert principal.user_id is not None
        now = self._clock.now_utc()
        approval = ApprovalRequest(
            approval_id=self._identifier_factory(),
            command_id=command.command_id,
            command_sha256=command.parameter_sha256,
            idempotency_key=command.idempotency_key,
            principal_user_id=principal.user_id,
            profile_id=principal.profile_id,
            provider_connection_id=command.provider_connection_id,
            provider_connection_revision=command.provider_connection_revision,
            preview=command.preview,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=now + self._configuration.approval_ttl,
        )
        self._approvals.add(approval)
        self._emit(approval, MediaActionAuditStage.PROPOSED)
        return approval

    def decide(
        self,
        principal: SessionPrincipal,
        command: MediaCommand,
        decision: ApprovalDecision,
    ) -> ApprovalRequest:
        self._authorization.require_authorized(principal, command)
        self._require_connection_revision(command)
        approval = self._require_exact_approval(command, decision)
        now = self._clock.now_utc()
        if now > approval.expires_at:
            raise ValueError(MediaActionErrorMessages.APPROVAL_EXPIRED)
        updated = approval.decide(decision.approved, now)
        self._approvals.save(updated, expected_status=ApprovalStatus.PENDING)
        self._emit(
            updated,
            MediaActionAuditStage.APPROVED
            if decision.approved
            else MediaActionAuditStage.REJECTED,
        )
        return updated

    def execute_approved(
        self,
        principal: SessionPrincipal,
        command: MediaCommand,
        approval_id: UUID,
    ) -> tuple[ApprovalRequest, MediaActionResult]:
        self._authorization.require_authorized(principal, command)
        self._require_connection_revision(command)
        approval = self._approvals.get(approval_id)
        if approval is None:
            raise ValueError(MediaActionErrorMessages.APPROVAL_NOT_FOUND)
        if (
            approval.command_sha256 != command.parameter_sha256
            or approval.status is not ApprovalStatus.APPROVED
        ):
            raise ValueError(MediaActionErrorMessages.APPROVAL_COMMAND_MISMATCH)
        result = self._provider.execute(command)
        executed = approval.mark_executed(self._clock.now_utc())
        self._approvals.save(executed, expected_status=ApprovalStatus.APPROVED)
        self._emit(executed, MediaActionAuditStage.EXECUTED)
        if not self._provider.verify(command, result):
            raise RuntimeError(MediaActionErrorMessages.PROVIDER_VERIFICATION_FAILED)
        verified = executed.mark_verified(self._clock.now_utc())
        self._approvals.save(verified, expected_status=ApprovalStatus.EXECUTED)
        self._emit(verified, MediaActionAuditStage.VERIFIED)
        return verified, result

    def get_approval(self, approval_id: UUID) -> ApprovalRequest:
        approval = self._approvals.get(approval_id)
        if approval is None:
            raise ValueError(MediaActionErrorMessages.APPROVAL_NOT_FOUND)
        return approval

    def _require_exact_approval(
        self,
        command: MediaCommand,
        decision: ApprovalDecision,
    ) -> ApprovalRequest:
        approval = self._approvals.get(decision.approval_id)
        if approval is None:
            raise ValueError(MediaActionErrorMessages.APPROVAL_NOT_FOUND)
        if (
            decision.command_sha256 != command.parameter_sha256
            or approval.command_sha256 != command.parameter_sha256
            or approval.command_id != command.command_id
            or approval.profile_id != command.profile_id
            or approval.provider_connection_id != command.provider_connection_id
        ):
            raise ValueError(MediaActionErrorMessages.APPROVAL_COMMAND_MISMATCH)
        return approval

    def _require_connection_revision(self, command: MediaCommand) -> None:
        if (
            self._provider.connection_revision(command.provider_connection_id)
            != command.provider_connection_revision
        ):
            raise ValueError(MediaActionErrorMessages.PROVIDER_CONNECTION_CHANGED)

    def _emit(
        self,
        approval: ApprovalRequest,
        stage: MediaActionAuditStage,
    ) -> None:
        self._audit_sink.emit(
            MediaActionAuditEvent(
                occurred_at=self._clock.now_utc(),
                approval_id=approval.approval_id,
                command_id=approval.command_id,
                command_sha256=approval.command_sha256,
                principal_user_id=approval.principal_user_id,
                profile_id=approval.profile_id,
                provider_connection_id=approval.provider_connection_id,
                stage=stage,
                status=approval.status,
            )
        )
