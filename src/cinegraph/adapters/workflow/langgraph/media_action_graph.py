from typing import TypedDict
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from cinegraph.application.models.media_action import (
    ApprovalDecision,
    MediaActionResult,
    MediaActionWorkflowOutcome,
)
from cinegraph.application.service.media_action_service import MediaActionService
from cinegraph.common.error_messages import MediaActionErrorMessages
from cinegraph.domain.enums.enum import (
    ApprovalStatus,
    CorpusAccessMode,
    MediaCommandKind,
    PrincipalKind,
)
from cinegraph.domain.models.access import CorpusAccessScope, CorpusSeasonAccess
from cinegraph.domain.models.identity import SessionPrincipal
from cinegraph.domain.models.media_action import ApprovalRequest, MediaCommand


class MediaActionGraphState(TypedDict):
    principal: dict[str, object]
    media_command: dict[str, object]
    approval_id: str | None
    result: dict[str, object] | None


class MediaActionGraphWorkflow:
    def __init__(
        self,
        service: MediaActionService,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self._service = service
        self._graph = self._build_graph().compile(
            checkpointer=checkpointer or InMemorySaver()
        )

    def start(
        self,
        principal: SessionPrincipal,
        command: MediaCommand,
        thread_id: UUID,
    ) -> MediaActionWorkflowOutcome:
        state = self._graph.invoke(
            MediaActionGraphState(
                principal=self._serialize_principal(principal),
                media_command=self._serialize_command(command),
                approval_id=None,
                result=None,
            ),
            self._config(thread_id),
        )
        return self._outcome(state)

    def resume(
        self,
        thread_id: UUID,
        decision: ApprovalDecision,
    ) -> MediaActionWorkflowOutcome:
        state = self._graph.invoke(
            Command(
                resume={
                    "approval_id": str(decision.approval_id),
                    "command_sha256": decision.command_sha256,
                    "approved": decision.approved,
                }
            ),
            self._config(thread_id),
        )
        return self._outcome(state)

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(MediaActionGraphState)
        graph.add_node("propose", self._propose)
        graph.add_node("approval", self._await_approval)
        graph.add_node("execute", self._execute)
        graph.add_edge(START, "propose")
        graph.add_conditional_edges(
            "propose",
            self._route_after_proposal,
            {"approval": "approval", "execute": "execute", "end": END},
        )
        graph.add_conditional_edges(
            "approval",
            self._route_after_approval,
            {"execute": "execute", "end": END},
        )
        graph.add_edge("execute", END)
        return graph

    def _route_after_proposal(self, state: MediaActionGraphState) -> str:
        approval = self._approval_from_state(state)
        if approval.status is ApprovalStatus.PENDING:
            return "approval"
        if approval.status is ApprovalStatus.APPROVED:
            return "execute"
        return "end"

    def _route_after_approval(self, state: MediaActionGraphState) -> str:
        approval = self._approval_from_state(state)
        return "execute" if approval.status is ApprovalStatus.APPROVED else "end"

    def _propose(self, state: MediaActionGraphState) -> dict[str, object]:
        approval = self._service.propose(
            self._deserialize_principal(state["principal"]),
            self._deserialize_command(state["media_command"]),
        )
        return {"approval_id": str(approval.approval_id)}

    def _await_approval(self, state: MediaActionGraphState) -> dict[str, object]:
        approval = self._approval_from_state(state)
        response = interrupt(
            {
                "approval_id": str(approval.approval_id),
                "command_sha256": approval.command_sha256,
                "preview": approval.preview,
                "expires_at": approval.expires_at.isoformat(),
            }
        )
        decision = ApprovalDecision(
            approval_id=UUID(response["approval_id"]),
            command_sha256=response["command_sha256"],
            approved=response["approved"],
        )
        updated = self._service.decide(
            self._deserialize_principal(state["principal"]),
            self._deserialize_command(state["media_command"]),
            decision,
        )
        return {"approval_id": str(updated.approval_id)}

    def _execute(self, state: MediaActionGraphState) -> dict[str, object]:
        approval = self._approval_from_state(state)
        verified, result = self._service.execute_approved(
            self._deserialize_principal(state["principal"]),
            self._deserialize_command(state["media_command"]),
            approval.approval_id,
        )
        return {
            "approval_id": str(verified.approval_id),
            "result": self._serialize_result(result),
        }

    @staticmethod
    def _config(thread_id: UUID) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": str(thread_id)}}

    def _outcome(self, state: MediaActionGraphState) -> MediaActionWorkflowOutcome:
        approval = self._approval_from_state(state)
        result = state.get("result")
        return MediaActionWorkflowOutcome(
            approval_id=approval.approval_id,
            command_sha256=approval.command_sha256,
            status=approval.status,
            preview=approval.preview,
            result=self._deserialize_result(result) if result is not None else None,
        )

    def _approval_from_state(
        self, state: MediaActionGraphState
    ) -> ApprovalRequest:
        approval_id = state.get("approval_id")
        if approval_id is None:
            raise RuntimeError(MediaActionErrorMessages.WORKFLOW_RESULT_MUST_EXIST)
        return self._service.get_approval(UUID(approval_id))

    @staticmethod
    def _serialize_principal(principal: SessionPrincipal) -> dict[str, object]:
        scope = principal.corpus_access_scope
        return {
            "kind": principal.kind.value,
            "profile_id": str(principal.profile_id),
            "user_id": str(principal.user_id) if principal.user_id is not None else None,
            "corpus_access_scope": {
                "mode": scope.mode.value,
                "revision": scope.revision,
                "unrestricted": scope.unrestricted,
                "allowed_seasons": [
                    {
                        "series_id": str(season.series_id),
                        "season_number": season.season_number,
                    }
                    for season in sorted(scope.allowed_seasons)
                ],
            },
        }

    @staticmethod
    def _deserialize_principal(snapshot: dict[str, object]) -> SessionPrincipal:
        scope_snapshot = snapshot["corpus_access_scope"]
        if not isinstance(scope_snapshot, dict):
            raise ValueError(MediaActionErrorMessages.WORKFLOW_RESULT_MUST_EXIST)
        allowed_seasons = scope_snapshot["allowed_seasons"]
        if not isinstance(allowed_seasons, list):
            raise ValueError(MediaActionErrorMessages.WORKFLOW_RESULT_MUST_EXIST)
        scope = CorpusAccessScope(
            mode=CorpusAccessMode(scope_snapshot["mode"]),
            revision=str(scope_snapshot["revision"]),
            unrestricted=bool(scope_snapshot["unrestricted"]),
            allowed_seasons=frozenset(
                CorpusSeasonAccess(
                    series_id=UUID(season["series_id"]),
                    season_number=season["season_number"],
                )
                for season in allowed_seasons
                if isinstance(season, dict)
            ),
        )
        user_id = snapshot["user_id"]
        return SessionPrincipal(
            kind=PrincipalKind(snapshot["kind"]),
            profile_id=UUID(snapshot["profile_id"]),
            user_id=UUID(user_id) if isinstance(user_id, str) else None,
            corpus_access_scope=scope,
        )

    @staticmethod
    def _serialize_command(command: MediaCommand) -> dict[str, object]:
        return {
            "command_id": str(command.command_id),
            "kind": command.kind.value,
            "profile_id": str(command.profile_id),
            "provider_connection_id": str(command.provider_connection_id),
            "provider_owner_user_id": str(command.provider_owner_user_id),
            "provider_connection_revision": command.provider_connection_revision,
            "idempotency_key": command.idempotency_key,
            "episode_ids": [str(episode_id) for episode_id in command.episode_ids],
            "playlist_name": command.playlist_name,
            "favorite": command.favorite,
        }

    @staticmethod
    def _deserialize_command(snapshot: dict[str, object]) -> MediaCommand:
        episode_ids = snapshot["episode_ids"]
        if not isinstance(episode_ids, list):
            raise ValueError(MediaActionErrorMessages.WORKFLOW_RESULT_MUST_EXIST)
        return MediaCommand(
            command_id=UUID(snapshot["command_id"]),
            kind=MediaCommandKind(snapshot["kind"]),
            profile_id=UUID(snapshot["profile_id"]),
            provider_connection_id=UUID(snapshot["provider_connection_id"]),
            provider_owner_user_id=UUID(snapshot["provider_owner_user_id"]),
            provider_connection_revision=str(snapshot["provider_connection_revision"]),
            idempotency_key=str(snapshot["idempotency_key"]),
            episode_ids=tuple(UUID(episode_id) for episode_id in episode_ids),
            playlist_name=(
                str(snapshot["playlist_name"])
                if snapshot["playlist_name"] is not None
                else None
            ),
            favorite=(
                bool(snapshot["favorite"])
                if snapshot["favorite"] is not None
                else None
            ),
        )

    @staticmethod
    def _serialize_result(result: MediaActionResult) -> dict[str, object]:
        return {
            "command_id": str(result.command_id),
            "external_reference": result.external_reference,
            "provider_state_revision": result.provider_state_revision,
            "idempotent_replay": result.idempotent_replay,
        }

    @staticmethod
    def _deserialize_result(snapshot: dict[str, object]) -> MediaActionResult:
        return MediaActionResult(
            command_id=UUID(snapshot["command_id"]),
            external_reference=str(snapshot["external_reference"]),
            provider_state_revision=str(snapshot["provider_state_revision"]),
            idempotent_replay=bool(snapshot["idempotent_replay"]),
        )
