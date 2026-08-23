from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import WorkflowErrorMessages
from cinegraph.config.series_agent import DEFAULT_SERIES_AGENT_CONFIGURATION
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodeRef, ProfileWatchState


@dataclass(frozen=True, slots=True)
class SeriesAgentRuntimeContext:
    """Trusted invocation-only values; this object is never model-visible."""

    series_id: UUID
    candidate_episodes: tuple[EpisodeRef, ...]
    profile_watch_state: ProfileWatchState | None
    corpus_access_scope: CorpusAccessScope

    def __post_init__(self) -> None:
        if not isinstance(self.series_id, UUID) or not isinstance(
            self.corpus_access_scope, CorpusAccessScope
        ):
            raise InvalidModelError(WorkflowErrorMessages.AGENT_RUNTIME_CONTEXT_MUST_BE_VALID)
        if (
            not isinstance(self.candidate_episodes, tuple)
            or not self.candidate_episodes
            or len(self.candidate_episodes)
            > DEFAULT_SERIES_AGENT_CONFIGURATION.max_candidate_episodes
        ):
            raise InvalidModelError(WorkflowErrorMessages.AGENT_RUNTIME_CONTEXT_MUST_BE_VALID)
        if any(
            not isinstance(item, EpisodeRef) or item.series_id != self.series_id
            for item in self.candidate_episodes
        ):
            raise InvalidModelError(WorkflowErrorMessages.AGENT_RUNTIME_CONTEXT_MUST_BE_VALID)
        if len({item.episode_id for item in self.candidate_episodes}) != len(
            self.candidate_episodes
        ) or not self.corpus_access_scope.allows_all(self.candidate_episodes):
            raise InvalidModelError(WorkflowErrorMessages.AGENT_RUNTIME_CONTEXT_MUST_BE_VALID)
        if self.profile_watch_state is not None and not isinstance(
            self.profile_watch_state, ProfileWatchState
        ):
            raise InvalidModelError(WorkflowErrorMessages.AGENT_RUNTIME_CONTEXT_MUST_BE_VALID)


SeriesAgentContext = SeriesAgentRuntimeContext
