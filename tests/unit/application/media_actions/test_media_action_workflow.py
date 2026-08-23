from uuid import UUID

import pytest
from tests.factories import (
    FixedClock,
    make_authenticated_corpus_access_scope,
    make_guest_corpus_access_scope,
)

from cinegraph.adapters.approval import InMemoryApprovalRepository
from cinegraph.adapters.workflow.langgraph.media_action_graph import (
    MediaActionGraphWorkflow,
)
from cinegraph.application.models.media_action import (
    ApprovalDecision,
    MediaActionResult,
)
from cinegraph.application.service.media_action_service import MediaActionService
from cinegraph.common.error_messages import MediaActionErrorMessages
from cinegraph.domain.enums.enum import (
    ApprovalStatus,
    MediaActionAuditStage,
    MediaCommandKind,
    PrincipalKind,
)
from cinegraph.domain.models.identity import SessionPrincipal
from cinegraph.domain.models.media_action import MediaCommand

PROFILE_ID = UUID(int=101)
USER_ID = UUID(int=102)
CONNECTION_ID = UUID(int=103)
EPISODE_ID = UUID(int=104)


class RecordingProvider:
    def __init__(self) -> None:
        self.revision = "mock-connection-v1"
        self.executions = []
        self.watched: set[UUID] = set()
        self.results_by_key: dict[str, MediaActionResult] = {}

    def connection_revision(self, provider_connection_id: UUID) -> str:
        assert provider_connection_id == CONNECTION_ID
        return self.revision

    def execute(self, command: MediaCommand) -> MediaActionResult:
        self.executions.append(command)
        existing = self.results_by_key.get(command.idempotency_key)
        if existing is not None:
            return MediaActionResult(
                command_id=command.command_id,
                external_reference=existing.external_reference,
                provider_state_revision=existing.provider_state_revision,
                idempotent_replay=True,
            )
        self.watched.add(command.episode_ids[0])
        result = MediaActionResult(
            command_id=command.command_id,
            external_reference="mock-action-1",
            provider_state_revision="mock-state-v2",
        )
        self.results_by_key[command.idempotency_key] = result
        return result

    def verify(self, command: MediaCommand, result: MediaActionResult) -> bool:
        del result
        return command.episode_ids[0] in self.watched


class RecordingAuditSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


class SequenceIdentifiers:
    def __init__(self) -> None:
        self.value = 200

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(int=self.value)


def authenticated_principal() -> SessionPrincipal:
    return SessionPrincipal(
        kind=PrincipalKind.AUTHENTICATED,
        profile_id=PROFILE_ID,
        user_id=USER_ID,
        corpus_access_scope=make_authenticated_corpus_access_scope(),
    )


def guest_principal() -> SessionPrincipal:
    return SessionPrincipal(
        kind=PrincipalKind.GUEST,
        profile_id=PROFILE_ID,
        user_id=None,
        corpus_access_scope=make_guest_corpus_access_scope(),
    )


def command(**changes) -> MediaCommand:
    values = {
        "command_id": UUID(int=301),
        "kind": MediaCommandKind.MARK_WATCHED,
        "profile_id": PROFILE_ID,
        "provider_connection_id": CONNECTION_ID,
        "provider_owner_user_id": USER_ID,
        "provider_connection_revision": "mock-connection-v1",
        "idempotency_key": "mark-watched-profile-101-episode-104",
        "episode_ids": (EPISODE_ID,),
    }
    values.update(changes)
    return MediaCommand(**values)


def make_service(provider=None, audit=None):
    provider = provider or RecordingProvider()
    audit = audit or RecordingAuditSink()
    service = MediaActionService(
        InMemoryApprovalRepository(),
        provider,
        audit,
        FixedClock(),
        identifier_factory=SequenceIdentifiers(),
    )
    return service, provider, audit


def test_workflow_interrupts_then_executes_and_verifies_exact_approval() -> None:
    service, provider, audit = make_service()
    workflow = MediaActionGraphWorkflow(service)
    thread_id = UUID(int=401)
    action = command()

    pending = workflow.start(authenticated_principal(), action, thread_id)

    assert pending.status is ApprovalStatus.PENDING
    assert pending.preview == f"Mark episode {EPISODE_ID} watched."
    assert provider.executions == []

    completed = workflow.resume(
        thread_id,
        ApprovalDecision(
            approval_id=pending.approval_id,
            command_sha256=pending.command_sha256,
            approved=True,
        ),
    )

    assert completed.status is ApprovalStatus.VERIFIED
    assert completed.result is not None
    assert len(provider.executions) == 1
    assert [event.stage for event in audit.events] == [
        MediaActionAuditStage.PROPOSED,
        MediaActionAuditStage.APPROVED,
        MediaActionAuditStage.EXECUTED,
        MediaActionAuditStage.VERIFIED,
    ]


def test_rejects_parameter_tampering_and_provider_revision_changes() -> None:
    service, provider, _ = make_service()
    workflow = MediaActionGraphWorkflow(service)
    thread_id = UUID(int=402)
    pending = workflow.start(authenticated_principal(), command(), thread_id)

    with pytest.raises(
        ValueError,
        match=MediaActionErrorMessages.APPROVAL_COMMAND_MISMATCH,
    ):
        workflow.resume(
            thread_id,
            ApprovalDecision(pending.approval_id, "0" * 64, True),
        )

    service, provider, _ = make_service()
    workflow = MediaActionGraphWorkflow(service)
    thread_id = UUID(int=403)
    pending = workflow.start(authenticated_principal(), command(), thread_id)
    provider.revision = "mock-connection-v2"

    with pytest.raises(
        ValueError,
        match=MediaActionErrorMessages.PROVIDER_CONNECTION_CHANGED,
    ):
        workflow.resume(
            thread_id,
            ApprovalDecision(
                pending.approval_id,
                pending.command_sha256,
                True,
            ),
        )


def test_guest_and_cross_profile_commands_fail_before_approval() -> None:
    service, _, audit = make_service()

    with pytest.raises(
        PermissionError,
        match=MediaActionErrorMessages.AUTHENTICATED_PRINCIPAL_REQUIRED,
    ):
        service.propose(guest_principal(), command())

    with pytest.raises(
        PermissionError,
        match=MediaActionErrorMessages.PRINCIPAL_MUST_OWN_PROFILE,
    ):
        service.propose(
            authenticated_principal(),
            command(profile_id=UUID(int=999)),
        )

    assert audit.events == []


def test_idempotency_key_cannot_be_rebound_and_verified_replay_is_a_noop() -> None:
    service, provider, _ = make_service()
    workflow = MediaActionGraphWorkflow(service)
    first_thread = UUID(int=404)
    action = command()
    pending = workflow.start(authenticated_principal(), action, first_thread)
    completed = workflow.resume(
        first_thread,
        ApprovalDecision(pending.approval_id, pending.command_sha256, True),
    )

    replay = workflow.start(authenticated_principal(), action, UUID(int=405))

    assert completed.status is ApprovalStatus.VERIFIED
    assert replay.status is ApprovalStatus.VERIFIED
    assert len(provider.executions) == 1

    with pytest.raises(
        ValueError,
        match=MediaActionErrorMessages.IDEMPOTENCY_KEY_REUSED,
    ):
        service.propose(
            authenticated_principal(),
            command(command_id=UUID(int=999)),
        )


def test_rejected_command_never_calls_provider() -> None:
    service, provider, audit = make_service()
    workflow = MediaActionGraphWorkflow(service)
    thread_id = UUID(int=406)
    pending = workflow.start(authenticated_principal(), command(), thread_id)

    rejected = workflow.resume(
        thread_id,
        ApprovalDecision(pending.approval_id, pending.command_sha256, False),
    )

    assert rejected.status is ApprovalStatus.REJECTED
    assert rejected.result is None
    assert provider.executions == []
    assert audit.events[-1].stage is MediaActionAuditStage.REJECTED
