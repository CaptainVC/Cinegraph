from dataclasses import replace
from uuid import UUID

import pytest
from tests.factories import (
    make_authenticated_corpus_access_scope,
    make_episode_ref,
    make_guest_corpus_access_scope,
)

from cinegraph.adapters.repository.in_memory.in_memory_conversation_thread_binding_repository import (
    InMemoryConversationThreadBindingRepository,
)
from cinegraph.application.exceptions.errors import CorpusAccessDeniedError
from cinegraph.application.models.conversation import ConversationalSeriesChatQuery
from cinegraph.application.models.series_agent_result import SeriesAgentResult
from cinegraph.application.service.conversational_series_chat_service import (
    ConversationalSeriesChatService,
)
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.watch_state import ProfileWatchState, SeriesWatchState


class WatchRepository:
    def __init__(
        self, state: ProfileWatchState | None, *next_states: ProfileWatchState | None
    ) -> None:
        self.states = iter((state, *next_states))
        self.calls = 0

    def get(self, profile_id: UUID):
        self.calls += 1
        return next(self.states)


class Agent:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, question, context, thread_id):
        self.calls += 1
        return SeriesAgentResult(None, True)


def state(version: int = 1, profile_id: UUID = UUID(int=42)) -> ProfileWatchState:
    return ProfileWatchState(profile_id=profile_id, profile_name="Alex", version=version)


def make_query(scope, episode):
    profile_id = UUID(int=42)
    return ConversationalSeriesChatQuery(
        UUID(int=43), profile_id, scope.revision, "Question", episode.series_id, (episode,), scope
    )


def test_guest_later_season_is_rejected_before_watch_repository_or_agent() -> None:
    scope = make_guest_corpus_access_scope()
    repository = WatchRepository(None)
    agent = Agent()
    service = ConversationalSeriesChatService(
        repository, InMemoryConversationThreadBindingRepository(), agent
    )
    with pytest.raises(CorpusAccessDeniedError):
        service.execute(make_query(scope, make_episode_ref(season_number=3)))
    assert repository.calls == 0
    assert agent.calls == 0


def test_authenticated_later_season_reaches_agent_with_trusted_context() -> None:
    episode = make_episode_ref(season_number=3)
    scope = make_authenticated_corpus_access_scope()
    repository = WatchRepository(ProfileWatchState(profile_id=UUID(int=42), profile_name="Alex"))
    agent = Agent()
    service = ConversationalSeriesChatService(
        repository, InMemoryConversationThreadBindingRepository(), agent
    )
    result = service.execute(make_query(scope, episode))
    assert result.is_safe_refusal is True
    assert agent.calls == 1


def test_same_binding_reuses_thread_and_changed_watch_version_fails_before_agent() -> None:
    episode = make_episode_ref()
    scope = make_authenticated_corpus_access_scope()
    repository = WatchRepository(state(1), state(2))
    agent = Agent()
    service = ConversationalSeriesChatService(
        repository, InMemoryConversationThreadBindingRepository(), agent
    )
    service.execute(make_query(scope, episode))
    with pytest.raises(ValueError):
        service.execute(make_query(scope, episode))
    assert agent.calls == 1


def test_repository_profile_mismatch_fails_before_agent() -> None:
    episode = make_episode_ref()
    scope = make_authenticated_corpus_access_scope()
    repository = WatchRepository(state(profile_id=UUID(int=99)))
    agent = Agent()
    service = ConversationalSeriesChatService(
        repository, InMemoryConversationThreadBindingRepository(), agent
    )
    with pytest.raises(ValueError):
        service.execute(make_query(scope, episode))
    assert agent.calls == 0


def test_thread_binding_rejects_changed_candidate_set_or_spoiler_policy() -> None:
    episode = make_episode_ref()
    second = replace(episode, episode_id=UUID(int=9_001))
    scope = make_authenticated_corpus_access_scope()
    profile_id = UUID(int=42)
    relaxed = ProfileWatchState(
        profile_id=profile_id,
        profile_name="Alex",
        spoiler_mode=SpoilerMode.RELAXED,
    )
    strict = ProfileWatchState(
        profile_id=profile_id,
        profile_name="Alex",
        spoiler_mode=SpoilerMode.STRICT,
        series_watch_states=(
            SeriesWatchState(
                series_id=episode.series_id,
                manually_allowed_episodes=frozenset({episode}),
            ),
        ),
    )

    for changed in (
        replace(
            make_query(scope, episode),
            candidate_episodes=(episode, second),
            profile_watch_state=relaxed,
        ),
        replace(make_query(scope, episode), profile_watch_state=strict),
    ):
        agent = Agent()
        service = ConversationalSeriesChatService(
            WatchRepository(None), InMemoryConversationThreadBindingRepository(), agent
        )
        service.execute(replace(make_query(scope, episode), profile_watch_state=relaxed))
        with pytest.raises(ValueError):
            service.execute(changed)
        assert agent.calls == 1
