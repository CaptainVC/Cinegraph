from typing import Any
from uuid import UUID

from langchain.agents.middleware import AgentMiddleware

from cinegraph.application.exceptions.errors import AgentRuntimeContextInvalidError
from cinegraph.application.models.series_agent_context import SeriesAgentRuntimeContext
from cinegraph.config.series_agent import (
    DEFAULT_SERIES_AGENT_CONFIGURATION,
    SeriesAgentConfiguration,
)
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodeRef, ProfileWatchState


class SeriesRuntimeContextIntegrityMiddleware(AgentMiddleware):
    """Reject malformed or over-broad trusted context before model/tool calls."""

    def before_agent(self, state: Any, runtime: Any) -> None:
        del state
        context = getattr(runtime, "context", None)
        if not isinstance(context, SeriesAgentRuntimeContext):
            raise AgentRuntimeContextInvalidError()
        if not isinstance(context.series_id, UUID) or not isinstance(
            context.corpus_access_scope, CorpusAccessScope
        ):
            raise AgentRuntimeContextInvalidError()
        episodes = context.candidate_episodes
        if (
            not isinstance(episodes, tuple)
            or not episodes
            or len(episodes) > self._configuration.max_candidate_episodes
        ):
            raise AgentRuntimeContextInvalidError()
        if any(
            not isinstance(item, EpisodeRef) or item.series_id != context.series_id
            for item in episodes
        ):
            raise AgentRuntimeContextInvalidError()
        if len({item.episode_id for item in episodes}) != len(
            episodes
        ) or not context.corpus_access_scope.allows_all(episodes):
            raise AgentRuntimeContextInvalidError()
        if context.profile_watch_state is not None and not isinstance(
            context.profile_watch_state, ProfileWatchState
        ):
            raise AgentRuntimeContextInvalidError()

    def __init__(
        self, configuration: SeriesAgentConfiguration = DEFAULT_SERIES_AGENT_CONFIGURATION
    ) -> None:
        self._configuration = configuration
