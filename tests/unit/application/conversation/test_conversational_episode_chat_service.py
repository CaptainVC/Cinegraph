from dataclasses import replace
from uuid import UUID

import pytest

from cinegraph.adapters.repository.in_memory.in_memory_conversation_thread_binding_repository import (
    InMemoryConversationThreadBindingRepository,
)
from cinegraph.application.exceptions.errors import (
    ConversationThreadProfileMismatchError,
    ConversationThreadScopeMismatchError,
    ConversationThreadWatchStateMismatchError,
    CorpusAccessDeniedError,
)
from cinegraph.application.models.agent_context import AgentRuntimeContext
from cinegraph.application.models.conversation import ConversationalEpisodeChatQuery
from cinegraph.application.service.conversational_episode_chat_service import (
    ConversationalEpisodeChatService,
)
from cinegraph.domain.enums.enum import CorpusAccessMode
from cinegraph.domain.models.access import CorpusAccessScope, CorpusSeasonAccess
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState
from tests.factories import (
    make_authenticated_corpus_access_scope,
    make_episode_ref,
    make_guest_corpus_access_scope,
)

PROFILE_ID = UUID("00000000-0000-0000-0000-000000000801")
OTHER_PROFILE_ID = UUID("00000000-0000-0000-0000-000000000802")
THREAD_ID = UUID("00000000-0000-0000-0000-000000000803")
SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000804")


class SequenceWatchProgressRepository:
    # Return a configured state on each successive turn.
    def __init__(self, states: tuple[ProfileWatchState | None, ...]) -> None:
        self._states = iter(states)
        self.requested_profile_ids: list[UUID] = []

    # Reload the next state for the requested profile.
    def get(self, profile_id: UUID) -> ProfileWatchState | None:
        self.requested_profile_ids.append(profile_id)
        return next(self._states)


class RecordingAgent:
    # Initialize invocation recording for boundary assertions.
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRuntimeContext, UUID]] = []

    # Record the invocation and return a small state-shaped result.
    def invoke(
        self,
        question: str,
        context: AgentRuntimeContext,
        thread_id: UUID,
    ) -> dict[str, object]:
        self.calls.append((question, context, thread_id))
        return {"question": question}


def make_state(*, profile_id: UUID = PROFILE_ID, version: int = 3) -> ProfileWatchState:
    # Build the minimal valid persisted state used by conversation tests.
    return ProfileWatchState(profile_id=profile_id, profile_name="Alex", version=version)


def make_query(
    *,
    scope: str = "scope-1",
    episode=None,
    corpus_access_scope=None,
) -> ConversationalEpisodeChatQuery:
    # Build a query with a stable episode and thread boundary.
    return ConversationalEpisodeChatQuery(
        thread_id=THREAD_ID,
        profile_id=PROFILE_ID,
        permission_scope_revision=scope,
        question="What happened?",
        episode=episode or make_episode_ref(),
        summary_source_document_id=SOURCE_DOCUMENT_ID,
        corpus_access_scope=(
            corpus_access_scope
            or make_authenticated_corpus_access_scope(revision=scope)
        ),
    )


def make_service(
    states: tuple[ProfileWatchState | None, ...],
    agent: RecordingAgent,
) -> ConversationalEpisodeChatService:
    # Compose the service with ports and the real in-memory binding adapter.
    return ConversationalEpisodeChatService(
        SequenceWatchProgressRepository(states),
        InMemoryConversationThreadBindingRepository(),
        agent,
    )


def test_same_profile_and_version_reuses_thread() -> None:
    # Accept repeated turns while the exact binding remains unchanged.
    agent = RecordingAgent()
    service = make_service((make_state(), make_state()), agent)

    service.execute(make_query())
    service.execute(make_query())

    assert len(agent.calls) == 2


def test_profile_mismatch_rejects_before_agent() -> None:
    # Reject a changed profile before invoking the model boundary.
    agent = RecordingAgent()
    service = make_service(
        (make_state(), make_state(profile_id=OTHER_PROFILE_ID)),
        agent,
    )

    service.execute(make_query())
    with pytest.raises(ConversationThreadProfileMismatchError):
        service.execute(replace(make_query(), profile_id=OTHER_PROFILE_ID))

    assert len(agent.calls) == 1


def test_watch_state_version_mismatch_after_reload_rejects_before_agent() -> None:
    # Reject a changed freshly-loaded watch-state version before the agent call.
    agent = RecordingAgent()
    service = make_service((make_state(version=3), make_state(version=4)), agent)

    service.execute(make_query())
    with pytest.raises(ConversationThreadWatchStateMismatchError):
        service.execute(make_query())

    assert len(agent.calls) == 1


def test_permission_scope_mismatch_rejects_before_agent() -> None:
    # Reject a caller scope revision that differs from the original thread binding.
    agent = RecordingAgent()
    service = make_service((make_state(), make_state()), agent)

    service.execute(make_query(scope="scope-1"))
    with pytest.raises(ConversationThreadScopeMismatchError):
        service.execute(make_query(scope="scope-2"))

    assert len(agent.calls) == 1


def test_changed_grants_with_same_revision_reject_before_agent() -> None:
    agent = RecordingAgent()
    service = make_service((make_state(), make_state()), agent)
    first_scope = make_authenticated_corpus_access_scope(revision="scope-1")
    episode = make_episode_ref()
    narrowed_scope = CorpusAccessScope(
        mode=CorpusAccessMode.AUTHENTICATED,
        revision="scope-1",
        allowed_seasons=frozenset(
            {
                CorpusSeasonAccess(
                    series_id=episode.series_id,
                    season_number=episode.position.season_number,
                )
            }
        ),
    )

    service.execute(make_query(corpus_access_scope=first_scope))
    with pytest.raises(ConversationThreadScopeMismatchError):
        service.execute(make_query(corpus_access_scope=narrowed_scope))

    assert len(agent.calls) == 1


def test_missing_watch_state_uses_baseline_and_strict_none_context() -> None:
    # Bind missing state at version zero and pass None to the runtime context.
    agent = RecordingAgent()
    service = make_service((None,), agent)

    service.execute(make_query())

    assert agent.calls[0][1]["profile_watch_state"] is None
    assert agent.calls[0][1]["corpus_access_scope"] == (
        make_authenticated_corpus_access_scope(revision="scope-1")
    )


def test_inconsistent_repository_profile_rejects_before_agent() -> None:
    # Reject a repository result whose identity contradicts the requested profile.
    agent = RecordingAgent()
    service = make_service((make_state(profile_id=OTHER_PROFILE_ID),), agent)

    with pytest.raises(ConversationThreadProfileMismatchError):
        service.execute(make_query())

    assert agent.calls == []


def test_guest_season_three_rejects_before_repository_or_agent_access() -> None:
    agent = RecordingAgent()
    watch_repository = SequenceWatchProgressRepository((make_state(),))
    service = ConversationalEpisodeChatService(
        watch_repository,
        InMemoryConversationThreadBindingRepository(),
        agent,
    )
    guest_scope = make_guest_corpus_access_scope()

    with pytest.raises(CorpusAccessDeniedError):
        service.execute(
            make_query(
                scope=guest_scope.revision,
                episode=make_episode_ref(season_number=3),
                corpus_access_scope=guest_scope,
            )
        )

    assert watch_repository.requested_profile_ids == []
    assert agent.calls == []
