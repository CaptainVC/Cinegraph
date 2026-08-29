from dataclasses import dataclass
from uuid import UUID

from cinegraph.common.error_messages import ConversationErrorMessages
from cinegraph.config.series_agent import DEFAULT_SERIES_AGENT_CONFIGURATION
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef
from cinegraph.domain.models.watch_state.profile_watch_state import ProfileWatchState


@dataclass(frozen=True, slots=True)
class ConversationThreadBinding:
    profile_id: UUID
    watch_state_version: int
    permission_scope_revision: str
    corpus_access_scope: CorpusAccessScope
    candidate_episode_ids: tuple[UUID, ...] = ()
    spoiler_mode: SpoilerMode = SpoilerMode.RELAXED
    safe_through_episode_id: UUID | None = None

    # Enforce the immutable thread binding's version and scope invariants.
    def __post_init__(self) -> None:
        if self.watch_state_version < 0:
            raise InvalidModelError(
                ConversationErrorMessages.BINDING_WATCH_STATE_VERSION_MUST_BE_NON_NEGATIVE
            )
        if (
            not self.permission_scope_revision
            or self.permission_scope_revision.strip() != self.permission_scope_revision
        ):
            raise InvalidModelError(
                ConversationErrorMessages.BINDING_PERMISSION_SCOPE_REVISION_MUST_BE_NONEMPTY
            )
        if self.permission_scope_revision != self.corpus_access_scope.revision:
            raise InvalidModelError(
                ConversationErrorMessages.BINDING_PERMISSION_SCOPE_REVISION_MUST_MATCH_ACCESS_SCOPE
            )
        if (
            not isinstance(self.candidate_episode_ids, tuple)
            or any(not isinstance(item, UUID) for item in self.candidate_episode_ids)
            or len(set(self.candidate_episode_ids)) != len(self.candidate_episode_ids)
        ):
            raise InvalidModelError(ConversationErrorMessages.BINDING_CANDIDATES_INVALID)
        if not isinstance(self.spoiler_mode, SpoilerMode):
            raise InvalidModelError(ConversationErrorMessages.BINDING_SPOILER_INVALID)
        if self.safe_through_episode_id is not None and not isinstance(
            self.safe_through_episode_id, UUID
        ):
            raise InvalidModelError(ConversationErrorMessages.BINDING_SPOILER_INVALID)


@dataclass(frozen=True, slots=True)
class ConversationalEpisodeChatQuery:
    thread_id: UUID
    profile_id: UUID
    permission_scope_revision: str
    question: str
    episode: EpisodeRef
    summary_source_document_id: UUID
    corpus_access_scope: CorpusAccessScope


@dataclass(frozen=True, slots=True)
class ConversationalSeriesChatQuery:
    thread_id: UUID
    profile_id: UUID
    permission_scope_revision: str
    question: str
    series_id: UUID
    candidate_episodes: tuple[EpisodeRef, ...]
    corpus_access_scope: CorpusAccessScope
    profile_watch_state: ProfileWatchState | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.thread_id, UUID) or not isinstance(self.profile_id, UUID):
            raise InvalidModelError(ConversationErrorMessages.SERIES_QUERY_IDENTITIES_MUST_BE_UUID)
        if (
            not isinstance(self.permission_scope_revision, str)
            or not self.permission_scope_revision
            or self.permission_scope_revision.strip() != self.permission_scope_revision
        ):
            raise InvalidModelError(
                ConversationErrorMessages.SERIES_QUERY_SCOPE_REVISION_MUST_BE_TRIMMED
            )
        if (
            not isinstance(self.question, str)
            or not self.question
            or self.question.strip() != self.question
            or len(self.question) > DEFAULT_SERIES_AGENT_CONFIGURATION.question_max_length
        ):
            raise InvalidModelError(ConversationErrorMessages.SERIES_QUERY_QUESTION_MUST_BE_BOUNDED)
        if (
            not isinstance(self.series_id, UUID)
            or not isinstance(self.candidate_episodes, tuple)
            or not self.candidate_episodes
            or not isinstance(self.corpus_access_scope, CorpusAccessScope)
        ):
            raise InvalidModelError(ConversationErrorMessages.SERIES_QUERY_CANDIDATES_MUST_BE_VALID)
        if self.permission_scope_revision != self.corpus_access_scope.revision:
            raise InvalidModelError(
                ConversationErrorMessages.BINDING_PERMISSION_SCOPE_REVISION_MUST_MATCH_ACCESS_SCOPE
            )
        if len(self.candidate_episodes) > DEFAULT_SERIES_AGENT_CONFIGURATION.max_candidate_episodes:
            raise InvalidModelError(ConversationErrorMessages.SERIES_QUERY_CANDIDATE_LIMIT_EXCEEDED)
        if any(
            not isinstance(item, EpisodeRef) or item.series_id != self.series_id
            for item in self.candidate_episodes
        ) or len({item.episode_id for item in self.candidate_episodes}) != len(
            self.candidate_episodes
        ):
            raise InvalidModelError(
                ConversationErrorMessages.SERIES_QUERY_CANDIDATES_MUST_SHARE_SERIES
            )
